"""Typed, model-independent state for a bounded investigation run."""

from typing import TypedDict

from ai.tools.contracts import ToolResultEnvelope


class InvestigationState(TypedDict):
    tenant_id: str
    case_id: str
    run_id: str
    workflow_version: str
    correlation_id: str
    evidence_ids: list[str]
    findings: list[dict[str, object]]
    tool_results: list[ToolResultEnvelope]
    step_count: int
    max_steps: int
    requires_human_review: bool
