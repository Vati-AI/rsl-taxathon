"""Policy-gap gate: separates observed risk from reviewer-verified legal route (TAX-052).

Each finding keeps two independent dimensions:

* ``observed_risk`` — what the case records show, with evidence locators. It is
  never upgraded by a citation and never implies a legal conclusion.
* ``legal_route`` — whether a reviewer-verified, in-force registry authority
  applies: ``verified_route``, ``unverified_route`` or ``no_route`` (policy gap).

Proposed citations (for example from model output) are untrusted. Citations that
are absent from the registry, cite a wrong or out-of-force version, are
unverified or do not cover the finding are rejected and recorded; they never
create a route. The gate emits no enforcement conclusion for any finding:
enforcement-type proposals are blocked, and only a verified route makes a
finding eligible for *human* legal review.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal, TypeAlias

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ai.tools.contracts import ToolResultEnvelope, tool_failure, tool_success
from ai.workflows.legal_routes import AuthorityEntry, LegalRouteRegistry, load_registry
from ai.workflows.state import InvestigationState

TOOL_NAME = "policy_gap_gate"
MAX_FINDINGS = 50
MAX_CITATIONS_PER_FINDING = 5

RouteClassification: TypeAlias = Literal["verified_route", "unverified_route", "no_route"]

NEXT_STEP: dict[RouteClassification, str] = {
    "verified_route": "human_legal_review",
    "unverified_route": "legal_route_verification",
    "no_route": "policy_gap_review",
}
NO_RISK_NEXT_STEP = "no_gate_action"
SAFE_NEXT_STEPS = frozenset({*NEXT_STEP.values(), NO_RISK_NEXT_STEP})

# Outcomes the AI must never conclude or recommend; humans decide (AGENTS.md).
ENFORCEMENT_ACTIONS = frozenset(
    {
        "enforcement_recommended",
        "tax_assessment",
        "penalty",
        "refund_denial",
        "criminal_referral",
        "fraud_accusation",
        "legal_violation_established",
        "proven_abuse",
    }
)

_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-_]{0,63}")


class InvalidFinding(ValueError):
    """A finding did not match the gate input contract."""


@dataclass(frozen=True)
class _FindingInput:
    finding_id: str
    code: str
    evidence_ids: list[str]
    observed_risk: bool
    proposed_citations: list[object]
    proposed_action: str | None
    prior_blocked_actions: list[dict[str, object]]
    prior_malformed_rejections: int


def _finding_input(raw: object, index: int) -> _FindingInput:
    if not isinstance(raw, Mapping):
        raise InvalidFinding(f"finding {index} is not an object")
    code = raw.get("code")
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        raise InvalidFinding(f"finding {index} has an invalid code")
    finding_id = raw.get("finding_id", f"finding-{index + 1}")
    if not isinstance(finding_id, str) or not _ID.fullmatch(finding_id):
        raise InvalidFinding(f"finding {index} has an invalid finding_id")
    evidence_ids = raw.get("evidence_ids")
    if (
        not isinstance(evidence_ids, list)
        or not evidence_ids
        or not all(isinstance(item, str) and _ID.fullmatch(item) for item in evidence_ids)
    ):
        raise InvalidFinding(f"finding {index} needs evidence_ids")
    observed = raw.get("observed_risk", True)
    if isinstance(observed, Mapping):  # already gated: idempotent replay
        observed = observed.get("present")
    if not isinstance(observed, bool):
        raise InvalidFinding(f"finding {index} observed_risk must be boolean")
    citations = raw.get("proposed_citations", [])
    if not isinstance(citations, list) or len(citations) > MAX_CITATIONS_PER_FINDING:
        raise InvalidFinding(f"finding {index} has too many or invalid proposed_citations")
    action = raw.get("proposed_action")
    if action is not None and not isinstance(action, str):
        raise InvalidFinding(f"finding {index} proposed_action must be a string")
    prior_blocked = raw.get("blocked_actions", [])
    prior_route = raw.get("legal_route", {})
    prior_rejected = (
        prior_route.get("rejected_citations", []) if isinstance(prior_route, Mapping) else []
    )
    return _FindingInput(
        finding_id=finding_id,
        code=code,
        evidence_ids=list(evidence_ids),
        observed_risk=observed,
        proposed_citations=list(citations),
        proposed_action=action,
        prior_blocked_actions=[dict(item) for item in prior_blocked if isinstance(item, Mapping)]
        if isinstance(prior_blocked, list)
        else [],
        prior_malformed_rejections=sum(
            1
            for item in prior_rejected
            if isinstance(item, Mapping) and item.get("reason") == "citation_malformed"
        )
        if isinstance(prior_rejected, list)
        else 0,
    )


def gate_finding(
    raw: object,
    *,
    registry: LegalRouteRegistry,
    as_of: date,
    case_evidence_ids: frozenset[str],
    index: int = 0,
) -> dict[str, object]:
    """Classify one finding; deterministic and safe to re-run on its own output."""
    finding = _finding_input(raw, index)
    code = finding.code
    evidence_ids = finding.evidence_ids
    observed = finding.observed_risk

    accepted: list[AuthorityEntry] = []
    rejected: list[dict[str, object]] = [
        {"authority_id": None, "version": None, "reason": "citation_malformed"}
        for _ in range(finding.prior_malformed_rejections)
    ]
    sanitized_citations: list[dict[str, str]] = []
    for raw_citation in finding.proposed_citations:
        check = registry.check_citation(raw_citation, finding_code=code, as_of=as_of)
        if check.citation is not None:
            sanitized_citations.append(
                {
                    "authority_id": check.citation["authority_id"],
                    "version": check.citation["version"],
                }
            )
        if check.accepted and check.entry is not None:
            accepted.append(check.entry)
        else:
            rejected.append(
                {
                    "authority_id": check.citation["authority_id"] if check.citation else None,
                    "version": check.citation["version"] if check.citation else None,
                    "reason": check.rejection,
                }
            )

    in_force = registry.routes_for(code, as_of)
    verified = accepted or [entry for entry in in_force if entry.is_verified]
    classification: RouteClassification
    if verified:
        classification = "verified_route"
        route_entries = verified
    elif in_force:
        classification = "unverified_route"
        route_entries = list(in_force)
    else:
        classification = "no_route"
        route_entries = []
    unique = {(entry.authority_id, entry.version): entry for entry in route_entries}
    route_authorities = [
        unique[key].audit_record(registry.registry_version) for key in sorted(unique)
    ]

    blocked = list(finding.prior_blocked_actions)
    action = finding.proposed_action
    if action is not None and action not in SAFE_NEXT_STEPS:
        reason = (
            "enforcement_conclusion_not_permitted"
            if action in ENFORCEMENT_ACTIONS
            else "action_not_allowlisted"
        )
        blocked_entry: dict[str, object] = {
            "action": action,
            "reason": reason,
            "legal_route": classification,
        }
        if blocked_entry not in blocked:
            blocked.append(blocked_entry)

    unresolved = sorted(set(evidence_ids) - case_evidence_ids)
    next_step = NEXT_STEP[classification] if observed else NO_RISK_NEXT_STEP
    return {
        "finding_id": finding.finding_id,
        "code": code,
        "evidence_ids": evidence_ids,
        "observed_risk": {
            "present": observed,
            "basis": "case_records",
            "evidence_ids": evidence_ids,
            "unresolved_evidence_ids": unresolved,
        },
        "legal_route": {
            "classification": classification,
            "as_of": as_of.isoformat(),
            "registry_version": registry.registry_version,
            "authorities": route_authorities,
            "rejected_citations": rejected,
        },
        "policy_gap": observed and classification == "no_route",
        "human_legal_review_eligible": observed
        and classification == "verified_route"
        and not unresolved,
        "enforcement_conclusion": None,
        "proposed_citations": sanitized_citations,
        "proposed_action": next_step,
        "blocked_actions": blocked,
        "requires_human_review": observed or bool(rejected) or bool(blocked) or bool(unresolved),
    }


def run_policy_gap_gate(
    state: InvestigationState, *, registry: LegalRouteRegistry, as_of: date
) -> ToolResultEnvelope:
    """Gate every finding in the active case and return a TAX-022 tool envelope."""
    scope = {
        "tool_name": TOOL_NAME,
        "tenant_id": state["tenant_id"],
        "case_id": state["case_id"],
        "run_id": state["run_id"],
        "correlation_id": state["correlation_id"],
    }
    if state["step_count"] >= state["max_steps"]:
        return tool_failure(
            **scope,
            code="step_budget_exhausted",
            message="Investigation step budget is exhausted.",
            retryable=False,
        )
    findings = state["findings"]
    if len(findings) > MAX_FINDINGS:
        return tool_failure(
            **scope,
            code="finding_budget_exceeded",
            message="Too many findings for one gate run.",
            retryable=False,
        )
    evidence = frozenset(state["evidence_ids"])
    try:
        gated = [
            gate_finding(item, registry=registry, as_of=as_of, case_evidence_ids=evidence, index=i)
            for i, item in enumerate(findings)
        ]
    except InvalidFinding:
        return tool_failure(
            **scope,
            code="invalid_finding",
            message="A finding did not match the gate contract.",
            retryable=False,
        )
    return tool_success(
        **scope,
        data={
            "synthetic_registry": True,
            "registry_version": registry.registry_version,
            "as_of": as_of.isoformat(),
            "findings": gated,
            "policy_gap_count": sum(1 for item in gated if item["policy_gap"]),
            "enforcement_conclusions": [],
        },
    )


def make_policy_gap_gate_node(
    registry: LegalRouteRegistry, as_of: date
) -> Callable[[InvestigationState], dict[str, object]]:
    """LangGraph node: replaces findings with gated findings and records the envelope."""

    def policy_gap_gate(state: InvestigationState) -> dict[str, object]:
        result = run_policy_gap_gate(state, registry=registry, as_of=as_of)
        update: dict[str, object] = {
            "tool_results": [*state["tool_results"], result],
            "step_count": state["step_count"] + 1,
        }
        if result["status"] == "ok":
            gated = result["data"]["findings"]
            if not isinstance(gated, list):
                raise TypeError("gate produced an invalid findings list")
            update["findings"] = gated
            update["requires_human_review"] = state["requires_human_review"] or any(
                item["requires_human_review"] for item in gated
            )
        else:
            update["requires_human_review"] = True
        return update

    return policy_gap_gate


def build_policy_gap_gate_graph(
    registry: LegalRouteRegistry | None = None, *, as_of: date
) -> CompiledStateGraph[InvestigationState, None, InvestigationState, InvestigationState]:
    """Compile a one-step graph that gates findings already present in state."""
    builder = StateGraph(InvestigationState)
    builder.add_node(TOOL_NAME, make_policy_gap_gate_node(registry or load_registry(), as_of))
    builder.add_edge(START, TOOL_NAME)
    builder.add_edge(TOOL_NAME, END)
    return builder.compile()
