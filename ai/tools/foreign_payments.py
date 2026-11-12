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

**Foreign payments booked outside service accounts (TAX-141):** a payment to a
non-resident counterparty with no service or interest booking (the fees sit in general
expenses, so the match above never tests them) is checked against two independent
descriptions of what was paid for: the Central Bank cross-border report's purpose, where
the report exists and carries one, and the taxpayer's own bank narration. When either
describes services and no withholding or imported-services VAT was declared for the payee,
the match raises ``foreign_service_payments_outside_service_accounts``: a question for the
taxpayer with the figures, never a conclusion. A counterparty marked exempt is not asked.

**CMA caveat:** within the Common Monetary Area there are no exchange restrictions between
banks, so payments to South Africa, Namibia or Eswatini may not appear in Central Bank
cross-border data. Company bank statements are the primary source.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ai.tools.refund_reconciliation import CENT, money, parse_money

MATCH_VERSION = "tax115-three-way-v1"
CMA_COUNTRIES = frozenset({"za", "na", "sz"})
SERVICE_CATEGORIES = frozenset({"management_fees", "consultancy_fees"})
WHT_CATEGORIES = SERVICE_CATEGORIES | {"interest"}
ZERO = Decimal("0.00")
# TAX-141: words in a bank narration that describe a service rather than goods or a loan.
SERVICE_NARRATION = re.compile(
    r"\b(services?|fees?|consult\w*|management|advisory|technical|licen[cs]e\w*|royalt\w*)\b",
    re.IGNORECASE,
)
CB_MATCH_DAYS = 5
OUTSIDE_SERVICE_ACCOUNTS = "foreign_service_payments_outside_service_accounts"
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
    a_rows: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
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
        a_rows.setdefault(key, []).append(p)
    b: dict[tuple[str, str], dict[str, Decimal]] = {}
    b_ids: dict[tuple[str, str], list[str]] = {}
    other: dict[tuple[str, str], Decimal] = {}
    other_ids: dict[tuple[str, str], list[str]] = {}
    for row in supplier_invoices:
        ref = str(row["supplier_ref"])
        if ref not in master or master[ref].get("resident") is True:
            continue
        category = str(row.get("category"))
        if category not in WHT_CATEGORIES:
            if row.get("tax_code") != "IMPORT":  # goods are evidenced by customs entries
                key = (ref, bucket(row["period"]))
                other[key] = other.get(key, ZERO) + parse_money(row["net"], field="ledger")
                other_ids.setdefault(key, []).append("ledger:" + str(row["entry_id"]))
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
    cb_reports = [p for p in (central_bank_payments or []) if p.get("direction") == "outgoing"]

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
        if total_b == ZERO and paid > tol and supplier.get("service_exempt") is not True:
            tested, flag = _payments_outside_service_accounts(
                ref=ref,
                year=year,
                country=country,
                payments=a_rows.get(key, []),
                cb_reports=cb_reports,
                other_booked=other.get(key, ZERO),
                evidence=a_ids.get(key, []) + other_ids.get(key, []),
                declared_wht=declared_wht,
                rates=rates,
                relief_with_certificate=relief and ref in certificates,
                currency=currency,
            )
            explanations.extend(tested)
            if flag is not None:
                flags.append(flag)
                questions.append(
                    {
                        "code": "explain_foreign_service_payments",
                        "counterparty": ref,
                        "year": year,
                        "evidence_ids": flag["evidence_ids"],
                    }
                )
    outside = [f for f in flags if f["code"] == OUTSIDE_SERVICE_ACCOUNTS]
    for year, expected in sorted(expected_vat_by_year.items()):
        declared = declared_vat.get(year, ZERO)
        gap = max(ZERO, expected - declared)
        # Imported-services VAT declared beyond what booked services explain may already
        # cover the payments booked elsewhere: the question asks only for the remainder.
        surplus = max(ZERO, declared - expected)
        for f in (f for f in outside if f["year"] == year):
            covered = min(surplus, f.pop("_vat"))
            surplus -= covered
            if covered:
                f["vat_declared_beyond_booked_services"] = money(covered, currency)
                f["amount"] = money(
                    parse_money(f["amount"]["amount"], field="gap") - covered, currency
                )
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
    for f in outside:
        f.pop("_vat", None)
    flags = [
        f
        for f in flags
        if f["code"] != OUTSIDE_SERVICE_ACCOUNTS
        or parse_money(f["amount"]["amount"], field="gap") > tolerance_abs
    ]
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


def _date(value: object) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _payments_outside_service_accounts(
    *,
    ref: str,
    year: str,
    country: str,
    payments: list[Mapping[str, Any]],
    cb_reports: list[Mapping[str, Any]],
    other_booked: Decimal,
    evidence: list[str],
    declared_wht: Decimal,
    rates: Mapping[str, Decimal],
    relief_with_certificate: bool,
    currency: str,
) -> tuple[list[str], dict[str, Any] | None]:
    """TAX-141: a question when services were paid for but not booked or declared as such.

    Returns the explanations tested (always recorded on the match row) and the flag, if any.
    """
    paid = sum((parse_money(p["settled_amount"], field="payment") for p in payments), ZERO)
    narrated = [p for p in payments if SERVICE_NARRATION.search(str(p.get("narration") or ""))]
    cb_ids: list[str] = []
    cb_services = 0
    for p in payments:
        when = _date(p.get("date"))
        amount = parse_money(p["settled_amount"], field="payment")
        for report in cb_reports:
            if str(report.get("counterparty_country")) != country:
                continue
            seen = _date(report.get("value_date"))
            if when is None or seen is None or abs((seen - when).days) > CB_MATCH_DAYS:
                continue
            if parse_money(report["amount"], field="central_bank") != amount:
                continue
            cb_ids.append("cb:" + str(report["report_id"]))
            cb_services += report.get("purpose") == "services"
            break
    tested = [
        "central_bank_purpose_services" if cb_services else (
            "central_bank_purpose_not_reported" if not cb_ids else "central_bank_purpose_other"
        ),
        "bank_narration_describes_services" if narrated else "bank_narration_not_services",
    ]  # fmt: skip
    if country in CMA_COUNTRIES and not cb_ids:
        tested.append("cma_payment_not_in_central_bank_data")
    if not cb_services and not narrated:
        return tested, None  # nothing says these payments were for services
    wht_rate = rates["wht_treaty"] if relief_with_certificate else rates["wht"]
    wht_gap = max(ZERO, _q(paid * wht_rate) - declared_wht)
    if declared_wht > ZERO:
        tested.append("withholding_declared_for_payee")
    vat = _q(paid * rates["vat"])
    amount = wht_gap + vat
    source = " and ".join(
        part
        for part, present in (
            ("the Central Bank report", cb_services),
            ("the bank narration", bool(narrated)),
        )
        if present
    )
    return tested, {
        "code": OUTSIDE_SERVICE_ACCOUNTS,
        "counterparty": ref,
        "year": year,
        "amount": money(amount, currency),
        "evidence_ids": [*evidence, *cb_ids],
        "_vat": vat,
        "reason": (
            f"{_fmt_m(paid)} paid to {ref} ({country.upper()}) in {year} is described as "
            f"services by {source}, but no service cost is booked for it "
            f"({_fmt_m(other_booked)} sits in general expenses), so its imported-services "
            f"VAT is not in the expected figure; withholding declared {_fmt_m(declared_wht)}. "
            "Ask the taxpayer what the payments were for before treating them as services."
        ),
    }
