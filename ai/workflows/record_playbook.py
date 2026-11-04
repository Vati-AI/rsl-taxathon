"""Shared case-record tool and review -> propose graph for deterministic playbooks.

Used by the TAX-050 imported-services and TAX-049 related-party playbooks. The
tool resolves records by (tenant_id, case_id) only, returns stable error codes
without record content, and the graph enforces the step budget, stops on tool
errors and adds at most one advisory proposal per run (replay-safe).
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
from ai.tools.refund_reconciliation import RefundDataError
from ai.workflows.fake_graph import InvestigationTool, _validate_result
from ai.workflows.refund_lanes import RefundRecordSource
from ai.workflows.state import InvestigationState

RecordReview = Callable[[Mapping[str, object]], dict[str, object]]
ProposalBuilder = Callable[[dict[str, object]], dict[str, object] | None]


def make_record_tool(
    source: RefundRecordSource,
    *,
    tool_name: str,
    review: RecordReview,
    failure_message: str,
) -> InvestigationTool:
    """Build a case-scoped TAX-022 tool that runs ``review`` on the active case's records."""

    def record_tool(context: ToolInvocationContext) -> ToolResultEnvelope:
        scope = {
            "tool_name": tool_name,
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
            data = review(record)
        except RefundDataError as error:
            # Stable code only; record content stays out of the envelope.
            return tool_failure(**scope, code=error.code, message=failure_message, retryable=False)
        return tool_success(**scope, data=data)

    record_tool.__name__ = tool_name
    return record_tool


def build_review_graph(
    tool: InvestigationTool,
    propose: ProposalBuilder,
    *,
    review_node: str,
    propose_node: str,
) -> CompiledStateGraph[InvestigationState, None, InvestigationState, InvestigationState]:
    """Compile review -> propose; failures stop and request human review."""

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

    def propose_finding(state: InvestigationState) -> dict[str, object]:
        result = state["tool_results"][-1]
        if result["status"] != "ok":  # pragma: no cover - routed away above
            return {"requires_human_review": True}
        try:
            proposal = propose(result["data"])
        except RefundDataError:
            return {"requires_human_review": True}
        if proposal is None:
            return {}
        # Replay-safe: one proposal of each kind per run.
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
    builder.add_node(review_node, review)
    builder.add_node(propose_node, propose_finding)
    builder.add_edge(START, review_node)
    builder.add_conditional_edges(review_node, route, {"propose": propose_node, "stop": END})
    builder.add_edge(propose_node, END)
    return builder.compile()
