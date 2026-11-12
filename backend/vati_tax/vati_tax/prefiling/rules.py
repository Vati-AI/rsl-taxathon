"""Check before you file: pure rules (TAX-129, ADR-043 §3, ADR-051).

No Frappe import, so the rules are testable without a bench. ``store`` reads RSL's data
through the TAX-111 source and runs the TAX-048 reconciliation through the one portal
adapter (``portal.presubmission``).

The check reads what RSL already holds for one taxpayer and VAT period, instead of asking
the taxpayer to type it:

- the VAT return filed in e-Tax (output VAT, input VAT claimed, refund claimed);
- purchase invoices in Lekuka where the taxpayer is the buyer, dated in the period;
- ASYCUDA customs entries where the taxpayer is the importer, registered in the period.

It returns guidance only. It never files, blocks or scores: there is no lane, risk score,
pass/fail or "eligible" in the result, and every message tells the taxpayer to file in
RSL's Online Refunds Application whatever the result. A source that does not answer is
reported as not checked; the result is then never "no issues found".
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Final

CHECK_VERSION: Final = "tax129-prefiling-v1"
CURRENCY: Final = "LSL"
_PERIOD = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")

NO_ISSUES: Final = "no_issues_found"
CHECK_THESE: Final = "check_these"
INCOMPLETE: Final = "incomplete"

SOURCES: Final = {
    "etax": "your VAT return in e-Tax",
    "idms": "your purchase invoices in Lekuka",
    "asycuda": "your customs entries in ASYCUDA",
}
FILE_WITH: Final = "Online Refunds Application"
GUIDANCE_NOTE: Final = (
    "This check is guidance only. It does not file a claim, does not decide anything and "
    "does not stop you filing. File your refund claim in RSL's Online Refunds Application."
)
# Guidance per code; no accusation, penalty or decision language.
PROMPTS: Final = {
    "no_vat_return": (
        "We could not find a VAT return for {period} in e-Tax. File the return in e-Tax "
        "before you claim a refund for that period."
    ),
    "return_arithmetic_mismatch": (
        "In your e-Tax return for {period}, the refund should equal input VAT minus output "
        "VAT. Check these figures before you file."
    ),
    "input_vat_above_rsl_records": (
        "Your return claims {amount} more input VAT than the invoices and customs entries "
        "RSL holds for {period}. If some suppliers are not yet on Lekuka, upload their "
        "invoices when you file; otherwise check the amount."
    ),
    "supplier_not_declared": (
        "{count} invoice(s) in Lekuka from supplier(s) {suppliers} have not been declared by "
        "the supplier yet. You can still file; RSL may ask you about them."
    ),
    "source_not_checked": "We could not read {source} right now, so that part was not checked.",
}
# From the TAX-048 reconciliation only its return arithmetic is used: its other amounts
# also need payment and contract evidence that RSL does not hold.
RECONCILIATION_CODES: Final = ("return_arithmetic_mismatch",)


class InvalidPeriod(ValueError):
    """The period is not YYYY-MM, or is in the future."""


def check_period(value: object, today: date) -> str:
    if not isinstance(value, str) or not _PERIOD.fullmatch(value):
        raise InvalidPeriod("Choose a VAT period as YYYY-MM.")
    year, month = int(value[:4]), int(value[5:])
    if (year, month) > (today.year, today.month):
        raise InvalidPeriod("Choose a period that has started.")
    return value


def _money(value: object) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def _fmt(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def in_period(day: object, period: str) -> bool:
    return isinstance(day, str) and day[:7] == period


def build_record(
    vat_return: Mapping[str, Any],
    invoices: Sequence[Mapping[str, Any]],
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The TAX-048 reconciliation record from RSL-held data (no typed figures)."""
    rows: list[dict[str, Any]] = [
        {"id": f"idms:{inv['idms_id']}", "net": _money(inv["net_amount"]),
         "vat": _money(inv["vat_amount"])}
        for inv in invoices
    ] + [
        {"id": f"asycuda:{e['entry_id']}", "net": _money(e["customs_value"]),
         "vat": _money(e["import_vat"])}
        for e in entries
    ]  # fmt: skip
    return {
        "currency": CURRENCY,
        "return": {
            "id": str(vat_return["return_id"]),
            "refund_claim": _fmt(_money(vat_return["refund_claim"])),
            "input_vat_claimed": _fmt(_money(vat_return["input_vat_claimed"])),
            "output_vat": _fmt(_money(vat_return["output_vat"])),
        },
        "invoices": [
            {
                "id": r["id"],
                "direction": "purchase",
                "net_amount": _fmt(r["net"]),
                "vat_amount": _fmt(r["vat"]),
                "gross_amount": _fmt(r["net"] + r["vat"]),
                "contract_id": None,
            }
            for r in rows
        ],  # fmt: skip
        "contracts": [],
        "payments": [],
    }


def _amount_text(entry: Mapping[str, Any]) -> str:
    value = entry.get("amount")
    if isinstance(value, Mapping):
        value = value.get("amount")
    return f"{CURRENCY} {_money(value):,.2f}"


def result(
    *,
    period: str,
    statuses: Mapping[str, str],
    vat_return: Mapping[str, Any] | None,
    invoices: Sequence[Mapping[str, Any]],
    entries: Sequence[Mapping[str, Any]],
    reconciliation: Mapping[str, Any] | None,
    file_url: str | None,
) -> dict[str, Any]:
    """The taxpayer-facing result: figures RSL holds, guidance, and where to file."""
    prompts: list[dict[str, str]] = []
    for system, label in SOURCES.items():
        if statuses.get(system) != "ok":
            prompts.append({"code": "source_not_checked",
                            "message": PROMPTS["source_not_checked"].format(source=label)})  # fmt: skip
    if statuses.get("etax") == "ok" and vat_return is None:
        prompts.append({"code": "no_vat_return",
                        "message": PROMPTS["no_vat_return"].format(period=period)})  # fmt: skip
    seen: set[str] = set()
    for entry in [*(reconciliation or {}).get("discrepancies", []),
                  *(reconciliation or {}).get("gaps", [])]:  # fmt: skip
        code = entry.get("code")
        if code in RECONCILIATION_CODES and code not in seen:
            seen.add(code)
            prompts.append({"code": code, "message": PROMPTS[code].format(
                period=period, amount=_amount_text(entry))})  # fmt: skip
    undeclared = [inv for inv in invoices if inv.get("supplier_output_declared") is False]
    if undeclared:
        suppliers = ", ".join(sorted({str(inv["supplier_tin"]) for inv in undeclared}))
        prompts.append({"code": "supplier_not_declared", "message": PROMPTS[
            "supplier_not_declared"].format(count=len(undeclared), suppliers=suppliers)})  # fmt: skip
    lekuka = sum((_money(i["vat_amount"]) for i in invoices), Decimal("0.00"))
    customs = sum((_money(e["import_vat"]) for e in entries), Decimal("0.00"))
    sources_ok = statuses.get("idms") == "ok" and statuses.get("asycuda") == "ok"
    if vat_return is not None and sources_ok:
        above = _money(vat_return["input_vat_claimed"]) - lekuka - customs
        if above > 0:
            prompts.append({"code": "input_vat_above_rsl_records", "message": PROMPTS[
                "input_vat_above_rsl_records"].format(
                    amount=f"{CURRENCY} {above:,.2f}", period=period)})  # fmt: skip
    figures: dict[str, str | None] = {
        "output_vat": _fmt(_money(vat_return["output_vat"])) if vat_return else None,
        "input_vat_claimed": _fmt(_money(vat_return["input_vat_claimed"])) if vat_return else None,
        "refund_claimed": _fmt(_money(vat_return["refund_claim"])) if vat_return else None,
        "lekuka_input_vat": _fmt(lekuka) if statuses.get("idms") == "ok" else None,
        "asycuda_import_vat": _fmt(customs) if statuses.get("asycuda") == "ok" else None,
    }
    incomplete = any(p["code"] in {"source_not_checked", "no_vat_return"} for p in prompts)
    status = INCOMPLETE if incomplete else CHECK_THESE if prompts else NO_ISSUES
    return {
        "check_version": CHECK_VERSION,
        "period": period,
        "status": status,
        "currency": CURRENCY,
        "figures": figures,
        "counts": {"lekuka_invoices": len(invoices), "asycuda_entries": len(entries)},
        "prompts": prompts,
        "sources": [
            {"system": s, "label": label, "checked": statuses.get(s) == "ok"}
            for s, label in SOURCES.items()
        ],  # fmt: skip
        "guidance_only": True,
        "note": GUIDANCE_NOTE,
        "file_with": {"name": FILE_WITH, "url": file_url},
    }


def select(
    period: str,
    returns: Sequence[Mapping[str, Any]],
    invoices: Sequence[Mapping[str, Any]],
    entries: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """The period's return (latest filed), purchase invoices and customs entries."""
    period_returns = sorted(
        (r for r in returns if r.get("period") == period), key=lambda r: str(r.get("filed_on"))
    )
    return (
        period_returns[-1] if period_returns else None,
        [i for i in invoices if in_period(i.get("issue_date"), period)],
        [e for e in entries if in_period(e.get("registered_on"), period)],
    )
