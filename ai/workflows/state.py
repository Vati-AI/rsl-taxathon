"""Shared starting types for TAX-022; no tax decision implementation."""
from typing import TypedDict

class InvestigationState(TypedDict):
    tenant_id: str
    case_id: str
    run_id: str
    workflow_version: str
    evidence_ids: list[str]
    findings: list[dict]
    requires_human_review: bool
