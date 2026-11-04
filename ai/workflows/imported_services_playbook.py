"""TAX-050 imported-services VAT and withholding evidence playbook (model-free).

``review`` runs the deterministic imported-services reconciliation through the
TAX-022 envelope validation. ``propose_evidence_request`` turns an unresolved
trail into an advisory, officer-approved evidence request. Statutory treatment
always stays unverified pending expert review; the finding's ``code`` and
``evidence_ids`` are compatible with the TAX-052 policy-gap gate, which reports
the legal route separately. No node assesses tax, establishes liability or
sends anything to the taxpayer.
"""

from langgraph.graph.state import CompiledStateGraph

from ai.tools.imported_services import (
    FINDING_CODE,
    PROHIBITED_CONCLUSIONS,
    REVIEW_VERSION,
    STATUTORY_STATUS,
    review_imported_services,
)
from ai.tools.refund_reconciliation import DEFAULT_CURRENCY, RefundDataError
from ai.workflows.fake_graph import InvestigationTool
from ai.workflows.record_playbook import build_review_graph, make_record_tool
from ai.workflows.refund_lanes import RefundRecordSource
from ai.workflows.state import InvestigationState

TOOL_NAME = "imported_services_review_tool"
PLAYBOOK_VERSION = "tax050-imported-services-playbook-v1"


def make_imported_services_tool(
    source: RefundRecordSource, *, currency: str = DEFAULT_CURRENCY
) -> InvestigationTool:
    """Build a case-scoped TAX-022 tool that reviews the active case's imported services."""
    return make_record_tool(
        source,
        tool_name=TOOL_NAME,
        review=lambda record: review_imported_services(record, currency=currency),
        failure_message="Imported-service records could not be reviewed.",
    )


def propose_evidence_request(review: dict[str, object]) -> dict[str, object] | None:
    """Turn a review into one advisory finding, or ``None`` when no imported charge exists."""
    charges = review.get("charges")
    if not isinstance(charges, list) or not charges:
        return None
    evidence_ids: list[str] = []
    questions: list[object] = []
    gaps: list[object] = []
    discrepancies: list[object] = []
    for charge in charges:
        if not isinstance(charge, dict):
            raise RefundDataError("invalid_review", "charges must be objects")
        for key, target in (
            ("evidence_ids", evidence_ids),
            ("evidence_questions", questions),
            ("gaps", gaps),
            ("discrepancies", discrepancies),
        ):
            value = charge.get(key, [])
            if not isinstance(value, list):
                raise RefundDataError("invalid_review", f"{key} must be a list")
            target.extend(item for item in value if item not in target)
    codes = {item.get("code") for item in [*gaps, *discrepancies] if isinstance(item, dict)}
    if codes & PROHIBITED_CONCLUSIONS:
        raise RefundDataError("prohibited_conclusion", "Review produced a prohibited conclusion")
    unresolved = review.get("unresolved_trail") is True
    return {
        "kind": "imported_services_evidence_request_proposal",
        "code": FINDING_CODE,
        "evidence_ids": evidence_ids,
        "observed_risk": unresolved,
        "status": "proposed",
        "advisory": True,
        "requires_officer_approval": True,
        "decision": None,
        "approved_by": None,
        "proposed_next_step": "targeted_evidence_request"
        if questions
        else "expert_treatment_review",
        "evidence_questions": questions,
        "gaps": gaps,
        "discrepancies": discrepancies,
        "unresolved_trail": unresolved,
        "unresolved_trail_amount": review.get("unresolved_trail_amount"),
        "statutory_treatment": {"status": STATUTORY_STATUS, "legal_conclusion": None},
        "enforcement_conclusion": None,
        "playbook_version": PLAYBOOK_VERSION,
        "review_version": review.get("review_version", REVIEW_VERSION),
        "notice": (
            "Advisory evidence request drafted from deterministic record reconciliation. "
            "Statutory VAT and withholding treatment is unverified pending expert review; "
            "this is not a liability, non-compliance or abuse finding. An officer must "
            "approve any request before it reaches the taxpayer."
        ),
    }


def build_imported_services_graph(
    tool: InvestigationTool,
) -> CompiledStateGraph[InvestigationState, None, InvestigationState, InvestigationState]:
    """Compile review -> propose_evidence_request; failures stop for human review."""
    return build_review_graph(
        tool,
        propose_evidence_request,
        review_node="review_imported_services",
        propose_node="propose_evidence_request",
    )
