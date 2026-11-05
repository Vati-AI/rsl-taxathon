"""TAX-048 refund reconciliation tool and advisory lane nodes on the TAX-022 interface.

``reconcile`` runs the deterministic tool through the TAX-022 envelope
validation, and ``propose_lane`` appends an advisory lane finding that always
requires officer review. Neither node records a decision. Amounts, thresholds
and the lane come only from the deterministic tool. With a ``models`` factory
(``ai.models.get_model``; TAX-097) the finding also carries a validated
``interpretation`` from the ``reasoning`` role that explains the reconciliation
and weighs alternatives; without one the graph stays model-free.
"""

from collections.abc import Callable, Mapping

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ai.tools.contracts import (
    ToolInvocationContext,
    ToolResultEnvelope,
    tool_failure,
    tool_success,
)
from ai.tools.refund_reconciliation import (
    DEFAULT_CURRENCY,
    RefundDataError,
    propose_refund_lane,
    reconcile_refund,
)
from ai.workflows.fake_graph import InvestigationTool, _validate_result
from ai.workflows.interpretation import ModelFactory, interpret_refund_lane
from ai.workflows.state import InvestigationState

RECONCILIATION_TOOL_NAME = "refund_reconciliation_tool"

# Records are looked up by (tenant_id, case_id) so one tenant's case can never
# resolve another's records, even when case references collide.
RefundRecordSource = Callable[[str, str], Mapping[str, object] | None]


def records_source(
    records: Mapping[tuple[str, str], Mapping[str, object]],
) -> RefundRecordSource:
    """Wrap an in-memory synthetic record map keyed by (tenant_id, case_id)."""

    def lookup(tenant_id: str, case_id: str) -> Mapping[str, object] | None:
        return records.get((tenant_id, case_id))

    return lookup


def make_refund_reconciliation_tool(
    source: RefundRecordSource, *, currency: str = DEFAULT_CURRENCY
) -> InvestigationTool:
    """Build a case-scoped TAX-022 tool that reconciles the active case's refund."""

    def refund_reconciliation_tool(context: ToolInvocationContext) -> ToolResultEnvelope:
        scope = {
            "tool_name": RECONCILIATION_TOOL_NAME,
            "tenant_id": context["tenant_id"],
            "case_id": context["case_id"],
            "run_id": context["run_id"],
            "correlation_id": context["correlation_id"],
        }
        record = source(context["tenant_id"], context["case_id"])
        if record is None:
            return tool_failure(
                **scope,
                code="refund_record_not_found",
                message="No refund records are available for this case.",
                retryable=False,
            )
        if record.get("case_id") not in (None, context["case_id"]):
            return tool_failure(
                **scope,
                code="tool_scope_mismatch",
                message="Refund records did not match the active investigation.",
                retryable=False,
            )
        try:
            reconciliation = reconcile_refund(record, currency=currency)
        except RefundDataError as error:
            # Stable code only; record content stays out of the envelope.
            return tool_failure(
                **scope,
                code=error.code,
                message="Refund records could not be reconciled.",
                retryable=False,
            )
        return tool_success(**scope, data=reconciliation)

    refund_reconciliation_tool.__name__ = RECONCILIATION_TOOL_NAME
    return refund_reconciliation_tool


def build_refund_lane_graph(
    tool: InvestigationTool,
    *,
    models: ModelFactory | None = None,
) -> CompiledStateGraph[InvestigationState, None, InvestigationState, InvestigationState]:
    """Compile reconcile -> propose_lane; failures stop and request human review.

    ``models`` is ``get_model`` (or a test double with the same signature); it is
    asked for the ``reasoning`` role only to interpret the deterministic result.
    """

    def reconcile(state: InvestigationState) -> dict[str, object]:
        context: ToolInvocationContext = {
            "tenant_id": state["tenant_id"],
            "case_id": state["case_id"],
            "run_id": state["run_id"],
            "correlation_id": state["correlation_id"],
            "evidence_ids": list(state["evidence_ids"]),
        }
        tool_name = getattr(tool, "__name__", "tool")
        try:
            result = _validate_result(tool(context), state, tool_name)
        except Exception:
            result = tool_failure(
                tool_name=tool_name,
                tenant_id=state["tenant_id"],
                case_id=state["case_id"],
                run_id=state["run_id"],
                correlation_id=state["correlation_id"],
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

    def propose_lane(state: InvestigationState) -> dict[str, object]:
        result = state["tool_results"][-1]
        if result["status"] != "ok":  # pragma: no cover - routed away above
            return {"requires_human_review": True}
        try:
            proposal = propose_refund_lane(result["data"])
        except RefundDataError:
            return {"requires_human_review": True}
        finding: dict[str, object] = {
            **proposal,
            "case_id": state["case_id"],
            "run_id": state["run_id"],
            "source_tool": result["tool_name"],
        }
        if models is not None:
            finding["interpretation"] = interpret_refund_lane(models, proposal, result["data"])
        # A lane is advisory: the graph always hands it to an officer.
        return {"findings": [*state["findings"], finding], "requires_human_review": True}

    builder = StateGraph(InvestigationState)
    builder.add_node("reconcile", reconcile)
    builder.add_node("propose_lane", propose_lane)
    builder.add_edge(START, "reconcile")
    builder.add_conditional_edges("reconcile", route, {"propose": "propose_lane", "stop": END})
    builder.add_edge("propose_lane", END)
    return builder.compile()
