"""Imported-services three-way match with the CMA caveat (TAX-115, framework BP5).

For each foreign counterparty, over the review window (or per calendar year), the match
compares three sources:

A. **money out**: outgoing foreign payments from the taxpayer's bank statements (TAX-079
   ``foreign_payment`` records, settled amount in Maloti);
B. **books**: foreign service costs in the SAF-T-style supplier ledger (TAX-113);
C. **declared**: withholding tax declared per payee and imported-services VAT declared in
   the VAT returns (e-Tax, TAX-111).

Counterparties are joined on the bank account reference held in the taxpayer's supplier
master, never on names. Legitimate explanations are tested before any flag: treaty relief
backed by a residence certificate, services marked exempt, goods (customs entries rather
than services) and timing (the comparison covers the review window by default and uses a
tolerance for exchange rates). Rates are **synthetic assumptions** passed in by the caller; nothing here states
Lesotho law. The output is a reconciliation with reasons and a route recommendation, never
a liability or a decision.

**CMA caveat:** within the Common Monetary Area there are no exchange restrictions between
banks, so payments to South Africa, Namibia or Eswatini may not appear in Central Bank
cross-border data. Company bank statements are the primary source.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ai.tools.refund_reconciliation import CENT, money, parse_money

MATCH_VERSION = "tax115-three-way-v1"
CMA_COUNTRIES = frozenset({"za", "na", "sz"})
SERVICE_CATEGORIES = frozenset({"management_fees", "consultancy_fees"})
WHT_CATEGORIES = SERVICE_CATEGORIES | {"interest"}
ZERO = Decimal("0.00")
CMA_CAVEAT = (
    "Central Bank cross-border data may not show payments within the Common Monetary Area; "
    "company bank statements are the primary source."
)


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _fmt_m(value: Decimal) -> str:
    if value >= Decimal("1000000"):
        return f"M{(value / Decimal('1000000')).quantize(Decimal('0.1'))}m"
    return f"M{value.quantize(Decimal('1')):,}"


def three_way_match(
    *,
    tin: str,
    suppliers: Iterable[Mapping[str, Any]],
    supplier_invoices: Iterable[Mapping[str, Any]],
    bank_payments: Iterable[Mapping[str, Any]],
    wht_lines: Iterable[Mapping[str, Any]],
    vat_returns: Iterable[Mapping[str, Any]],
    residence_certificates: Iterable[str],
    central_bank_payments: Iterable[Mapping[str, Any]] | None,
    rates: Mapping[str, Decimal],
    materiality: Decimal,
    tolerance_share: Decimal = Decimal("0.02"),
    tolerance_abs: Decimal = Decimal("100.00"),
    group_by: str = "window",
    currency: str = "LSL",
) -> dict[str, Any]:
    """Return per counterparty A/B/C amounts, explanations tested and flags.

    ``group_by`` is ``window`` (the whole review window, so late payments and declarations
    inside it net out) or ``year``.
    """
    if group_by not in {"window", "year"}:
        raise ValueError("group_by must be 'window' or 'year'")

    def bucket(value: object) -> str:
        return "window" if group_by == "window" else str(value)[:4]

    master = {str(s["supplier_ref"]): s for s in suppliers}
    by_account = {
        str(s["bank_account_ref"]): ref for ref, s in master.items() if s.get("bank_account_ref")
    }
    certificates = frozenset(residence_certificates)
    a: dict[tuple[str, str], Decimal] = {}
    a_ids: dict[tuple[str, str], list[str]] = {}
    unresolved_payments: list[str] = []
    for p in bank_payments:
        if p.get("direction") != "outflow":
            continue
        ref = by_account.get(str(p.get("counterparty_account_ref")))
        if ref is None:
            unresolved_payments.append(str(p["provider_reference"]))
            continue
        key = (ref, bucket(p["date"]))
        a[key] = a.get(key, ZERO) + parse_money(p["settled_amount"], field="payment")
        a_ids.setdefault(key, []).append("bank:" + str(p["provider_reference"]))
    b: dict[tuple[str, str], dict[str, Decimal]] = {}
    b_ids: dict[tuple[str, str], list[str]] = {}
    for row in supplier_invoices:
        ref = str(row["supplier_ref"])
        if ref not in master or master[ref].get("resident") is True:
            continue
        category = str(row.get("category"))
        if category not in WHT_CATEGORIES:
            continue
        key = (ref, bucket(row["period"]))
        booked_here = b.setdefault(key, {})
        booked_here[category] = booked_here.get(category, ZERO) + parse_money(
            row["net"], field="ledger"
        )
        b_ids.setdefault(key, []).append("ledger:" + str(row["entry_id"]))
    c_gross: dict[tuple[str, str], Decimal] = {}
    c_wht: dict[tuple[str, str], Decimal] = {}
    c_relief: dict[tuple[str, str], bool] = {}
    c_ids: dict[tuple[str, str], list[str]] = {}
    for line in wht_lines:
        key = (str(line["payee_ref"]), bucket(line["period"]))
        c_gross[key] = c_gross.get(key, ZERO) + parse_money(line["gross_amount"], field="wht")
        c_wht[key] = c_wht.get(key, ZERO) + parse_money(line["wht_amount"], field="wht")
        c_relief[key] = c_relief.get(key, False) or line.get("treaty_relief_claimed") is True
        c_ids.setdefault(key, []).append("wht:" + str(line["line_id"]))
    declared_vat: dict[str, Decimal] = {}
    for ret in vat_returns:
        year = bucket(ret["period"])
        declared_vat[year] = declared_vat.get(year, ZERO) + parse_money(
            ret["imported_services_vat_declared"], field="vat_return"
        )
    cb_seen = {
        (str(p.get("counterparty_country")), bucket(p["value_date"]))
        for p in (central_bank_payments or [])
    }

    rows: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    expected_vat_by_year: dict[str, Decimal] = {}
    vat_ids: dict[str, list[str]] = {}
    for key in sorted(set(a) | set(b)):
        ref, year = key
        supplier = master.get(ref, {})
        country = str(supplier.get("country", ""))
        booked = b.get(key, {})
        services = sum((booked.get(c, ZERO) for c in SERVICE_CATEGORIES), ZERO)
        interest = booked.get("interest", ZERO)
        total_b = services + interest
        paid = a.get(key, ZERO)
        explanations: list[str] = []
        notes: list[str] = []
        tol = max(tolerance_abs, _q(total_b * tolerance_share))
        if abs(paid - total_b) > tol:
            questions.append(
                {
                    "code": "payments_and_books_differ",
                    "counterparty": ref,
                    "year": year,
                    "evidence_ids": a_ids.get(key, []) + b_ids.get(key, []),
                }
            )
        relief = c_relief.get(key, False)
        if relief and ref in certificates:
            wht_rate = rates["wht_treaty"]
            explanations.append("treaty_relief_with_residence_certificate")
        elif relief:
            wht_rate = rates["wht_treaty"]
            questions.append(
                {
                    "code": "provide_residence_certificate_for_treaty_relief",
                    "counterparty": ref,
                    "year": year,
                    "evidence_ids": c_ids.get(key, []),
                }
            )
        else:
            wht_rate = rates["wht"]
        expected_wht = _q(total_b * wht_rate)
        declared_wht = c_wht.get(key, ZERO)
        wht_gap = max(ZERO, expected_wht - declared_wht)
        if supplier.get("service_exempt") is True:
            explanations.append("service_marked_exempt")
            vat_expected = ZERO
        else:
            vat_expected = _q(services * rates["vat"])
        expected_vat_by_year[year] = expected_vat_by_year.get(year, ZERO) + vat_expected
        vat_ids.setdefault(year, []).extend(b_ids.get(key, []))
        if country in CMA_COUNTRIES and (country, year) not in cb_seen:
            notes.append("cma_payment_not_in_central_bank_data")
        row = {
            "counterparty": ref,
            "country": country,
            "year": year,
            "money_out": money(paid, currency),
            "booked_services": money(services, currency),
            "booked_interest": money(interest, currency),
            "wht_declared": money(declared_wht, currency),
            "wht_expected": money(expected_wht, currency),
            "imported_services_vat_expected": money(vat_expected, currency),
            "tolerance": money(tol, currency),
            "explanations_tested": explanations,
            "notes": notes,
            "evidence_ids": a_ids.get(key, []) + b_ids.get(key, []) + c_ids.get(key, []),
        }
        rows.append(row)
        if wht_gap > tol:
            flags.append(
                {
                    "code": "wht_declared_below_expected",
                    "counterparty": ref,
                    "year": year,
                    "amount": money(wht_gap, currency),
                    "evidence_ids": row["evidence_ids"],
                    "reason": (
                        f"{_fmt_m(total_b)} booked to {ref} ({country.upper()}) in {year}; "
                        f"withholding declared {_fmt_m(declared_wht)}, expected "
                        f"{_fmt_m(expected_wht)} at the synthetic rate"
                        + ("; no treaty relief found." if not relief else ".")
                    ),
                }
            )
    for year, expected in sorted(expected_vat_by_year.items()):
        declared = declared_vat.get(year, ZERO)
        gap = max(ZERO, expected - declared)
        tol = max(tolerance_abs, _q(expected * tolerance_share))
        if gap > tol:
            flags.append(
                {
                    "code": "imported_services_vat_declared_below_expected",
                    "counterparty": None,
                    "year": year,
                    "amount": money(gap, currency),
                    "evidence_ids": vat_ids.get(year, []),
                    "reason": (
                        f"Imported-services VAT declared for {year} is {_fmt_m(declared)}; "
                        f"booked foreign services imply {_fmt_m(expected)} at the synthetic rate."
                    ),
                }
            )
    total_gap = sum((parse_money(f["amount"]["amount"], field="gap") for f in flags), ZERO)
    route = (
        "special_sectors_review"
        if total_gap >= materiality
        else ("standard_lane_request" if flags else None)
    )
    return {
        "tin": tin,
        "method": "deterministic_decimal",
        "match_version": MATCH_VERSION,
        "rates_are_synthetic_assumptions": True,
        "rows": rows,
        "flags": flags,
        "questions": questions,
        "unresolved_payments": unresolved_payments,
        "total_gap": money(total_gap, currency),
        "route_recommendation": route,
        "cma_caveat": CMA_CAVEAT,
        "statutory_status": "unverified_pending_expert_review",
        "decision": None,
    }
