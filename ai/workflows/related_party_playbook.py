"""TAX-049 related-party service and persistent-loss playbook.

``review_related_party`` runs through the shared case-scoped record tool, and
``propose_officer_review`` turns its cited observations, counter-evidence and
gaps into one advisory finding. The finding's ``code`` and ``evidence_ids`` are
compatible with the TAX-052 policy-gap gate. Nothing here establishes pricing,
intent, liability or abuse; an officer reviews every proposal. With a
``models`` factory (``ai.models.get_model``; TAX-097) the proposal also carries a
validated ``interpretation`` from the ``reasoning`` role; the deterministic
observations, gaps and questions are unchanged.
"""

from langgraph.graph.state import CompiledStateGraph

from ai.tools.refund_reconciliation import DEFAULT_CURRENCY, RefundDataError
from ai.tools.related_party import PROHIBITED_CONCLUSIONS, REVIEW_VERSION, review_related_party
from ai.workflows.fake_graph import InvestigationTool
from ai.workflows.interpretation import ModelFactory, interpret_related_party
from ai.workflows.record_playbook import build_review_graph, make_record_tool
from ai.workflows.refund_lanes import RefundRecordSource
from ai.workflows.rule_register import load_rule_register
from ai.workflows.state import InvestigationState

TOOL_NAME = "related_party_review_tool"
PLAYBOOK_VERSION = "tax049-related-party-playbook-v1"


def make_related_party_tool(
    source: RefundRecordSource, *, currency: str = DEFAULT_CURRENCY
) -> InvestigationTool:
    """Build a case-scoped TAX-022 tool that reviews related-party services and losses."""
    return make_record_tool(
        source,
        tool_name=TOOL_NAME,
        review=lambda record: review_related_party(record, currency=currency),
        failure_message="Related-party records could not be reviewed.",
    )


def _entries(review: dict[str, object], key: str) -> list[dict[str, object]]:
    value = review.get(key, [])
    if not isinstance(value, list) or not all(
        isinstance(item, dict) and isinstance(item.get("evidence_ids"), list) for item in value
    ):
        raise RefundDataError("invalid_review", f"{key} entries need evidence_ids")
    return value


def propose_officer_review(review: dict[str, object]) -> dict[str, object] | None:
    """One advisory finding, or ``None`` when there is no relationship or loss pattern."""
    observations = _entries(review, "observations")
    if not observations:
        return None
    counter = _entries(review, "counter_evidence")
    gaps = _entries(review, "evidence_gaps")
    discrepancies = _entries(review, "discrepancies")
    questions = _entries(review, "evidence_questions")
    codes = {str(item.get("code")) for item in [*observations, *gaps, *discrepancies]}
    if codes & PROHIBITED_CONCLUSIONS:
        raise RefundDataError("prohibited_conclusion", "Review produced a prohibited conclusion")
    evidence_ids: list[str] = []
    for item in [*observations, *counter, *gaps, *discrepancies]:
        cited = item["evidence_ids"]
        if isinstance(cited, list):
            evidence_ids.extend(str(e) for e in cited if str(e) not in evidence_ids)
    code = (
        "related_party_transaction"
        if "related_party_transaction" in codes
        else "financing_driven_losses"
        if "financing_driven_losses" in codes
        else "consecutive_loss_periods"
    )
    # TAX-140: financing-driven losses and thin capitalisation, each with its TAX-116
    # route (unverified candidates or a policy gap; never eligible for assessment).
    financing = review.get("financing_review")
    financing_block = None
    if isinstance(financing, dict) and financing.get("findings"):
        register = load_rule_register()
        financing_block = {
            "findings": list(financing["findings"]),
            "loss_periods": financing["loss_periods"],
            "alternative_explanations_tested": financing["alternative_explanations_tested"],
            "routes": {c: register.route_for(c) for c in financing["findings"]},
        }
    return {
        "kind": "related_party_review_proposal",
        "code": code,
        "evidence_ids": evidence_ids,
        "observed_risk": True,
        "status": "proposed",
        "advisory": True,
        "requires_officer_approval": True,
        "decision": None,
        "approved_by": None,
        "proposed_next_step": "targeted_evidence_request" if questions else "officer_review",
        "observations": observations,
        "counter_evidence": counter,
        "evidence_gaps": gaps,
        "discrepancies": discrepancies,
        "evidence_questions": questions,
        "alternative_explanations": [
            item
            for item in counter
            if item.get("code")
            in ("documented_alternative_explanation", "loan_funded_capital_investment")
        ],
        "financing": financing_block,
        "legal_conclusion": None,
        "enforcement_conclusion": None,
        "playbook_version": PLAYBOOK_VERSION,
        "review_version": review.get("review_version", REVIEW_VERSION),
        "notice": (
            "Advisory review drafted from deterministic record tests. A related party or "
            "a loss pattern is not evidence of wrongdoing; unsupported services are "
            "evidence gaps, not conclusions. Pricing, intent and tax treatment are not "
            "assessed. An officer must approve any request before it reaches the taxpayer."
        ),
    }


def build_related_party_graph(
    tool: InvestigationTool,
    *,
    models: ModelFactory | None = None,
) -> CompiledStateGraph[InvestigationState, None, InvestigationState, InvestigationState]:
    """Compile review_related_party -> propose_officer_review; failures stop for review."""

    def propose(review: dict[str, object]) -> dict[str, object] | None:
        proposal = propose_officer_review(review)
        if proposal is not None and models is not None:
            proposal["interpretation"] = interpret_related_party(models, proposal)
        return proposal

    return build_review_graph(
        tool,
        propose,
        review_node="review_related_party",
        propose_node="propose_officer_review",
    )
