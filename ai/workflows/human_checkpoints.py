"""Persistent checkpoints and authenticated human resume for consequential actions (TAX-047).

A run drafts one consequential action, persists its state in a SQLite checkpoint
and interrupts before anything leaves the worker. It resumes only with an
approved TAX-046 decision presented by the authenticated supervisor who
approved it, bound to the same tenant, case, draft, case version and SHA-256
digest of the exact action payload. Anything else is refused and the run stays
paused at its checkpoint.

After approval the action goes through an injected ``ApprovedActionPort`` with a
deterministic idempotency key, so a worker that restarts and re-executes the
node cannot apply the action twice. The worker never calls Jube or Frappe
directly; the Vati integration layer implements the port (ADR-001, ADR-013).
"""

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, NotRequired, Protocol, TypedDict, cast

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from ai.workflows.state import InvestigationState

CHECKPOINT_VERSION = "tax047-checkpoints-v1"
APPROVER_ROLE = "supervisor"
# TAX-046 decision outcomes; assessment, refund, payment, penalty, fraud and
# prosecution actions do not exist here and are refused.
ALLOWED_ACTIONS = frozenset(
    {
        "request_further_evidence",
        "continue_investigation",
        "escalate_to_supervisor",
        "close_no_further_action",
        "close_after_correction",
    }
)


class PendingAction(TypedDict):
    action_id: str
    kind: str
    tenant_id: str
    case_id: str
    run_id: str
    case_version: int
    payload: dict[str, object]
    payload_digest: str
    proposed_by: str


class Approval(TypedDict):
    """An approved TAX-046 decision as delivered by the Vati integration layer."""

    decision_id: str
    status: str
    tenant_id: str
    case_id: str
    draft_id: str
    outcome: str
    case_version: int
    payload_digest: str
    proposed_by: str
    approved_by: str
    approver_role: str


class AuthenticatedActor(TypedDict):
    """The caller as authenticated by the Vati session layer, not by this worker."""

    actor_id: str
    tenant_id: str
    roles: list[str]
    authenticated: bool


class ActionResult(TypedDict):
    status: Literal["applied", "failed"]
    idempotency_key: str
    decision_id: str
    detail: dict[str, object]


class CheckpointedState(InvestigationState):
    case_version: int
    pending_action: NotRequired[PendingAction | None]
    approval: NotRequired[Approval | None]
    action_result: NotRequired[ActionResult | None]


class ApprovedActionPort(Protocol):
    """Applies an approved action exactly once per idempotency key."""

    def apply(
        self, idempotency_key: str, action: PendingAction, approval: Approval
    ) -> dict[str, object]: ...


ActionProposer = Callable[[CheckpointedState], tuple[str, dict[str, object]]]


class ResumeRejected(Exception):
    """A resume attempt was refused; the run stays paused. Carries a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def action_digest(payload: Mapping[str, object]) -> str:
    """Canonical SHA-256 of an action payload (same encoding as TAX-046 ``digest``)."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def idempotency_key(action: PendingAction, approval: Approval) -> str:
    """Deterministic key: re-executing the node after a restart reuses it."""
    parts = (
        CHECKPOINT_VERSION,
        action["tenant_id"],
        action["case_id"],
        action["run_id"],
        action["action_id"],
        approval["decision_id"],
        action["payload_digest"],
    )
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def thread_config(tenant_id: str, case_id: str, run_id: str) -> dict[str, dict[str, str]]:
    """Checkpoint thread scoped to one tenant, case and run."""
    for name, value in (("tenant_id", tenant_id), ("case_id", case_id), ("run_id", run_id)):
        if not isinstance(value, str) or not value.strip() or "\x1f" in value:
            raise ValueError(f"{name} must be a non-empty string")
    return {"configurable": {"thread_id": f"{tenant_id}\x1f{case_id}\x1f{run_id}"}}


def open_checkpointer(path: Path) -> SqliteSaver:
    """Open a persistent SQLite checkpoint store at an explicit file path."""
    if not isinstance(path, Path) or str(path) in {"", ":memory:"}:
        raise ValueError("A checkpoint file path is required for persistent runs")
    path.parent.mkdir(parents=True, exist_ok=True)
    saver = SqliteSaver(sqlite3.connect(str(path), check_same_thread=False))
    saver.setup()
    return saver


def check_resume(pending: PendingAction, approval: object, actor: object) -> Approval:
    """Validate that ``actor`` may resume ``pending`` with ``approval``; raise otherwise."""
    if not isinstance(actor, Mapping) or actor.get("authenticated") is not True:
        raise ResumeRejected("not_authenticated")
    if not isinstance(approval, Mapping):
        raise ResumeRejected("invalid_approval")
    for source, fields in (
        (actor, ("actor_id", "tenant_id")),
        (approval, ("decision_id", "approved_by", "proposed_by", "payload_digest")),
    ):
        for field in fields:
            value = source.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ResumeRejected("missing_identity")
    roles = actor.get("roles")
    checks: tuple[tuple[bool, str], ...] = (
        (actor.get("tenant_id") == pending["tenant_id"], "tenant_mismatch"),
        (approval.get("tenant_id") == pending["tenant_id"], "tenant_mismatch"),
        (approval.get("case_id") == pending["case_id"], "case_mismatch"),
        (approval.get("status") == "approved", "decision_not_approved"),
        (approval.get("draft_id") == pending["action_id"], "draft_mismatch"),
        (approval.get("outcome") == pending["kind"], "outcome_mismatch"),
        (
            isinstance(roles, list)
            and APPROVER_ROLE in roles
            and approval.get("approver_role") == APPROVER_ROLE,
            "role_not_permitted",
        ),
        (actor.get("actor_id") == approval.get("approved_by"), "actor_not_approver"),
        (
            approval.get("approved_by")
            not in (approval.get("proposed_by"), pending["proposed_by"]),
            "self_approval",
        ),
        (approval.get("case_version") == pending["case_version"], "stale_case_version"),
        (
            approval.get("payload_digest") == pending["payload_digest"]
            and action_digest(pending["payload"]) == pending["payload_digest"],
            "payload_mismatch",
        ),
    )
    for passed, code in checks:
        if not passed:
            raise ResumeRejected(code)
    return {
        "decision_id": str(approval.get("decision_id")),
        "status": "approved",
        "tenant_id": pending["tenant_id"],
        "case_id": pending["case_id"],
        "draft_id": pending["action_id"],
        "outcome": pending["kind"],
        "case_version": pending["case_version"],
        "payload_digest": pending["payload_digest"],
        "proposed_by": str(approval.get("proposed_by")),
        "approved_by": str(approval.get("approved_by")),
        "approver_role": APPROVER_ROLE,
    }


def build_checkpointed_graph(
    proposer: ActionProposer,
    port: ApprovedActionPort,
    checkpointer: SqliteSaver,
    *,
    agent_id: str,
) -> CompiledStateGraph[CheckpointedState, None, CheckpointedState, CheckpointedState]:
    """Compile draft_action -> await_approval (interrupt) -> apply_approved_action."""

    def draft_action(state: CheckpointedState) -> dict[str, object]:
        if state.get("pending_action"):
            return {}  # replay keeps the original draft and its digest
        kind, payload = proposer(state)
        if kind not in ALLOWED_ACTIONS:
            raise ValueError("Proposed action is not allowlisted")
        pending: PendingAction = {
            "action_id": f"draft-{state['run_id']}",
            "kind": kind,
            "tenant_id": state["tenant_id"],
            "case_id": state["case_id"],
            "run_id": state["run_id"],
            "case_version": state["case_version"],
            "payload": payload,
            "payload_digest": action_digest(payload),
            "proposed_by": agent_id,
        }
        return {"pending_action": pending, "requires_human_review": True}

    def await_approval(state: CheckpointedState) -> dict[str, object]:
        pending = state.get("pending_action")
        if not pending:
            raise ValueError("No drafted action to approve")
        request: dict[str, object] = {"type": "approval_required", "pending_action": pending}
        while True:
            response = interrupt(request)
            try:
                approval = check_resume(
                    pending,
                    response.get("approval") if isinstance(response, Mapping) else None,
                    response.get("actor") if isinstance(response, Mapping) else None,
                )
            except ResumeRejected as rejected:
                # Defense in depth for direct Command(resume=...) calls: pause again.
                request = {**request, "last_rejection": rejected.code}
                continue
            return {"approval": approval}

    def apply_approved_action(state: CheckpointedState) -> dict[str, object]:
        pending = state.get("pending_action")
        approval = state.get("approval")
        if not pending or not approval:
            raise ValueError("Cannot apply an action without an approval")
        key = idempotency_key(pending, approval)
        try:
            detail = port.apply(key, pending, approval)
            status: Literal["applied", "failed"] = "applied"
        except Exception as error:  # recorded as a failure, never as success
            detail = {"error": type(error).__name__}
            status = "failed"
        result: ActionResult = {
            "status": status,
            "idempotency_key": key,
            "decision_id": approval["decision_id"],
            "detail": dict(detail),
        }
        return {
            "action_result": result,
            "step_count": state["step_count"] + 1,
            "requires_human_review": status == "failed",
        }

    builder = StateGraph(CheckpointedState)
    builder.add_node("draft_action", draft_action)
    builder.add_node("await_approval", await_approval)
    builder.add_node("apply_approved_action", apply_approved_action)
    builder.add_edge(START, "draft_action")
    builder.add_edge("draft_action", "await_approval")
    builder.add_edge("await_approval", "apply_approved_action")
    builder.add_edge("apply_approved_action", END)
    return builder.compile(checkpointer=checkpointer)


def pending_action(
    graph: CompiledStateGraph[CheckpointedState, None, CheckpointedState, CheckpointedState],
    config: Mapping[str, object],
) -> PendingAction | None:
    """Return the action waiting for approval in this thread, if the run is paused."""
    snapshot = graph.get_state(config)
    if not any(task.interrupts for task in snapshot.tasks):
        return None
    pending = snapshot.values.get("pending_action")
    if not isinstance(pending, dict) or set(PendingAction.__annotations__) - set(pending):
        return None
    return cast(PendingAction, pending)


def resume_with_approval(
    graph: CompiledStateGraph[CheckpointedState, None, CheckpointedState, CheckpointedState],
    config: Mapping[str, object],
    approval: object,
    actor: object,
) -> CheckpointedState:
    """Validate first, then resume; a refused attempt leaves the checkpoint untouched."""
    pending = pending_action(graph, config)
    if pending is None:
        raise ResumeRejected("no_pending_checkpoint")
    check_resume(pending, approval, actor)
    return graph.invoke(
        Command(resume={"approval": approval, "actor": actor}),
        config,
    )


def recover_run(
    graph: CompiledStateGraph[CheckpointedState, None, CheckpointedState, CheckpointedState],
    config: Mapping[str, object],
) -> CheckpointedState:
    """Continue a run a crashed worker left mid-step; never skips a pending approval."""
    snapshot = graph.get_state(config)
    if not snapshot.next:
        raise ResumeRejected("nothing_to_recover")
    if any(task.interrupts for task in snapshot.tasks):
        raise ResumeRejected("approval_required")
    return graph.invoke(None, config)
