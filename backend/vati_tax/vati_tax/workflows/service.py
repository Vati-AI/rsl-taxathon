"""Learned-workflow approval, activation and rollback with policy checks (TAX-065).

Frappe-free. ``store.py`` binds the ``WorkflowStore`` port to Frappe; tests use
``MemoryWorkflows``. Every step and every refusal is sealed into the TAX-059
audit ledger under subject type ``workflow``.

- ``propose_workflow``: staff or the TAX-064 observer's service identity records
  a proposal as a new version (``workflow.propose``). Recording approves nothing.
- ``decide_workflow``: a supervisor who did not record it approves or rejects
  (``playbook.approve``, separation of duties), with a reason; approval also
  needs ``holdout_reviewed: true``.
- ``activate_workflow``: a supervisor activates an approved version
  (``workflow.activate``); the history entry keeps a rollback reference.
- ``rollback_workflow``: a supervisor re-activates the previous approved
  version (``workflow.activate``), with a reason.
- ``check_execution``: before running a learned workflow, the caller asks
  whether that version may run (``workflow.read``). Only the active approved
  version may; every refusal is recorded.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, NoReturn, Protocol, TypeVar

from vati_tax.audit import ledger
from vati_tax.audit import service as audit
from vati_tax.audit.ledger import LedgerStore
from vati_tax.identity import policy
from vati_tax.ids.canonical import IdempotencyConflict, InvalidMapping, MappingError
from vati_tax.workflows import rules
from vati_tax.workflows.rules import Activation, WorkflowVersion

T = TypeVar("T")
SUBJECT = "workflow"


class WorkflowStore(Protocol):
    def atomic(self) -> contextlib.AbstractContextManager[None]: ...

    def get_version(
        self, tenant_id: str, workflow: str, version: str
    ) -> WorkflowVersion | None: ...

    def versions(self, tenant_id: str, workflow: str) -> dict[str, WorkflowVersion]: ...

    def insert_version(self, record: WorkflowVersion) -> None:
        """Store a new version; raise ``ledger.Duplicate`` if (tenant, workflow, version) exists."""
        ...

    def update_version(self, record: WorkflowVersion, expected: int) -> bool:
        """Replace the stored version if it is still at record version ``expected``."""
        ...

    def history(self, tenant_id: str, workflow: str) -> list[Activation]: ...

    def append_activation(self, entry: Activation) -> None:
        """Append; raise ``ledger.Duplicate`` if (tenant, workflow, seq) exists."""
        ...


@dataclass(frozen=True)
class Ports:
    workflows: WorkflowStore
    ledger: LedgerStore


# --- ledger helpers -----------------------------------------------------------------


def _append(
    ctx: audit.Context,
    ports: Ports,
    *,
    action: str,
    outcome: str,
    subject_id: str,
    payload: Any,
    privilege: str,
    reason: str | None = None,
) -> None:
    ledger.append(
        ports.ledger,
        ledger.Unsealed(
            event_id=ledger.new_event_id(),
            tenant_id=ctx.scope.tenant_id,
            action=action,
            outcome=outcome,
            actor=ctx.scope.user,
            actor_role=audit.acting_role(ctx.scope, privilege),
            occurred_at=ctx.now,
            correlation_id=ctx.correlation_id,
            subject_type=SUBJECT,
            subject_id=subject_id,
            payload_hash=ledger.digest(payload),
            reason=reason,
        ),
    )


def _refuse(
    ctx: audit.Context, ports: Ports, *, attempted: str, subject_id: str, exc: MappingError
) -> NoReturn:
    audit.record_denied(
        ctx,
        ports.ledger,
        attempted_action=attempted,
        subject_type=SUBJECT,
        subject_id=subject_id,
        code=exc.field_errors[0]["code"] if exc.field_errors else exc.code,
    )
    raise exc


def _authorise(
    ctx: audit.Context, ports: Ports, *, action: str, subject_id: str, fn: Callable[[], T]
) -> T:
    return audit.guarded(
        ctx,
        ports.ledger,
        attempted_action=action,
        subject_type=SUBJECT,
        subject_id=subject_id,
        fn=fn,
    )


def _names(workflow: object, version: object | None = None) -> tuple[str, str | None]:
    if not isinstance(workflow, str) or not rules.WORKFLOW_NAME.match(workflow):
        raise rules.WorkflowNotFound("Workflow not found.", "workflow", "not_found")
    if version is None:
        return workflow, None
    if not isinstance(version, str) or not rules.SEMVER.match(version):
        raise rules.WorkflowNotFound("Workflow version not found.", "version", "not_found")
    return workflow, version


def _load(ctx: audit.Context, ports: Ports, workflow: str, version: str) -> WorkflowVersion:
    record = ports.workflows.get_version(ctx.scope.tenant_id, workflow, version)
    if record is None or record.tenant_id != ctx.scope.tenant_id:
        raise rules.WorkflowNotFound("Workflow version not found.", "version", "not_found")
    return record


# --- propose ------------------------------------------------------------------------


def propose_workflow(
    ctx: audit.Context, ports: Ports, *, body: Mapping[str, Any], idempotency_key: str
) -> dict[str, Any]:
    intake = rules.ProposalIntake.build(body, idempotency_key)
    subject = rules.version_uuid(ctx.scope.tenant_id, intake.workflow, intake.version)
    _authorise(
        ctx,
        ports,
        action=policy.WORKFLOW_PROPOSE,
        subject_id=subject,
        fn=lambda: policy.authorize_action(ctx.scope, policy.WORKFLOW_PROPOSE),
    )
    existing = ports.workflows.get_version(ctx.scope.tenant_id, intake.workflow, intake.version)
    current = rules.active(ports.workflows.history(ctx.scope.tenant_id, intake.workflow))
    if existing is not None:
        if (
            existing.request_digest == intake.request_digest()
            and existing.proposed_by.lower() == ctx.scope.user.lower()
        ):
            return {**rules.version_view(existing, current), "replayed": True}
        raise IdempotencyConflict(
            "This workflow version was already recorded with different content.",
            "version",
            "idempotency_conflict",
        )
    record = WorkflowVersion(
        tenant_id=ctx.scope.tenant_id,
        workflow=intake.workflow,
        version=intake.version,
        steps=intake.steps,
        proposal_id=intake.proposal_id,
        observer_version=intake.observer_version,
        proposal_digest=intake.proposal_digest,
        holdout=intake.holdout,
        request_digest=intake.request_digest(),
        proposed_by=ctx.scope.user,
        proposed_at=rules.utc(ctx.now),
    )
    with ports.workflows.atomic():
        try:
            ports.workflows.insert_version(record)
        except ledger.Duplicate:
            raise rules.WorkflowConflict(
                "This workflow version was recorded concurrently.", "version", "duplicate"
            ) from None
        _append(
            ctx,
            ports,
            action="workflow.proposed",
            outcome="proposed",
            subject_id=subject,
            payload={
                "workflow": record.workflow,
                "version": record.version,
                "proposal_digest": record.proposal_digest,
                "steps": list(record.steps),
            },
            privilege=policy.WORKFLOW_PROPOSE,
        )
    return {**rules.version_view(record, current), "replayed": False}


# --- decide -------------------------------------------------------------------------


def decide_workflow(
    ctx: audit.Context,
    ports: Ports,
    *,
    workflow: object,
    version: object,
    expected_version: object,
    approve: bool,
    reason: object,
    holdout_reviewed: object = False,
) -> dict[str, Any]:
    name, ver = _names(workflow, version)
    assert ver is not None
    record = _load(ctx, ports, name, ver)
    subject = rules.version_uuid(ctx.scope.tenant_id, name, ver)
    _authorise(
        ctx,
        ports,
        action=policy.PLAYBOOK_APPROVE,
        subject_id=subject,
        fn=lambda: policy.authorize_action(
            ctx.scope, policy.PLAYBOOK_APPROVE, proposed_by=record.proposed_by
        ),
    )
    text = rules.reason_text(reason)
    try:
        rules.check_decidable(record, expected_version, holdout_reviewed, approve)
    except MappingError as exc:
        _refuse(ctx, ports, attempted=policy.PLAYBOOK_APPROVE, subject_id=subject, exc=exc)
    decided = replace(
        record,
        status="approved" if approve else "rejected",
        record_version=record.record_version + 1,
        decided_by=ctx.scope.user,
        decided_at=rules.utc(ctx.now),
        decision_reason=text,
    )
    with ports.workflows.atomic():
        if not ports.workflows.update_version(decided, record.record_version):
            raise rules.WorkflowConflict(
                "The version changed since you loaded it.", "expected_version", "stale_version"
            )
        _append(
            ctx,
            ports,
            action="workflow.approved" if approve else "workflow.rejected",
            outcome=decided.status,
            subject_id=subject,
            payload={
                "workflow": name,
                "version": ver,
                "proposal_digest": record.proposal_digest,
                "holdout_reviewed": bool(holdout_reviewed),
            },
            privilege=policy.PLAYBOOK_APPROVE,
            reason=text,
        )
    current = rules.active(ports.workflows.history(ctx.scope.tenant_id, name))
    return rules.version_view(decided, current)


# --- activate and roll back ---------------------------------------------------------


def _record_activation(ctx: audit.Context, ports: Ports, entry: Activation, action: str) -> None:
    with ports.workflows.atomic():
        try:
            ports.workflows.append_activation(entry)
        except ledger.Duplicate:
            raise rules.WorkflowConflict(
                "The workflow's activation changed since you loaded it.",
                "expected_seq",
                "stale_activation",
            ) from None
        _append(
            ctx,
            ports,
            action=action,
            outcome=entry.kind,
            subject_id=rules.workflow_uuid(ctx.scope.tenant_id, entry.workflow),
            payload=rules.activation_view(entry),
            privilege=policy.WORKFLOW_ACTIVATE,
            reason=entry.reason,
        )


def activate_workflow(
    ctx: audit.Context,
    ports: Ports,
    *,
    workflow: object,
    version: object,
    expected_seq: object,
    reason: object,
) -> dict[str, Any]:
    name, ver = _names(workflow, version)
    assert ver is not None
    subject = rules.workflow_uuid(ctx.scope.tenant_id, name)
    _authorise(
        ctx,
        ports,
        action=policy.WORKFLOW_ACTIVATE,
        subject_id=subject,
        fn=lambda: policy.authorize_action(ctx.scope, policy.WORKFLOW_ACTIVATE),
    )
    record = _load(ctx, ports, name, ver)
    text = rules.reason_text(reason)
    history = ports.workflows.history(ctx.scope.tenant_id, name)
    try:
        current = rules.check_activatable(record, history, expected_seq)
    except MappingError as exc:
        _refuse(ctx, ports, attempted=policy.WORKFLOW_ACTIVATE, subject_id=subject, exc=exc)
    entry = Activation(
        tenant_id=ctx.scope.tenant_id,
        workflow=name,
        seq=(current.seq if current else 0) + 1,
        kind="activate",
        version=ver,
        replaced_version=current.version if current else None,
        rollback_to=current.version if current else None,
        activated_by=ctx.scope.user,
        activated_at=rules.utc(ctx.now),
        reason=text,
    )
    _record_activation(ctx, ports, entry, "workflow.activated")
    return rules.activation_view(entry)


def rollback_workflow(
    ctx: audit.Context, ports: Ports, *, workflow: object, expected_seq: object, reason: object
) -> dict[str, Any]:
    name, _ = _names(workflow)
    subject = rules.workflow_uuid(ctx.scope.tenant_id, name)
    _authorise(
        ctx,
        ports,
        action=policy.WORKFLOW_ACTIVATE,
        subject_id=subject,
        fn=lambda: policy.authorize_action(ctx.scope, policy.WORKFLOW_ACTIVATE),
    )
    text = rules.reason_text(reason)
    history = ports.workflows.history(ctx.scope.tenant_id, name)
    versions = ports.workflows.versions(ctx.scope.tenant_id, name)
    try:
        if isinstance(expected_seq, bool) or not isinstance(expected_seq, int):
            raise InvalidMapping("expected_seq must be an integer.", "expected_seq", "required")
        current, target, onward = rules.rollback_target(history, versions)
        if expected_seq != current.seq:
            raise rules.WorkflowConflict(
                "The workflow's activation changed since you loaded it.",
                "expected_seq",
                "stale_activation",
            )
    except MappingError as exc:
        _refuse(ctx, ports, attempted=policy.WORKFLOW_ACTIVATE, subject_id=subject, exc=exc)
    entry = Activation(
        tenant_id=ctx.scope.tenant_id,
        workflow=name,
        seq=current.seq + 1,
        kind="rollback",
        version=target.version,
        replaced_version=current.version,
        rollback_to=onward,
        activated_by=ctx.scope.user,
        activated_at=rules.utc(ctx.now),
        reason=text,
    )
    _record_activation(ctx, ports, entry, "workflow.rolled_back")
    return rules.activation_view(entry)


# --- read and execution check -------------------------------------------------------


def get_workflow(ctx: audit.Context, ports: Ports, *, workflow: object) -> dict[str, Any]:
    name, _ = _names(workflow)
    subject = rules.workflow_uuid(ctx.scope.tenant_id, name)
    _authorise(
        ctx,
        ports,
        action=policy.WORKFLOW_READ,
        subject_id=subject,
        fn=lambda: policy.authorize_action(ctx.scope, policy.WORKFLOW_READ),
    )
    versions = ports.workflows.versions(ctx.scope.tenant_id, name)
    if not versions:
        raise rules.WorkflowNotFound("Workflow not found.", "workflow", "not_found")
    history = ports.workflows.history(ctx.scope.tenant_id, name)
    current = rules.active(history)
    ordered = sorted(versions.values(), key=lambda v: rules.version_tuple(v.version))
    return {
        "tenant_id": ctx.scope.tenant_id,
        "workflow": name,
        "active_version": current.version if current else None,
        "activation_seq": current.seq if current else 0,
        "versions": [rules.version_view(v, current) for v in ordered],
        "history": [rules.activation_view(a) for a in sorted(history, key=lambda a: a.seq)],
    }


def check_execution(
    ctx: audit.Context, ports: Ports, *, workflow: object, version: object
) -> dict[str, Any]:
    """Permit a run only of the active, approved version. Refusals are recorded."""
    name, ver = _names(workflow, version)
    assert ver is not None
    subject = rules.version_uuid(ctx.scope.tenant_id, name, ver)
    _authorise(
        ctx,
        ports,
        action=policy.WORKFLOW_READ,
        subject_id=subject,
        fn=lambda: policy.authorize_action(ctx.scope, policy.WORKFLOW_READ),
    )
    versions = ports.workflows.versions(ctx.scope.tenant_id, name)
    history = ports.workflows.history(ctx.scope.tenant_id, name)
    try:
        record = rules.check_executable(history, versions, ver)
    except MappingError as exc:
        _refuse(ctx, ports, attempted="workflow.execute", subject_id=subject, exc=exc)
    current = rules.active(history)
    return {
        "workflow": name,
        "version": ver,
        "executable": True,
        "steps": list(record.steps),
        "activation_seq": current.seq if current else 0,
        "proposal_digest": record.proposal_digest,
        "notice": "Each step still needs its own officer approval; a learned workflow "
        "orders approved actions and never performs them automatically.",
    }


# --- in-memory port -----------------------------------------------------------------


@dataclass
class MemoryWorkflows:
    rows: dict[tuple[str, str, str], WorkflowVersion] = field(default_factory=dict)
    activations: list[Activation] = field(default_factory=list)

    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        rows, acts = dict(self.rows), list(self.activations)
        try:
            yield
        except BaseException:
            self.rows, self.activations = rows, acts
            raise

    def get_version(self, tenant_id: str, workflow: str, version: str) -> WorkflowVersion | None:
        return self.rows.get((tenant_id, workflow, version))

    def versions(self, tenant_id: str, workflow: str) -> dict[str, WorkflowVersion]:
        return {v: r for (t, w, v), r in self.rows.items() if t == tenant_id and w == workflow}

    def insert_version(self, record: WorkflowVersion) -> None:
        k = (record.tenant_id, record.workflow, record.version)
        if k in self.rows:
            raise ledger.Duplicate("version_key")
        self.rows[k] = record

    def update_version(self, record: WorkflowVersion, expected: int) -> bool:
        k = (record.tenant_id, record.workflow, record.version)
        current = self.rows.get(k)
        if current is None or current.record_version != expected:
            return False
        self.rows[k] = record
        return True

    def history(self, tenant_id: str, workflow: str) -> list[Activation]:
        return [a for a in self.activations if a.tenant_id == tenant_id and a.workflow == workflow]

    def append_activation(self, entry: Activation) -> None:
        if any(
            a.tenant_id == entry.tenant_id and a.workflow == entry.workflow and a.seq == entry.seq
            for a in self.activations
        ):
            raise ledger.Duplicate("activation_key")
        self.activations.append(entry)
