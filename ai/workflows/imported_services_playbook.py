"""TAX-050 imported-services VAT and withholding evidence playbook (model-free).

``review`` runs the deterministic imported-services reconciliation through the
TAX-022 envelope validation. ``propose_evidence_request`` turns an unresolved
trail into an advisory, officer-approved evidence request. Statutory treatment
always stays unverified pending expert review; the finding's ``code`` and
``evidence_ids`` are compatible with the TAX-052 policy-gap gate, which reports
the legal route separately. No node assesses tax, establishes liability or
sends anything to the taxpayer.
"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ai.tools.contracts import (
    ToolInvocationContext,
    ToolResultEnvelope,
    tool_failure,
    tool_success,
)
from ai.tools.imported_services import (
    FINDING_CODE,
    PROHIBITED_CONCLUSIONS,
    REVIEW_VERSION,
    STATUTORY_STATUS,
    review_imported_services,
)
from ai.tools.refund_reconciliation import DEFAULT_CURRENCY, RefundDataError
from ai.workflows.fake_graph import InvestigationTool, _validate_result
from ai.workflows.refund_lanes import RefundRecordSource
from ai.workflows.state import InvestigationState

TOOL_NAME = "imported_services_review_tool"
PLAYBOOK_VERSION = "tax050-imported-services-playbook-v1"


def make_imported_services_tool(
    source: RefundRecordSource, *, currency: str = DEFAULT_CURRENCY
) -> InvestigationTool:
    """Build a case-scoped TAX-022 tool that reviews the active case's imported services."""

    def imported_services_review_tool(context: ToolInvocationContext) -> ToolResultEnvelope:
        scope = {
            "tool_name": TOOL_NAME,
            "tenant_id": context["tenant_id"],
            "case_id": context["case_id"],
            "run_id": context["run_id"],
            "correlation_id": context["correlation_id"],
        }
        record = source(context["tenant_id"], context["case_id"])
        if record is None:
            return tool_failure(
                **scope,
                code="case_records_not_found",
                message="No records are available for this case.",
                retryable=False,
            )
        if record.get("case_id") not in (None, context["case_id"]):
            return tool_failure(
                **scope,
                code="tool_scope_mismatch",
                message="Records did not match the active investigation.",
                retryable=False,
            )
        try:
            review = review_imported_services(record, currency=currency)
        except RefundDataError as error:
            # Stable code only; record content stays out of the envelope.
            return tool_failure(
                **scope,
                code=error.code,
                message="Imported-service records could not be reviewed.",
                retryable=False,
            )
        return tool_success(**scope, data=review)

    imported_services_review_tool.__name__ = TOOL_NAME
    return imported_services_review_tool


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

    def review(state: InvestigationState) -> dict[str, object]:
        tool_name = getattr(tool, "__name__", "tool")
        scope = {
            "tool_name": tool_name,
            "tenant_id": state["tenant_id"],
            "case_id": state["case_id"],
            "run_id": state["run_id"],
            "correlation_id": state["correlation_id"],
        }
        if state["step_count"] >= state["max_steps"]:
            result: ToolResultEnvelope = tool_failure(
                **scope,
                code="step_budget_exhausted",
                message="Investigation step budget is exhausted.",
                retryable=False,
            )
        else:
            context: ToolInvocationContext = {
                "tenant_id": state["tenant_id"],
                "case_id": state["case_id"],
                "run_id": state["run_id"],
                "correlation_id": state["correlation_id"],
                "evidence_ids": list(state["evidence_ids"]),
            }
            try:
                result = _validate_result(tool(context), state, tool_name)
            except Exception:
                result = tool_failure(
                    **scope,
                    code="tool_execution_failed",
                    message="Tool execution failed.",
                    retryable=True,
                )
        return {
            "tool_results": [*state["tool_results"], result],
            "step_count": state["step_count"] + 1,
            "requires_human_review": state["requires_human_review"] or result["status"] == "error",
        }

    def route(state: InvestigationState) -> str:
        return "stop" if state["tool_results"][-1]["status"] == "error" else "propose"

    def propose(state: InvestigationState) -> dict[str, object]:
        result = state["tool_results"][-1]
        if result["status"] != "ok":  # pragma: no cover - routed away above
            return {"requires_human_review": True}
        try:
            proposal = propose_evidence_request(result["data"])
        except RefundDataError:
            return {"requires_human_review": True}
        if proposal is None:
            return {}
        # Replay-safe: one proposal per case/run/source tool.
        if any(
            item.get("kind") == proposal["kind"] and item.get("run_id") == state["run_id"]
            for item in state["findings"]
        ):
            return {"requires_human_review": True}
        finding: dict[str, object] = {
            **proposal,
            "case_id": state["case_id"],
            "run_id": state["run_id"],
            "source_tool": result["tool_name"],
        }
        return {"findings": [*state["findings"], finding], "requires_human_review": True}

    builder = StateGraph(InvestigationState)
    builder.add_node("review_imported_services", review)
    builder.add_node("propose_evidence_request", propose)
    builder.add_edge(START, "review_imported_services")
    builder.add_conditional_edges(
        "review_imported_services", route, {"propose": "propose_evidence_request", "stop": END}
    )
    builder.add_edge("propose_evidence_request", END)
    return builder.compile()
