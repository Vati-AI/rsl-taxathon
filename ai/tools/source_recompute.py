"""SAF-T-style source-record intake and recompute engine (TAX-113, framework BP1).

The taxpayer supplies its own books in a small SAF-T-style structure (modelled on the
OECD Standard Audit File for Tax; this is a documented subset, not the OECD schema):

``header``            ``tin``, ``company_name``, ``currency`` (LSL), ``period_start``,
                      ``period_end``
``sales_summary``     per period and tax code (``STD``, ``ZERO``, ``EXEMPT``): ``net``, ``vat``
``supplier_invoices`` ``entry_id``, ``period``, ``supplier_ref``, ``invoice_number``,
                      ``invoice_date``, ``net``, ``vat``, ``tax_code`` (``STD``, ``IMPORT``,
                      ``NONE``), ``account``
``journals``          ``journal_id``, ``period``, ``lines`` of ``account``, ``debit``, ``credit``

Steps: **standardise** (ISO dates; ``DD/MM/YYYY`` is converted and counted), **clean**
(balanced journals, duplicate invoices, supplier reference format, VAT arithmetic,
sub-ledger to GL tie; problems are flagged, never corrected), **recompute** output VAT,
input VAT and the refund position per period, **compare** with the declared e-Tax
position line by line, and **score** each supplier record ``complete``, ``verified``,
``unverified`` or ``contradicted``.

A difference that reverses in the next period (an invoice claimed a period late or early)
is reported as a timing difference; only the excess that persists becomes a finding.
Low data quality on its own never produces an action. RSL independently validates; the
taxpayer remains responsible for its return under self-assessment.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ai.tools.refund_reconciliation import CENT, RefundDataError, money

RECOMPUTE_VERSION = "tax113-recompute-v1"
VAT_INPUT_ACCOUNT = "1400"
VAT_OUTPUT_ACCOUNT = "2400"
ZERO = Decimal("0.00")

_SIGNED = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]{1,2})?")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_DMY = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_PERIOD = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
_LOCAL_TIN = re.compile(r"\d{9}")
_FOREIGN_REF = re.compile(r"F-[A-Z0-9\-]{3,40}")

ACCOUNT_CATEGORIES = (
    ("1400", "vat_input"),
    ("2400", "vat_output"),
    ("4", "revenue"),
    ("50", "purchases"),
    ("61", "management_fees"),
    ("62", "consultancy_fees"),
    ("63", "interest"),
    ("6", "operating_expenses"),
    ("1", "assets"),
    ("2", "liabilities"),
)


def _amount(value: object, field: str) -> Decimal:
    if isinstance(value, float) or not isinstance(value, str) or not _SIGNED.fullmatch(value):
        raise RefundDataError("invalid_money", f"{field} must be a decimal string")
    return Decimal(value).quantize(CENT)


def account_category(account: str) -> str:
    for prefix, category in ACCOUNT_CATEGORIES:
        if account.startswith(prefix):
            return category
    return "unmapped"


def _standard_date(value: object, field: str, counter: list[int]) -> str:
    if isinstance(value, str) and _ISO.fullmatch(value):
        return value
    if isinstance(value, str) and (m := _DMY.fullmatch(value)):
        counter[0] += 1
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    raise RefundDataError("invalid_date", f"{field} must be YYYY-MM-DD or DD/MM/YYYY")


def recompute(
    saft: Mapping[str, Any],
    declared_returns: Iterable[Mapping[str, Any]],
    *,
    vat_rate: Decimal,
    tolerance: Decimal = Decimal("1.00"),
    external_status: Mapping[str, str] | None = None,
    currency: str = "LSL",
) -> dict[str, Any]:
    """Recompute VAT per period from source records and compare with declared returns.

    ``external_status`` optionally maps a supplier ``entry_id`` to ``verified`` or
    ``contradicted`` from an independent source (IDMS or ASYCUDA, via TAX-112).
    """
    header = saft.get("header")
    if not isinstance(header, Mapping) or header.get("currency") != currency:
        raise RefundDataError("invalid_header", "header with currency LSL is required")
    tin = str(header.get("tin"))
    issues: list[dict[str, Any]] = []
    converted = [0]

    # Sales (customer ledger summary).
    output: dict[str, Decimal] = {}
    sales_by_code: dict[str, dict[str, Decimal]] = {}
    for i, row in enumerate(_rows(saft, "sales_summary")):
        period = _period(row.get("period"), f"sales_summary[{i}].period")
        code = row.get("tax_code")
        net = _amount(row.get("net"), f"sales_summary[{i}].net")
        vat = _amount(row.get("vat"), f"sales_summary[{i}].vat")
        if code not in {"STD", "ZERO", "EXEMPT"}:
            raise RefundDataError("invalid_tax_code", f"sales_summary[{i}].tax_code")
        sales_by_code.setdefault(period, {}).setdefault(str(code), ZERO)
        sales_by_code[period][str(code)] += net
        if code == "STD":
            output[period] = output.get(period, ZERO) + vat
            if _vat_of(net, vat_rate) != vat:
                issues.append({"code": "vat_rate_inconsistent", "record": f"sales:{period}"})
        elif vat != ZERO:
            issues.append({"code": "vat_on_non_standard_sale", "record": f"sales:{period}"})

    # Supplier ledger.
    input_vat: dict[str, Decimal] = {}
    seen: dict[tuple[str, str], str] = {}
    quality: dict[str, str] = {}
    supplier_rows = []
    for i, row in enumerate(_rows(saft, "supplier_invoices")):
        entry_id = str(row.get("entry_id"))
        period = _period(row.get("period"), f"supplier_invoices[{i}].period")
        inv_date = _standard_date(row.get("invoice_date"), f"supplier_invoices[{i}]", converted)
        net = _amount(row.get("net"), f"supplier_invoices[{i}].net")
        vat = _amount(row.get("vat"), f"supplier_invoices[{i}].vat")
        code = row.get("tax_code")
        ref = str(row.get("supplier_ref"))
        problems: list[str] = []
        if not (_LOCAL_TIN.fullmatch(ref) or _FOREIGN_REF.fullmatch(ref)):
            problems.append("supplier_reference_invalid")
        key = (ref, str(row.get("invoice_number")))
        duplicate = key in seen
        if duplicate:
            problems.append("duplicate_of:" + seen[key])
        else:
            seen[key] = entry_id
        if code == "STD" and _vat_of(net, vat_rate) != vat:
            problems.append("vat_rate_inconsistent")
        if code not in {"STD", "IMPORT", "NONE"}:
            problems.append("tax_code_unknown")
        if code in {"STD", "IMPORT"} and not duplicate:
            input_vat[period] = input_vat.get(period, ZERO) + vat
        for p in problems:
            issues.append({"code": p.split(":")[0], "record": entry_id, "detail": p})
        status = (external_status or {}).get(entry_id)
        if problems or status == "contradicted":
            quality[entry_id] = (
                "contradicted" if (duplicate or status == "contradicted") else ("unverified")
            )
        elif status == "verified":
            quality[entry_id] = "verified"
        else:
            quality[entry_id] = "complete"
        supplier_rows.append(
            {
                "entry_id": entry_id,
                "period": period,
                "invoice_date": inv_date,
                "category": account_category(str(row.get("account", ""))),
            }
        )

    # General ledger: balance and sub-ledger tie.
    gl_input: dict[str, Decimal] = {}
    gl_output: dict[str, Decimal] = {}
    for i, journal in enumerate(_rows(saft, "journals")):
        period = _period(journal.get("period"), f"journals[{i}].period")
        debit = credit = ZERO
        for j, line in enumerate(_items(journal, "lines")):
            d = _amount(line.get("debit", "0.00"), f"journals[{i}].lines[{j}].debit")
            c = _amount(line.get("credit", "0.00"), f"journals[{i}].lines[{j}].credit")
            debit += d
            credit += c
            account = str(line.get("account", ""))
            if account == VAT_INPUT_ACCOUNT:
                gl_input[period] = gl_input.get(period, ZERO) + d - c
            elif account == VAT_OUTPUT_ACCOUNT:
                gl_output[period] = gl_output.get(period, ZERO) + c - d
        if debit != credit:
            issues.append({"code": "journal_unbalanced", "record": str(journal.get("journal_id"))})
    for period in sorted(set(input_vat) | set(gl_input)):
        if gl_input.get(period, ZERO) != input_vat.get(period, ZERO):
            issues.append({"code": "subledger_gl_mismatch_input_vat", "record": period})
    for period in sorted(set(output) | set(gl_output)):
        if gl_output.get(period, ZERO) != output.get(period, ZERO):
            issues.append({"code": "subledger_gl_mismatch_output_vat", "record": period})

    # Compare with declared returns.
    comparison = []
    diffs: dict[tuple[str, str], Decimal] = {}
    declared_by_period = {}
    for ret in declared_returns:
        if str(ret.get("tin")) != tin:
            raise RefundDataError("taxpayer_mismatch", "declared return belongs to another TIN")
        declared_by_period[str(ret["period"])] = ret
    for period in sorted(declared_by_period):
        ret = declared_by_period[period]
        rec_out = output.get(period, ZERO)
        rec_in = input_vat.get(period, ZERO)
        rec_refund = max(ZERO, rec_in - rec_out)
        for field, recomputed in (
            ("output_vat", rec_out),
            ("input_vat_claimed", rec_in),
            ("refund_claim", rec_refund),
        ):
            declared = _amount(ret.get(field), f"return {period}.{field}")
            diff = declared - recomputed
            diffs[(period, field)] = diff
            comparison.append(
                {
                    "period": period,
                    "line": field,
                    "declared": money(declared, currency),
                    "recomputed": _signed(recomputed, currency),
                    "difference": _signed(diff, currency),
                    "return_id": ret.get("return_id"),
                }
            )
    findings = []
    persistent: dict[tuple[str, str], Decimal] = {}
    for field, sign in (("input_vat_claimed", 1), ("output_vat", -1)):
        persistent.update(_persistent_excess(diffs, field, sign, tolerance))
    for row in comparison:
        key = (str(row["period"]), str(row["line"]))
        diff = diffs[key]
        excess = persistent.get(key, ZERO)
        if row["line"] == "refund_claim":
            row["status"] = "informational"
        elif excess > tolerance:
            row["status"] = "differs"
            row["persistent_excess"] = _signed(excess, currency)
            code = (
                "declared_input_vat_exceeds_recomputed"
                if row["line"] == "input_vat_claimed"
                else "declared_output_vat_below_recomputed"
            )
            findings.append(
                {
                    "code": code,
                    "period": row["period"],
                    "amount": _signed(excess, currency),
                    "evidence_ids": [row["return_id"], f"saft:{tin}:{row['period']}"],
                    "legal_conclusion": None,
                }
            )
        elif abs(diff) <= tolerance:
            row["status"] = "agrees"
        else:
            row["status"] = "timing_difference"
    counts: dict[str, int] = {}
    for status in quality.values():
        counts[status] = counts.get(status, 0) + 1
    return {
        "tin": tin,
        "method": "deterministic_decimal",
        "recompute_version": RECOMPUTE_VERSION,
        "vat_rate_assumption": str(vat_rate),
        "standardised_dates": converted[0],
        "data_issues": issues,
        "record_quality": quality,
        "quality_counts": counts,
        "recomputed": {
            p: {
                "output_vat": _signed(output.get(p, ZERO), currency),
                "input_vat": _signed(input_vat.get(p, ZERO), currency),
            }
            for p in sorted(set(output) | set(input_vat))
        },
        "sales_by_code": {
            p: {k: str(v) for k, v in sorted(codes.items())}
            for p, codes in sorted(sales_by_code.items())
        },
        "supplier_records": supplier_rows,
        "comparison": comparison,
        "findings": findings,
        "responsibility_notice": (
            "RSL independently validates the declared position from the taxpayer's own "
            "records; the taxpayer remains responsible for its return."
        ),
        "decision": None,
    }


def _persistent_excess(
    diffs: Mapping[tuple[str, str], Decimal], field: str, sign: int, tol: Decimal
) -> dict[tuple[str, str], Decimal]:
    """Attribute the declared-over-recomputed excess that does not reverse next period.

    The running total of ``sign * (declared - recomputed)`` is taken through each period.
    The persistent part at a period is the smaller of that total and the next period's
    total, so an invoice claimed one period late (or early) cancels out as timing. The new
    persistent excess that first appears at a period is attributed to that period.
    """
    periods = sorted(p for p, f in diffs if f == field)
    running: list[Decimal] = []
    total = ZERO
    for period in periods:
        total += sign * diffs[(period, field)]
        running.append(total)
    out: dict[tuple[str, str], Decimal] = {}
    previous = ZERO
    for i, period in enumerate(periods):
        nxt = running[i + 1] if i + 1 < len(running) else running[i]
        level = max(ZERO, min(running[i], nxt))
        if level - previous > tol:
            out[(period, field)] = level - previous
        previous = max(previous, level)
    return out


def _vat_of(net: Decimal, rate: Decimal) -> Decimal:
    return (net * rate).quantize(CENT, rounding=ROUND_HALF_UP)


def _signed(value: Decimal, currency: str) -> dict[str, str]:
    return {"amount": str(value.quantize(CENT)), "currency": currency}


def _period(value: object, field: str) -> str:
    if not isinstance(value, str) or not _PERIOD.fullmatch(value):
        raise RefundDataError("invalid_period", f"{field} must be YYYY-MM")
    return value


def _rows(saft: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = saft.get(key, [])
    if not isinstance(value, list) or not all(isinstance(r, Mapping) for r in value):
        raise RefundDataError("invalid_saft", f"{key} must be a list of objects")
    return value


def _items(row: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = row.get(key, [])
    if not isinstance(value, list) or not all(isinstance(r, Mapping) for r in value):
        raise RefundDataError("invalid_saft", f"{key} must be a list of objects")
    return value


def match_lines_to_books(
    lines: Iterable[Mapping[str, Any]], saft: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Find each claimed invoice line in the taxpayer's supplier ledger.

    A line is in the books when a ledger invoice has the same invoice number and VAT and
    either the same supplier reference or one a single adjacent-digit swap away (a typing
    slip in the claim). The booking period may differ from the claim period: an invoice
    claimed late is still in the books.
    """
    index: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in _rows(saft, "supplier_invoices"):
        key = (str(row.get("invoice_number")), str(_amount(row.get("vat"), "ledger.vat")))
        index.setdefault(key, []).append(row)
    out: dict[str, dict[str, Any]] = {}
    for line in lines:
        key = (str(line["invoice_number"]), str(_amount(line["vat_amount"], "line.vat")))
        ref = str(line["supplier_ref"])
        hit = next(
            (
                row
                for row in index.get(key, [])
                if str(row.get("supplier_ref")) == ref
                or str(row.get("supplier_ref")) in _swaps(ref)
            ),
            None,
        )
        out[str(line["line_id"])] = {
            "in_books": hit is not None,
            "entry_id": None if hit is None else hit.get("entry_id"),
            "booked_period": None if hit is None else hit.get("period"),
        }
    return out


def _swaps(ref: str) -> set[str]:
    return {
        ref[:i] + ref[i + 1] + ref[i] + ref[i + 2 :]
        for i in range(len(ref) - 1)
        if ref[i] != ref[i + 1]
    }
