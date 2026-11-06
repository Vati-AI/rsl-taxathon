"""Refund lanes with service targets, undisputed portion and RSL inputs (TAX-114, BP3).

Extends the TAX-048 lane idea to the framework's three lanes, fed by RSL's own data:

- **inputs**: the Online Refunds Application claim, the TAX-112 IDMS/ASYCUDA line match,
  the TAX-113 declared-versus-recomputed comparison, the RSL refund-fraud model score
  (carried as RSL's, with model ID and version), open taxpayer-level flags (TAX-115
  imported services, related-party review) and the group risk rating, which is ``not
  available`` until BP2 exists. A missing input is stated as missing and never counts as
  clean.
- **structural explanation first**: a refund position is expected when zero-rated sales
  are a large share of sales; a credit without that explanation is a question, not a
  flag.
- **lanes** (``fast``, ``standard``, ``investigation``; ``investigation`` corresponds to
  TAX-048 ``targeted``) carry a proposed decision-time target and due date from the
  versioned policy file.
- the **undisputed portion** is the claim less the VAT on lines that are not yet
  supported. It is a recommendation for the refund auditor; nothing is released or
  denied here.

The RSL score on its own never moves a claim into investigation: it needs unresolved
evidence alongside it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from ai.tools.refund_reconciliation import money, parse_money

POLICY_PATH = Path(__file__).with_name("refund_triage_policy.json")
ZERO = Decimal("0.00")
_DISPUTED = frozenset({"unmatched", "supplier_gap"})


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("status") != "proposed_prototype_choice":
        raise ValueError("lane policy must be marked as a proposed prototype choice")
    return policy


def add_working_days(start: str, days: int) -> str:
    """Add weekdays to an ISO date. Public holidays are not modelled."""
    current = date.fromisoformat(start)
    left = days
    while left > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            left -= 1
    return current.isoformat()


def triage_claim(
    claim: Mapping[str, Any],
    *,
    line_match: Mapping[str, Any],
    recompute: Mapping[str, Any] | None,
    rsl_score: Mapping[str, Any] | None,
    taxpayer_flags: Sequence[Mapping[str, Any]] = (),
    group_rating: str | None = None,
    policy: Mapping[str, Any] | None = None,
    currency: str = "LSL",
) -> dict[str, Any]:
    """Propose a lane with reasons, a service target and the undisputed portion."""
    pol = policy or load_policy()
    claimed = parse_money(claim["claimed_amount"], field="claim.claimed_amount")
    period = str(claim["period"])
    reasons: list[str] = []
    inputs: dict[str, Any] = {}
    questions: list[dict[str, Any]] = []

    # Line evidence (TAX-112).
    disputed_lines = []
    line_vat = ZERO
    authenticity = False
    counts: dict[str, int] = {}
    for line in line_match.get("lines", []):
        status = str(line["status"])
        counts[status] = counts.get(status, 0) + 1
        if status in _DISPUTED:
            vat = parse_money(line["vat"]["amount"], field="line.vat")
            line_vat += vat
            disputed_lines.append(
                {"line_id": line["line_id"], "vat": line["vat"], "questions": line["questions"]}
            )
            if "supplier_not_registered" in line["notes"] or (
                "provide_invoice_absent_from_idms" in line["questions"]
            ):
                authenticity = True
        for q in line["questions"]:
            questions.append({"code": q, "line_id": line["line_id"]})
    total = sum(counts.values())
    inputs["idms_match"] = {
        "lines": total,
        "by_status": counts,
        "idms_available": line_match.get("idms_available"),
    }

    # Declared versus recomputed (TAX-113) for this period.
    recompute_excess = ZERO
    zero_share: Decimal | None = None
    if recompute is None:
        inputs["recompute"] = "not available"
        reasons.append("Source records were not supplied, so the return was not recomputed.")
    else:
        for finding in recompute.get("findings", []):
            if finding["period"] == period and finding["code"] in {
                "declared_input_vat_exceeds_recomputed",
                "declared_refund_exceeds_recomputed",
            }:
                recompute_excess = max(recompute_excess, Decimal(finding["amount"]["amount"]))
        sales = recompute.get("sales_by_code", {}).get(period)
        if sales:
            total_sales = sum((Decimal(v) for v in sales.values()), ZERO)
            if total_sales > 0:
                zero_share = (Decimal(sales.get("ZERO", "0")) / total_sales).quantize(
                    Decimal("0.0001")
                )
        inputs["recompute"] = {
            "declared_exceeds_recomputed": money(recompute_excess, currency),
            "zero_rated_share": None if zero_share is None else str(zero_share),
            "quality_counts": recompute.get("quality_counts"),
        }
        if recompute_excess > 0:
            authenticity = True
            reasons.append(
                f"The declared position exceeds the amount recomputed from the taxpayer's own "
                f"records by M{recompute_excess:,}."
            )

    # RSL score (owned by RSL; carried with model identity).
    score: Decimal | None = None
    if rsl_score is None or rsl_score.get("status") == "unavailable":
        inputs["rsl_fraud_score"] = "not available"
    else:
        score = Decimal(str(rsl_score["score"]))
        inputs["rsl_fraud_score"] = {
            "score": str(score),
            "model_id": rsl_score.get("model_id"),
            "model_version": rsl_score.get("model_version"),
            "owner": "RSL",
        }
    inputs["group_risk_rating"] = group_rating or "not available"
    inputs["taxpayer_flags"] = [f.get("code") for f in taxpayer_flags]

    disputed = max(line_vat, recompute_excess)
    disputed = min(disputed, claimed)
    undisputed = claimed - disputed
    material = disputed >= Decimal(pol["materiality"]["absolute"]) or (
        claimed > 0 and disputed / claimed >= Decimal(pol["materiality"]["share_of_claim"])
    )
    review_at = Decimal(pol["rsl_score"]["review_at_or_above"])
    high_at = Decimal(pol["rsl_score"]["high_at_or_above"])
    structural_ok = zero_share is not None and zero_share >= Decimal(
        pol["structural"]["min_zero_rated_share"]
    )

    if authenticity and material:
        lane = "investigation"
        reasons.append(
            f"M{disputed:,} of input VAT is unsupported with an authenticity indicator "
            "(supplier not registered, invoice absent from IDMS for an integrated supplier, "
            "or declared figures above the taxpayer's own records)."
        )
    elif disputed > 0 and score is not None and score >= high_at:
        lane = "investigation"
        reasons.append(
            f"RSL's refund model scores this claim {score} and M{disputed:,} is unsupported."
        )
    elif (
        disputed > 0
        or questions
        or taxpayer_flags
        or not structural_ok
        or (score is not None and score >= review_at)
    ):
        lane = "standard"
        if disputed > 0:
            reasons.append(f"M{disputed:,} of input VAT needs targeted evidence.")
        if questions and disputed == 0:
            reasons.append("Specific lines need a short clarification.")
        for flag in taxpayer_flags:
            reasons.append(f"Open taxpayer-level item: {flag.get('code')}.")
        if not structural_ok:
            reasons.append("The refund position is not explained by zero-rated sales.")
        if score is not None and score >= review_at:
            reasons.append(f"RSL's refund model scores this claim {score} (review level).")
    else:
        lane = "fast"
        reasons.append(
            "Every input-VAT line is supported, the return agrees with the taxpayer's records "
            "and zero-rated sales explain the refund position."
        )
    if score is None:
        reasons.append("RSL's refund-model score was not available for this claim.")

    lane_pol = pol["lanes"][lane]
    submitted = str(claim["submitted_on"])
    target = int(lane_pol["decision_target_working_days"])
    service: dict[str, Any] = {
        "decision_target_working_days": target,
        "decision_due": add_working_days(submitted, target),
        "status": "proposed_target_verify_against_vat_act",
    }
    if lane == "investigation":
        plan = int(lane_pol["case_plan_working_days"])
        service["case_plan_working_days"] = plan
        service["case_plan_due"] = add_working_days(submitted, plan)
    return {
        "claim_id": claim["claim_id"],
        "lane": lane,
        "policy_version": pol["policy_version"],
        "reasons": reasons,
        "inputs": inputs,
        "service_target": service,
        "claimed_amount": money(claimed, currency),
        "undisputed_amount": money(undisputed, currency),
        "undisputed_status": "recommendation_for_refund_auditor",
        "disputed_amount": money(disputed, currency),
        "disputed_items": disputed_lines,
        "questions": questions,
        "requires_officer_review": True,
        "decision": None,
    }
