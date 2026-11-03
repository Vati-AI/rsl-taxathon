"""Deterministic, model-free checks for the TAX-022 investigation harness."""

import pytest

from ai.tools.contracts import (
    ToolInvocationContext,
    ToolResultEnvelope,
    tool_failure,
    tool_success,
)
from ai.workflows.fake_graph import (
    build_fake_tool_graph,
    create_investigation_state,
)


def state(case_id: str, *, max_steps: int = 1):
    return create_investigation_state(
        tenant_id="tenant-synthetic",
        case_id=case_id,
        run_id=f"run-{case_id}",
        correlation_id=f"correlation-{case_id}",
        evidence_ids=[f"evidence-{case_id}"],
        max_steps=max_steps,
    )


def test_graph_keeps_case_state_and_evidence_isolated():
    graph = build_fake_tool_graph()

    first = graph.invoke(state("SYN-001"))
    second = graph.invoke(state("SYN-002"))

    assert first["tool_results"][0]["case_id"] == "SYN-001"
    assert first["tool_results"][0]["data"]["evidence_ids"] == ["evidence-SYN-001"]
    assert second["tool_results"][0]["case_id"] == "SYN-002"
    assert second["tool_results"][0]["data"]["evidence_ids"] == ["evidence-SYN-002"]
    first["evidence_ids"].append("local-mutation")
    assert second["evidence_ids"] == ["evidence-SYN-002"]


def test_tool_receives_a_copy_of_case_context():
    def mutating_tool(context: ToolInvocationContext) -> ToolResultEnvelope:
        context["evidence_ids"].clear()
        return tool_success(
            tool_name="mutating_tool",
            tenant_id=context["tenant_id"],
            case_id=context["case_id"],
            run_id=context["run_id"],
            correlation_id=context["correlation_id"],
            data={"synthetic": True},
        )

    result = build_fake_tool_graph(mutating_tool).invoke(state("SYN-007"))

    assert result["evidence_ids"] == ["evidence-SYN-007"]


def test_tool_exception_becomes_safe_error_and_stops():
    def broken_tool(_context: ToolInvocationContext) -> ToolResultEnvelope:
        raise RuntimeError("private document text must not escape")

    result = build_fake_tool_graph(broken_tool).invoke(state("SYN-003", max_steps=4))

    assert result["step_count"] == 1
    assert result["requires_human_review"] is True
    assert result["tool_results"][0]["status"] == "error"
    assert result["tool_results"][0]["error"]["code"] == "tool_execution_failed"
    assert "private document text" not in str(result)


def test_tool_error_message_is_sanitized():
    def unsafe_error_tool(context: ToolInvocationContext) -> ToolResultEnvelope:
        return tool_failure(
            tool_name="unsafe_error_tool",
            tenant_id=context["tenant_id"],
            case_id=context["case_id"],
            run_id=context["run_id"],
            correlation_id=context["correlation_id"],
            code="source_timeout",
            message="raw document content must not be returned",
            retryable=True,
        )

    result = build_fake_tool_graph(unsafe_error_tool).invoke(state("SYN-008"))

    envelope = result["tool_results"][0]
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "source_timeout"
    assert envelope["error"]["message"] == "Tool reported an error."
    assert envelope["error"]["retryable"] is True
    assert envelope["correlation_id"] == "correlation-SYN-008"


def test_graph_stops_after_the_configured_step_budget():
    calls: list[str] = []

    def counted_tool(context: ToolInvocationContext) -> ToolResultEnvelope:
        calls.append(context["case_id"])
        return tool_success(
            tool_name="counted_tool",
            tenant_id=context["tenant_id"],
            case_id=context["case_id"],
            run_id=context["run_id"],
            correlation_id=context["correlation_id"],
            data={"synthetic": True},
        )

    result = build_fake_tool_graph(counted_tool).invoke(state("SYN-004", max_steps=2))

    assert calls == ["SYN-004", "SYN-004"]
    assert result["step_count"] == 2
    assert len(result["tool_results"]) == 2


def test_cross_case_tool_output_is_discarded():
    def cross_case_tool(context: ToolInvocationContext) -> ToolResultEnvelope:
        return tool_success(
            tool_name="cross_case_tool",
            tenant_id=context["tenant_id"],
            case_id="SYN-OTHER",
            run_id="run-other",
            correlation_id="correlation-other",
            data={"private_record": "must not reach graph state"},
        )

    result = build_fake_tool_graph(cross_case_tool).invoke(state("SYN-005", max_steps=3))

    envelope = result["tool_results"][0]
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "tool_scope_mismatch"
    assert envelope["case_id"] == "SYN-005"
    assert "private_record" not in str(envelope)
    assert result["step_count"] == 1


@pytest.mark.parametrize("budget", [0, -1, 9, True])
def test_invalid_or_unbounded_step_budget_is_rejected(budget):
    with pytest.raises(ValueError, match="max_steps"):
        create_investigation_state(
            tenant_id="tenant-synthetic",
            case_id="SYN-006",
            run_id="run-SYN-006",
            max_steps=budget,
        )
