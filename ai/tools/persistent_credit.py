"""Persistent VAT credit positions across periods (TAX-139, Challenge 2).

A VAT credit (input VAT claimed above output VAT) is normal for an exporter, for a business
importing stock through customs, or for one building or equipping premises. A credit that
persists for many periods with none of those explanations is a question worth asking.

``review_persistent_credit`` works period by period over the declared e-Tax VAT returns:

1. **Credit**: ``input_vat_claimed - output_vat`` as declared. A period with no credit is
   recorded and skipped.
2. **Structural explanations, tested in order and recorded with figures** (policy order):

   - ``zero_rated_sales``: the output VAT the taxpayer would have charged had its
     zero-rated sales been standard-rated (``zero_rated_sales x rate``). Evidence: the
     return itself.
   - ``import_vat_paid_at_border``: import VAT on ASYCUDA entries registered to the
     taxpayer in the period. Evidence: RSL's own customs records, so the VAT was paid.
   - ``capital_spending``: VAT on purchases the taxpayer's SAF-T books post to asset
     accounts (``1xxx`` other than the VAT control account). Evidence: the taxpayer's
     books only, so it is marked ``books_only`` for the officer to verify the assets.

   Each test explains at most what is left of the credit after the earlier ones.
3. **Persistence**: the taxpayer has a persistent credit when credits occur in at least the
   policy's number and share of the window's periods.
4. **Finding**: only when the credit is persistent *and* the unexplained remainder (above
   ``min_amount_per_period``) itself persists, in at least ``unexplained.min_periods``
   periods and ``unexplained.min_share_of_window`` of the window, does the
   tool return ``persistent_vat_credit_unexplained``, with the periods, the remainder and
   the questions an officer would put to the taxpayer. Every test result is in the output
   whether or not a finding is raised.

The output is a proposal for an officer. It does not decide, assess, pay or deny anything,
and an unexplained remainder is not evidence of wrongdoing: it says which structural
explanations the records did not support.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

POLICY_PATH = Path(__file__).with_name("persistent_credit_policy.json")
FINDING_CODE = "persistent_vat_credit_unexplained"
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
TESTS = ("zero_rated_sales", "import_vat_paid_at_border", "capital_spending")
VAT_CONTROL_ACCOUNT = "1400"
QUESTIONS = (
    "Explain why input VAT has exceeded output VAT in the periods listed.",
    "List exports or other zero-rated supplies not shown on these returns, with evidence.",
    "List capital purchases and imports in these periods, with invoices and customs entries.",
    "Confirm that all taxable sales in these periods were declared.",
)


class PersistentCreditError(ValueError):
    """Input is malformed; the review cannot run."""


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    order = policy.get("explanation_order")
    if not isinstance(order, list) or sorted(order) != sorted(TESTS):
        raise PersistentCreditError(f"explanation_order must name exactly {TESTS}")
    return policy


def _money(value: object, field: str) -> Decimal:
    if isinstance(value, float) or value is None:
        raise PersistentCreditError(f"{field} must be a decimal string")
    try:
        return Decimal(str(value)).quantize(CENT)
    except ArithmeticError as exc:  # InvalidOperation
        raise PersistentCreditError(f"{field} must be a decimal string") from exc


def _s(x: Decimal) -> str:
    return str(x.quantize(CENT))


def _capital(account: str) -> bool:
    return account.startswith("1") and not account.startswith(VAT_CONTROL_ACCOUNT)


def review_persistent_credit(
    *,
    vat_returns: Iterable[Mapping[str, Any]],
    vat_rate: Decimal,
    saft: Mapping[str, Any] | None = None,
    asycuda_entries: Iterable[Mapping[str, Any]] = (),
    customs_available: bool = True,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Test structural explanations for each credit period, then decide on persistence."""
    pol = policy or load_policy()
    returns = sorted(vat_returns, key=lambda r: str(r["period"]))
    window = [str(r["period"]) for r in returns]
    if len(set(window)) != len(window):
        raise PersistentCreditError("one VAT return per period is expected")

    imports: dict[str, list[Mapping[str, Any]]] = {}
    for e in asycuda_entries:
        imports.setdefault(str(e["registered_on"])[:7], []).append(e)
    capital: dict[str, list[Mapping[str, Any]]] = {}
    for entry in (saft or {}).get("supplier_invoices", []):
        if _capital(str(entry.get("account", ""))):
            capital.setdefault(str(entry["period"]), []).append(entry)

    floor = Decimal(pol["unexplained"]["min_amount_per_period"])
    rows: list[dict[str, Any]] = []
    for r in returns:
        period = str(r["period"])
        output = _money(r["output_vat"], f"{period}.output_vat")
        claimed = _money(r["input_vat_claimed"], f"{period}.input_vat_claimed")
        credit = claimed - output
        row: dict[str, Any] = {
            "period": period,
            "return_id": r.get("return_id"),
            "output_vat": _s(output),
            "input_vat_claimed": _s(claimed),
            "credit": _s(max(credit, ZERO)),
            "tests": [],
            "unexplained": "0.00",
            "unexplained_material": False,
        }
        rows.append(row)
        if credit <= 0:
            continue
        remaining = credit
        for test in pol["explanation_order"]:
            available, evidence, basis = _explanation(
                test, r, period, vat_rate, imports, capital, customs_available
            )
            explained = min(remaining, available) if available is not None else ZERO
            remaining -= explained
            row["tests"].append(
                {
                    "test": test,
                    "status": "not_tested" if available is None else "tested",
                    "available": None if available is None else _s(available),
                    "explained": _s(explained),
                    "remaining_after": _s(remaining),
                    "basis": basis,
                    "evidence": evidence,
                }
            )
        row["unexplained"] = _s(remaining)
        row["unexplained_material"] = remaining > floor

    credit_periods = [x["period"] for x in rows if Decimal(x["credit"]) > 0]
    unexplained_periods = [x["period"] for x in rows if x["unexplained_material"]]
    persistence = pol["persistence"]
    persistent = bool(window) and (
        len(credit_periods) >= int(persistence["min_credit_periods"])
        and Decimal(len(credit_periods)) / Decimal(len(window))
        >= Decimal(persistence["min_credit_share_of_window"])
    )
    totals = {
        "credit": _s(sum((Decimal(x["credit"]) for x in rows), ZERO)),
        "unexplained": _s(
            sum((Decimal(x["unexplained"]) for x in rows if x["unexplained_material"]), ZERO)
        ),
    }
    for test in TESTS:
        totals[f"explained_by_{test}"] = _s(
            sum(
                (Decimal(t["explained"]) for x in rows for t in x["tests"] if t["test"] == test),
                ZERO,
            )
        )
    findings: list[dict[str, Any]] = []
    if not persistent:
        status = "no_persistent_credit"
    elif len(unexplained_periods) >= int(pol["unexplained"]["min_periods"]) and Decimal(
        len(unexplained_periods)
    ) / Decimal(len(window)) >= Decimal(pol["unexplained"]["min_share_of_window"]):
        status = "unexplained"
        findings.append(
            {
                "code": FINDING_CODE,
                "reason": (
                    f"Credits in {len(credit_periods)} of {len(window)} periods; after "
                    "testing zero-rated sales, import VAT paid at the border and capital "
                    f"spending, {len(unexplained_periods)} periods keep an unexplained "
                    f"credit totalling M{Decimal(totals['unexplained']):,.2f}."
                ),
                "periods": unexplained_periods,
                "unexplained_total": {"amount": totals["unexplained"], "currency": "LSL"},
                "questions": list(QUESTIONS),
            }
        )
    else:
        status = "explained"
    books_only = any(
        t["test"] == "capital_spending" and Decimal(t["explained"]) > 0
        for x in rows
        for t in x["tests"]
    )
    return {
        "policy_version": pol["policy_version"],
        "status": status,
        "window": {
            "periods": len(window),
            "first": window[0] if window else None,
            "last": window[-1] if window else None,
        },
        "credit_periods": credit_periods,
        "unexplained_periods": unexplained_periods,
        "persistent": persistent,
        "totals": totals,
        "explanation_order": list(pol["explanation_order"]),
        "officer_checks": (
            ["Capital spending is taken from the taxpayer's books: verify the assets exist."]
            if books_only and persistent
            else []
        ),
        "periods": rows,
        "findings": findings,
        "customs_available": customs_available,
        "decision": None,
    }


def _explanation(
    test: str,
    ret: Mapping[str, Any],
    period: str,
    vat_rate: Decimal,
    imports: Mapping[str, list[Mapping[str, Any]]],
    capital: Mapping[str, list[Mapping[str, Any]]],
    customs_available: bool,
) -> tuple[Decimal | None, list[str], str]:
    if test == "zero_rated_sales":
        zero = _money(ret.get("zero_rated_sales", "0.00"), f"{period}.zero_rated_sales")
        forgone = (zero * vat_rate).quantize(CENT)
        return (
            forgone,
            [str(ret.get("return_id"))] if ret.get("return_id") else [],
            f"Zero-rated sales M{zero:,.2f} x {vat_rate} = output VAT not charged "
            f"M{forgone:,.2f} (declared return).",
        )
    if test == "import_vat_paid_at_border":
        if not customs_available:
            return None, [], "ASYCUDA was unavailable; import VAT could not be checked."
        entries = imports.get(period, [])
        paid = sum((_money(e["import_vat"], "import_vat") for e in entries), ZERO)
        return (
            paid,
            [str(e["entry_id"]) for e in entries],
            f"{len(entries)} ASYCUDA entries in the period, import VAT M{paid:,.2f} "
            "(RSL customs record).",
        )
    rows = capital.get(period, [])
    vat = sum((_money(x.get("vat", "0.00"), "vat") for x in rows), ZERO)
    return (
        vat,
        [str(x["entry_id"]) for x in rows],
        f"{len(rows)} purchases posted to asset accounts, VAT M{vat:,.2f} "
        "(taxpayer's books only: books_only).",
    )
