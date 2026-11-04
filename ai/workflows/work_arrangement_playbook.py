"""TAX-051 employment-versus-consultancy specialist playbook (model-free).

``structure_facts`` runs the deterministic fact review for the active case.
When a contract needs classification, ``specialist_checkpoint`` persists the
structured facts (TAX-047 SQLite checkpoint) and interrupts. The run resumes
only for an authenticated specialist (TAX-046 ``specialist.opinion.record``)
bound to the same tenant, case, case version and SHA-256 of the exact facts
reviewed. ``record_specialist_opinion`` stores that human opinion with
attribution; the workflow never classifies, assesses tax or decides. An
officer decision (TAX-046) is still required afterwards.
"""

from collections.abc import Mapping
from typing import NotRequired, TypedDict, cast

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from ai.tools.contracts import ToolInvocationContext, ToolResultEnvelope, tool_failure, tool_success
from ai.tools.refund_reconciliation import RefundDataError
from ai.tools.work_arrangement import CLASSIFICATION_STATUS, review_work_arrangement
from ai.workflows.fake_graph import InvestigationTool, _validate_result
from ai.workflows.human_checkpoints import ResumeRejected, action_digest
from ai.workflows.refund_lanes import RefundRecordSource
from ai.workflows.state import InvestigationState

TOOL_NAME = "work_arrangement_review_tool"
PLAYBOOK_VERSION = "tax051-specialist-playbook-v1"
SPECIALIST_ROLE = "specialist"
# Opinions a specialist may record; "undetermined" keeps the case unresolved.
OPINION_VALUES = frozenset({"employment", "independent_service", "undetermined"})
RATIONALE_MIN = 20


class SpecialistReferral(TypedDict):
    tenant_id: str
    case_id: str
    run_id: str
    case_version: int
    referrals: list[dict[str, object]]
    facts_digest: str
    evidence_ids: list[str]
    proposed_by: str


class SpecialistState(InvestigationState):
    case_version: int
    referral: NotRequired[SpecialistReferral | None]
    specialist_opinion: NotRequired[dict[str, object] | None]


def make_work_arrangement_tool(source: RefundRecordSource) -> InvestigationTool:
    """Build a case-scoped TAX-022 tool that structures work-arrangement facts."""

    def work_arrangement_review_tool(context: ToolInvocationContext) -> ToolResultEnvelope:
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
            review = review_work_arrangement(record)
        except RefundDataError as error:
            # Stable code only; record content stays out of the envelope.
            return tool_failure(
                **scope,
                code=error.code,
                message="Work-arrangement records could not be reviewed.",
                retryable=False,
            )
        return tool_success(**scope, data=review)

    work_arrangement_review_tool.__name__ = TOOL_NAME
    return work_arrangement_review_tool


def check_specialist_resume(
    referral: SpecialistReferral, opinion: object, actor: object
) -> dict[str, object]:
    """Validate a specialist opinion against the persisted referral; raise otherwise."""
    if not isinstance(actor, Mapping) or actor.get("authenticated") is not True:
        raise ResumeRejected("not_authenticated")
    if not isinstance(opinion, Mapping):
        raise ResumeRejected("invalid_opinion")
    actor_id = actor.get("actor_id")
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise ResumeRejected("missing_identity")
    roles = actor.get("roles")
    rationale = opinion.get("rationale")
    cited = opinion.get("evidence_ids")
    checks: tuple[tuple[bool, str], ...] = (
        (actor.get("tenant_id") == referral["tenant_id"], "tenant_mismatch"),
        (opinion.get("tenant_id") == referral["tenant_id"], "tenant_mismatch"),
        (opinion.get("case_id") == referral["case_id"], "case_mismatch"),
        (isinstance(roles, list) and SPECIALIST_ROLE in roles, "role_not_permitted"),
        (opinion.get("specialist_id") == actor_id, "actor_not_specialist"),
        (actor_id != referral["proposed_by"], "self_approval"),
        (opinion.get("case_version") == referral["case_version"], "stale_case_version"),
        (opinion.get("facts_digest") == referral["facts_digest"], "facts_mismatch"),
        (opinion.get("classification") in OPINION_VALUES, "invalid_classification"),
        (
            isinstance(rationale, str) and len(rationale.strip()) >= RATIONALE_MIN,
            "rationale_required",
        ),
        (
            isinstance(cited, list)
            and bool(cited)
            and set(map(str, cited)) <= set(referral["evidence_ids"]),
            "citation_not_in_referral",
        ),
    )
    for passed, code in checks:
        if not passed:
            raise ResumeRejected(code)
    return {
        "kind": "specialist_classification_opinion",
        "status": "specialist_opinion_recorded",
        "classification": opinion.get("classification"),
        "specialist_id": actor_id,
        "rationale": str(rationale).strip(),
        "evidence_ids": [str(e) for e in cited] if isinstance(cited, list) else [],
        "case_version": referral["case_version"],
        "facts_digest": referral["facts_digest"],
        "decided_by_agent": False,
        "assessment": None,
        "next_step": "officer_decision_required",
    }


def build_work_arrangement_graph(
    tool: InvestigationTool, checkpointer: SqliteSaver, *, agent_id: str
) -> CompiledStateGraph[SpecialistState, None, SpecialistState, SpecialistState]:
    """Compile structure_facts -> specialist_checkpoint -> record_specialist_opinion."""

    def structure_facts(state: SpecialistState) -> dict[str, object]:
        if state.get("referral"):
            return {}  # replay keeps the persisted facts and digest
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
        update: dict[str, object] = {
            "tool_results": [*state["tool_results"], result],
            "step_count": state["step_count"] + 1,
        }
        if result["status"] != "ok":
            update["requires_human_review"] = True
            return update
        referrals = result["data"].get("referrals")
        if not isinstance(referrals, list) or not referrals:
            return update
        evidence_ids: list[str] = []
        for item in referrals:
            if not isinstance(item, dict) or not isinstance(item.get("factors"), list):
                raise RefundDataError("invalid_review", "referral needs factors")
            evidence_ids.extend(
                str(e) for e in item.get("evidence_ids", []) if str(e) not in evidence_ids
            )
            for factor in item["factors"]:
                for statement in factor.get("statements", []):
                    evidence_ids.extend(
                        str(e)
                        for e in statement.get("evidence_ids", [])
                        if str(e) not in evidence_ids
                    )
        referral: SpecialistReferral = {
            "tenant_id": state["tenant_id"],
            "case_id": state["case_id"],
            "run_id": state["run_id"],
            "case_version": state["case_version"],
            "referrals": referrals,
            "facts_digest": action_digest({"referrals": referrals}),
            "evidence_ids": evidence_ids,
            "proposed_by": agent_id,
        }
        finding: dict[str, object] = {
            "kind": "specialist_referral",
            "code": "service_classification_evidence_gap",
            "evidence_ids": evidence_ids,
            "observed_risk": True,
            "classification_status": CLASSIFICATION_STATUS,
            "classification": None,
            "auto_classification_permitted": False,
            "evidence_gaps": result["data"].get("evidence_gaps", []),
            "evidence_questions": result["data"].get("evidence_questions", []),
            "assessment": None,
            "legal_conclusion": None,
            "enforcement_conclusion": None,
            "requires_officer_approval": True,
            "decision": None,
            "playbook_version": PLAYBOOK_VERSION,
            "case_id": state["case_id"],
            "run_id": state["run_id"],
            "source_tool": TOOL_NAME,
        }
        update["referral"] = referral
        update["findings"] = [*state["findings"], finding]
        update["requires_human_review"] = True
        return update

    def route(state: SpecialistState) -> str:
        return "refer" if state.get("referral") and not state.get("specialist_opinion") else "end"

    def specialist_checkpoint(state: SpecialistState) -> dict[str, object]:
        referral = state.get("referral")
        if not referral:
            raise ValueError("No referral to review")
        request: dict[str, object] = {
            "type": "specialist_classification_required",
            "referral": referral,
        }
        while True:
            response = interrupt(request)
            try:
                opinion = check_specialist_resume(
                    referral,
                    response.get("opinion") if isinstance(response, Mapping) else None,
                    response.get("actor") if isinstance(response, Mapping) else None,
                )
            except ResumeRejected as rejected:
                request = {**request, "last_rejection": rejected.code}  # stays paused
                continue
            return {"specialist_opinion": opinion}

    builder = StateGraph(SpecialistState)
    builder.add_node("structure_facts", structure_facts)
    builder.add_node("specialist_checkpoint", specialist_checkpoint)
    builder.add_edge(START, "structure_facts")
    builder.add_conditional_edges(
        "structure_facts", route, {"refer": "specialist_checkpoint", "end": END}
    )
    builder.add_edge("specialist_checkpoint", END)
    return builder.compile(checkpointer=checkpointer)


def pending_referral(
    graph: CompiledStateGraph[SpecialistState, None, SpecialistState, SpecialistState],
    config: Mapping[str, object],
) -> SpecialistReferral | None:
    """The referral awaiting a specialist in this thread, if the run is paused."""
    snapshot = graph.get_state(config)
    if not any(task.interrupts for task in snapshot.tasks):
        return None
    referral = snapshot.values.get("referral")
    if not isinstance(referral, dict) or set(SpecialistReferral.__annotations__) - set(referral):
        return None
    return cast(SpecialistReferral, referral)


def resume_with_specialist_opinion(
    graph: CompiledStateGraph[SpecialistState, None, SpecialistState, SpecialistState],
    config: Mapping[str, object],
    opinion: object,
    actor: object,
) -> SpecialistState:
    """Validate first, then resume; a refused attempt leaves the checkpoint untouched."""
    referral = pending_referral(graph, config)
    if referral is None:
        raise ResumeRejected("no_pending_checkpoint")
    check_specialist_resume(referral, opinion, actor)
    return graph.invoke(Command(resume={"opinion": opinion, "actor": actor}), config)
