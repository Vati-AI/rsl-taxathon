"""Typed, case-scoped contracts shared by investigation tools and workflows."""

from typing import Literal, TypedDict, TypeAlias


class ToolInvocationContext(TypedDict):
    tenant_id: str
    case_id: str
    run_id: str
    correlation_id: str
    evidence_ids: list[str]


class ToolErrorDetail(TypedDict):
    code: str
    message: str
    retryable: bool


class ToolSuccess(TypedDict):
    status: Literal["ok"]
    tool_name: str
    tenant_id: str
    case_id: str
    run_id: str
    correlation_id: str
    data: dict[str, object]


class ToolFailure(TypedDict):
    status: Literal["error"]
    tool_name: str
    tenant_id: str
    case_id: str
    run_id: str
    correlation_id: str
    error: ToolErrorDetail


ToolResultEnvelope: TypeAlias = ToolSuccess | ToolFailure


def tool_success(
    *,
    tool_name: str,
    tenant_id: str,
    case_id: str,
    run_id: str,
    correlation_id: str,
    data: dict[str, object],
) -> ToolSuccess:
    """Create a successful result tagged with its originating investigation."""
    return {
        "status": "ok",
        "tool_name": tool_name,
        "tenant_id": tenant_id,
        "case_id": case_id,
        "run_id": run_id,
        "correlation_id": correlation_id,
        "data": data,
    }


def tool_failure(
    *,
    tool_name: str,
    tenant_id: str,
    case_id: str,
    run_id: str,
    correlation_id: str,
    code: str,
    message: str,
    retryable: bool,
) -> ToolFailure:
    """Create a safe error result without attaching document or exception data."""
    return {
        "status": "error",
        "tool_name": tool_name,
        "tenant_id": tenant_id,
        "case_id": case_id,
        "run_id": run_id,
        "correlation_id": correlation_id,
        "error": {"code": code, "message": message, "retryable": retryable},
    }
