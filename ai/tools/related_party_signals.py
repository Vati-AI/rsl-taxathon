"""Related-party and profit-shifting review triggers (framework BP6; feeds TAX-116).

Deterministic triggers from the framework, computed from e-Tax CIT history and the
taxpayer's agreements:

- ``related_party_fees_with_persistent_losses``: at least three consecutive loss years
  while related-party fees grow faster than revenue (by ``fee_growth_margin`` in
  compound annual terms), with no documented alternative explanation for the loss years;
- ``related_party_interest_free_loan``: a related-party loan at a zero interest rate.
  After AfriSam this has no legal route to impute interest, so the register routes it to
  the policy-gap register rather than to an assessment.

A documented explanation (market entry, restructuring, exchange loss, write-off) is
recorded as an alternative explanation tested and suppresses the loss trigger. Nothing
here prices a transaction or concludes on deductibility.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

SIGNALS_VERSION = "bp6-signals-v1"
EXPLANATION_CODES = frozenset({"market_entry", "restructuring", "exchange_loss", "write_off"})


def _cagr(first: Decimal, last: Decimal, years: int) -> Decimal | None:
    if first <= 0 or last <= 0 or years <= 0:
        return None
    return Decimal(str((float(last / first)) ** (1 / years) - 1)).quantize(Decimal("0.0001"))


def related_party_signals(
    *,
    cit_returns: Iterable[Mapping[str, Any]],
    agreements: Iterable[Mapping[str, Any]],
    loss_explanations: Iterable[Mapping[str, Any]],
    min_loss_years: int = 3,
    fee_growth_margin: Decimal = Decimal("0.10"),
) -> dict[str, Any]:
    """Return BP6 triggers with evidence IDs and the explanations tested."""
    returns = sorted(cit_returns, key=lambda r: int(str(r["year"])))
    explained = {
        int(str(e["year"])) for e in loss_explanations if e.get("code") in EXPLANATION_CODES
    }
    findings: list[dict[str, Any]] = []
    explanations: list[dict[str, Any]] = []
    streak: list[Mapping[str, Any]] = []
    best: list[Mapping[str, Any]] = []
    for r in returns:
        if r.get("taxable_income_sign") == "loss":
            streak.append(r)
            if len(streak) > len(best):
                best = list(streak)
        else:
            streak = []
    if len(best) >= min_loss_years and len(returns) >= 2:
        first, last = returns[0], returns[-1]
        span = int(str(last["year"])) - int(str(first["year"]))
        fee_growth = _cagr(
            Decimal(str(first["related_party_fees"])),
            Decimal(str(last["related_party_fees"])),
            span,
        )
        rev_growth = _cagr(Decimal(str(first["revenue"])), Decimal(str(last["revenue"])), span)
        loss_years = [int(str(r["year"])) for r in best]
        evidence = [str(r["cit_return_id"]) for r in returns]
        if explained & set(loss_years):
            explanations.append(
                {
                    "code": "documented_loss_explanation",
                    "years": sorted(explained & set(loss_years)),
                }
            )
        elif (
            fee_growth is not None
            and rev_growth is not None
            and fee_growth - rev_growth >= fee_growth_margin
        ):
            findings.append(
                {
                    "code": "related_party_fees_with_persistent_losses",
                    "loss_years": loss_years,
                    "fee_growth": str(fee_growth),
                    "revenue_growth": str(rev_growth),
                    "evidence_ids": evidence,
                    "reason": (
                        f"Losses in {len(loss_years)} consecutive years "
                        f"({loss_years[0]}-{loss_years[-1]}) while related-party fees grew "
                        f"{fee_growth:.1%} a year against revenue growth of {rev_growth:.1%}; "
                        "no documented explanation."
                    ),
                    "legal_conclusion": None,
                }
            )
        else:
            explanations.append({"code": "fees_not_outpacing_revenue", "years": loss_years})
    for agreement in agreements:
        if agreement.get("kind") != "loan" or agreement.get("related_party") is not True:
            continue
        if Decimal(str(agreement.get("annual_interest_rate", "0"))) == 0:
            findings.append(
                {
                    "code": "related_party_interest_free_loan",
                    "counterparty": agreement.get("counterparty_ref"),
                    "principal": agreement.get("principal"),
                    "evidence_ids": [str(agreement["document_id"])],
                    "reason": (
                        f"Interest-free related-party loan of M{Decimal(str(agreement['principal'])):,}"
                        f" from {agreement.get('counterparty_ref')}."
                    ),
                    "legal_conclusion": None,
                }
            )
    return {
        "method": "deterministic_triggers",
        "signals_version": SIGNALS_VERSION,
        "findings": findings,
        "alternative_explanations_tested": explanations,
        "decision": None,
    }
