"""A bounded LangGraph harness that uses only explicitly synthetic tools."""

from collections.abc import Sequence
import re
from typing import Protocol

from langgraph.graph import END, START, StateGraph

from ai.tools.contracts import (
    ToolInvocationContext,
    ToolResultEnvelope,
    tool_failure,
    tool_success,
)
from ai.workflows.state import InvestigationState

DEFAULT_MAX_STEPS = 3
MAX_TOOL_STEPS = 8
_TOOL_NAME = "synthetic_evidence_tool"


class InvestigationTool(Protocol):
    def __call__(self, context: ToolInvocationContext) -> ToolResultEnvelope: ...


def create_investigation_state(
    *,
    tenant_id: str,
    case_id: str,
    run_id: str,
    workflow_version: str = "1",
    correlation_id: str | None = None,
    evidence_ids: Sequence[str] = (),
    max_steps: int = DEFAULT_MAX_STEPS,
) -> InvestigationState:
    """Build an isolated graph state and reject invalid execution budgets."""
    for name, value in (
        ("tenant_id", tenant_id),
        ("case_id", case_id),
        ("run_id", run_id),
        ("workflow_version", workflow_version),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
    if correlation_id is None:
        correlation_id = run_id
    if not isinstance(correlation_id, str) or not correlation_id.strip():
        raise ValueError("correlation_id must be a non-empty string")
    if isinstance(max_steps, bool) or not isinstance(max_steps, int):
        raise ValueError("max_steps must be an integer")
    if not 1 <= max_steps <= MAX_TOOL_STEPS:
        raise ValueError(f"max_steps must be between 1 and {MAX_TOOL_STEPS}")
    if any(not isinstance(item, str) or not item.strip() for item in evidence_ids):
        raise ValueError("evidence_ids must contain non-empty strings")
    if isinstance(evidence_ids, str):
        raise ValueError("evidence_ids must be a sequence of strings, not a string")

    return {
        "tenant_id": tenant_id,
        "case_id": case_id,
        "run_id": run_id,
        "workflow_version": workflow_version,
        "correlation_id": correlation_id,
        "evidence_ids": list(evidence_ids),
        "findings": [],
        "tool_results": [],
        "step_count": 0,
        "max_steps": max_steps,
        "requires_human_review": False,
    }


def synthetic_evidence_tool(context: ToolInvocationContext) -> ToolResultEnvelope:
    """Return an explicitly synthetic result for this case, with no live calls."""
    return tool_success(
        tool_name=_TOOL_NAME,
        tenant_id=context["tenant_id"],
        case_id=context["case_id"],
        run_id=context["run_id"],
        correlation_id=context["correlation_id"],
        data={"synthetic": True, "evidence_ids": list(context["evidence_ids"])},
    )


def _validate_result(
    result: object, state: InvestigationState, tool_name: str
) -> ToolResultEnvelope:
    """Reject malformed or cross-case tool output before it enters graph state."""
    if not isinstance(result, dict) or result.get("status") not in {"ok", "error"}:
        return tool_failure(
            tool_name=tool_name,
            tenant_id=state["tenant_id"],
            case_id=state["case_id"],
            run_id=state["run_id"],
            correlation_id=state["correlation_id"],
            code="invalid_tool_result",
            message="Tool returned an invalid result envelope.",
            retryable=False,
        )

    scope = ("tenant_id", "case_id", "run_id", "correlation_id")
    if any(result.get(key) != state[key] for key in scope):
        return tool_failure(
            tool_name=tool_name,
            tenant_id=state["tenant_id"],
            case_id=state["case_id"],
            run_id=state["run_id"],
            correlation_id=state["correlation_id"],
            code="tool_scope_mismatch",
            message="Tool result did not match the active investigation.",
            retryable=False,
        )
    if result.get("tool_name") != tool_name:
        return tool_failure(
            tool_name=tool_name,
            tenant_id=state["tenant_id"],
            case_id=state["case_id"],
            run_id=state["run_id"],
            correlation_id=state["correlation_id"],
            code="tool_name_mismatch",
            message="Tool result did not match the invoked tool.",
            retryable=False,
        )
    if result["status"] == "ok" and isinstance(result.get("data"), dict):
        return result  # type: ignore[return-value]
    error = result.get("error")
    if (
        result["status"] == "error"
        and isinstance(error, dict)
        and isinstance(error.get("code"), str)
        and isinstance(error.get("message"), str)
        and isinstance(error.get("retryable"), bool)
    ):
        code = error["code"]
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
            code = "tool_reported_error"
        return tool_failure(
            tool_name=tool_name,
            tenant_id=state["tenant_id"],
            case_id=state["case_id"],
            run_id=state["run_id"],
            correlation_id=state["correlation_id"],
            code=code,
            message="Tool reported an error.",
            retryable=error["retryable"],
        )
    return tool_failure(
        tool_name=tool_name,
        tenant_id=state["tenant_id"],
        case_id=state["case_id"],
        run_id=state["run_id"],
        correlation_id=state["correlation_id"],
        code="invalid_tool_result",
        message="Tool returned an invalid result envelope.",
        retryable=False,
    )


def build_fake_tool_graph(
    tool: InvestigationTool = synthetic_evidence_tool,
):
    """Compile an injectable fake-tool loop; failures stop and request review."""

    def invoke_tool(state: InvestigationState) -> dict[str, object]:
        tool_name = getattr(tool, "__name__", "tool")
        context: ToolInvocationContext = {
            "tenant_id": state["tenant_id"],
            "case_id": state["case_id"],
            "run_id": state["run_id"],
            "correlation_id": state["correlation_id"],
            "evidence_ids": list(state["evidence_ids"]),
        }
        try:
            raw_result = tool(context)
            result = _validate_result(raw_result, state, tool_name)
        except Exception:
            # Keep exception text out of findings, logs and tool result envelopes.
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
        results = [*state["tool_results"], result]
        failed = result["status"] == "error"
        return {
            "tool_results": results,
            "step_count": state["step_count"] + 1,
            "requires_human_review": state["requires_human_review"] or failed,
        }

    def route(state: InvestigationState) -> str:
        if state["step_count"] >= state["max_steps"]:
            return "stop"
        if state["tool_results"][-1]["status"] == "error":
            return "stop"
        return "continue"

    builder = StateGraph(InvestigationState)
    builder.add_node("tool", invoke_tool)
    builder.add_edge(START, "tool")
    builder.add_conditional_edges(
        "tool", route, {"continue": "tool", "stop": END}
    )
    return builder.compile()
