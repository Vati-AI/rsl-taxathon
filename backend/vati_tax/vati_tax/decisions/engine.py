"""Decision and evidence-request workflow (TAX-046), independent of Frappe.

``store`` binds ``Repository`` to Frappe DocTypes; tests bind it to memory.
``CaseLifecycle`` is the shape of ``integration/jube/case_lifecycle.JubeCaseAdapter``
(TAX-034): this module never talks to Jube directly and never stores a case
status of its own (ADR-001). The status it records is what Jube returned.

Order of effects (ADR-001: approval records precede idempotent calls into Jube):

1. The approval and its audit event are written.
2. The Jube operation runs once, through the adapter, with the status the
   decision was approved against as ``expected_status``.
3. The result is written back: ``synced``, or ``failed`` with an error code and
   whether a retry is allowed. A failed call is never shown as applied.

The adapter is replay-safe (it sends nothing when Jube already holds the
target), so a retry after a lost response cannot apply an action twice.
"""

from __future__ import annotations

import contextlib
import datetime
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal, Protocol

from vati_tax.audit import ledger
from vati_tax.audit.ledger import MemoryLedger
from vati_tax.decisions import rules
from vati_tax.decisions.rules import (
    DecisionNotFound,
    DecisionRefused,
    UpstreamUnavailable,
    VersionConflict,
)
from vati_tax.identity import policy
from vati_tax.ids.canonical import CaseNotFound, IdempotencyConflict, canonical_uuid

# Authorised actions that change what a reviewer sees on the case. Each takes
# the next case version, so a reviewer acting on an older view is refused.
ADVANCING = frozenset(
    {
        "decision.proposed",
        "decision.approved",
        "decision.rejected",
        "decision.withdrawn",
        "evidence_request.recorded",
    }
)


class Duplicate(Exception):
    """The repository refused a write because a unique key is already taken."""


# --- records ------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseRef:
    tenant_id: str
    case_id: str
    taxpayer_id: str | None


@dataclass
class DecisionRecord:
    decision_id: str
    tenant_id: str
    case_id: str
    taxpayer_id: str | None
    status: str
    origin: str
    outcome: str
    rationale: str
    evidence_ids: tuple[str, ...]
    finding_ids: tuple[str, ...]
    rating: int | None
    draft_id: str | None
    proposed_by: str
    proposer_role: str
    proposed_at: datetime.datetime
    case_status_at_proposal: str | None
    case_version: int | None
    registration_key: str
    request_digest: str
    open_key: str | None = None
    reviewed_by: str | None = None
    reviewer_role: str | None = None
    reviewed_at: datetime.datetime | None = None
    review_reason: str | None = None
    review_key: str | None = None
    review_digest: str | None = None
    sync_state: str = "not_started"
    sync_attempts: int = 0
    sync_error_code: str | None = None
    sync_retryable: bool = False
    last_attempt_at: datetime.datetime | None = None
    synced_at: datetime.datetime | None = None
    jube_changed: bool | None = None
    jube_status_after: str | None = None


@dataclass
class EvidenceRequestRecord:
    request_id: str
    tenant_id: str
    case_id: str
    taxpayer_id: str
    status: str
    authorised_by: str
    authoriser_role: str
    finding_ids: tuple[str, ...]
    items: tuple[rules.RequestedItem, ...]
    due_date: datetime.date
    message_to_taxpayer: str
    case_version: int
    created_at: datetime.datetime
    registration_key: str
    request_digest: str


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    tenant_id: str
    case_id: str
    taxpayer_id: str | None
    sequence: int | None
    actor: str
    actor_role: str
    action: str
    subject_type: Literal["decision", "evidence_request"]
    subject_id: str
    occurred_at: datetime.datetime
    correlation_id: str
    payload_hash: str
    outcome: str


# --- ports --------------------------------------------------------------------------


class Repository(Protocol):
    def atomic(self) -> AbstractContextManager[None]: ...

    def get_case(self, tenant_id: str, case_id: str) -> CaseRef | None: ...

    def case_version(self, tenant_id: str, case_id: str) -> int: ...

    def known_evidence(
        self, tenant_id: str, taxpayer_id: str | None, evidence_ids: tuple[str, ...]
    ) -> set[str]: ...

    def get_decision(self, decision_id: str) -> DecisionRecord | None: ...

    def decision_by_key(self, registration_key: str) -> DecisionRecord | None: ...

    def decision_by_review_key(self, review_key: str) -> DecisionRecord | None: ...

    def open_decision(self, tenant_id: str, case_id: str) -> DecisionRecord | None: ...

    def insert_decision(self, record: DecisionRecord) -> None: ...

    def save_decision(self, record: DecisionRecord) -> None: ...

    def request_by_key(self, registration_key: str) -> EvidenceRequestRecord | None: ...

    def insert_request(self, record: EvidenceRequestRecord) -> None: ...

    def append_event(self, event: AuditEvent) -> None: ...

    def case_events(self, tenant_id: str, case_id: str) -> list[AuditEvent]: ...


class CaseLifecycle(Protocol):
    """``JubeCaseAdapter`` (integration/jube/case_lifecycle.py), as far as decisions use it."""

    def get_case(self, tenant_id: str, case_id: str, *, correlation_id: str) -> Any: ...

    def transition(
        self,
        tenant_id: str,
        case_id: str,
        target_status: str,
        *,
        expected_status: str,
        correlation_id: str,
        decision_id: str | None = None,
        rating: int | None = None,
    ) -> Any: ...

    def escalate(
        self,
        tenant_id: str,
        case_id: str,
        *,
        expected_status: str,
        correlation_id: str,
        rating: int | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class Context:
    scope: policy.TenantScope
    now: datetime.datetime
    correlation_id: str

    @classmethod
    def build(
        cls, scope: policy.TenantScope, correlation_id: str | None, now: datetime.datetime
    ) -> Context:
        corr = canonical_uuid(correlation_id, "correlation_id") if correlation_id else None
        return cls(scope=scope, now=now, correlation_id=corr or str(uuid.uuid4()))


# --- helpers ------------------------------------------------------------------------


def _acting_role(scope: policy.TenantScope, action: str) -> str:
    roles = scope.roles & policy.ACTION_ROLES[action]
    return sorted(roles)[0]


def _authorize(ctx: Context, action: str, *, proposed_by: str | None = None) -> str:
    policy.authorize_action(ctx.scope, action, proposed_by=proposed_by)
    return _acting_role(ctx.scope, action)


def _case(repo: Repository, tenant_id: str, case_id: str) -> CaseRef:
    case = repo.get_case(tenant_id, case_id)
    if case is None or case.tenant_id != tenant_id:
        raise CaseNotFound("Case not found.", "case_id", "not_found")
    return case


def _decision(repo: Repository, tenant_id: str, decision_id: str) -> DecisionRecord:
    record = repo.get_decision(canonical_uuid(decision_id, "decision_id"))
    if record is None or record.tenant_id != tenant_id:
        raise DecisionNotFound("Decision not found.", "decision_id", "not_found")
    return record


def _check_version(repo: Repository, tenant_id: str, case_id: str, expected: int) -> int:
    current = repo.case_version(tenant_id, case_id)
    if expected != current:
        raise VersionConflict(
            "The case changed since you loaded it; reload and try again.",
            "expected_version",
            "stale_case_version",
            current_version=current,
        )
    return current


def _jube_status(lifecycle: CaseLifecycle, ctx: Context, case_id: str) -> str:
    """The case status as Jube holds it now. Nothing is decided without it."""
    try:
        case = lifecycle.get_case(ctx.scope.tenant_id, case_id, correlation_id=ctx.correlation_id)
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code == "not_found":
            raise DecisionRefused(
                "The case has no Investigation case yet.", "case_id", "no_jube_case"
            ) from None
        if code is None and not isinstance(exc, OSError):
            raise
        raise UpstreamUnavailable(
            "Investigation could not be reached to confirm the case status.",
            "case_id",
            "jube_unavailable",
        ) from None
    return str(case.status)


def _event(
    ctx: Context,
    *,
    case: CaseRef | DecisionRecord | EvidenceRequestRecord,
    action: str,
    role: str,
    subject_type: Literal["decision", "evidence_request"],
    subject_id: str,
    outcome: str,
    sequence: int | None,
    payload: dict[str, Any],
    actor: str | None = None,
) -> AuditEvent:
    if (sequence is None) == (action in ADVANCING):
        raise AssertionError(f"{action}: advancing events need a sequence, others none")
    return AuditEvent(
        event_id=str(uuid.uuid4()),
        tenant_id=case.tenant_id,
        case_id=case.case_id,
        taxpayer_id=case.taxpayer_id,
        sequence=sequence,
        actor=actor or ctx.scope.user,
        actor_role=role,
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        occurred_at=ctx.now,
        correlation_id=ctx.correlation_id,
        payload_hash=rules.digest(payload),
        outcome=outcome,
    )


def _replay_decision(
    record: DecisionRecord | None, request_digest: str, stored_digest: str | None
) -> dict[str, Any] | None:
    if record is None:
        return None
    if stored_digest != request_digest:
        raise IdempotencyConflict(
            "This idempotency key was used for a different request.",
            "idempotency_key",
            "idempotency_conflict",
        )
    return decision_view(record, replayed=True)


# --- drafts and proposals -----------------------------------------------------------


def record_draft(
    ctx: Context, repo: Repository, *, case_id: str, body: dict[str, Any], idempotency_key: str
) -> dict[str, Any]:
    """Record an agent's suggested decision. It can never be approved or executed."""
    role = _authorize(ctx, policy.DRAFT_RECORD)
    draft = rules.Draft.build(
        tenant_id=ctx.scope.tenant_id,
        case_id=case_id,
        outcome=body.get("outcome"),
        rationale=body.get("rationale"),
        evidence_ids=body.get("evidence_ids"),
        finding_ids=body.get("finding_ids"),
        idempotency_key=idempotency_key,
    )
    key = rules.operation_key("decision.draft", draft.tenant_id, draft.idempotency_key)
    request_digest = draft.digest()
    existing = repo.decision_by_key(key)
    replay = _replay_decision(
        existing, request_digest, existing.request_digest if existing else None
    )
    if replay is not None:
        return replay
    case = _case(repo, draft.tenant_id, draft.case_id)
    _check_citations(repo, case, draft.evidence_ids)
    record = DecisionRecord(
        decision_id=str(uuid.uuid4()),
        tenant_id=case.tenant_id,
        case_id=case.case_id,
        taxpayer_id=case.taxpayer_id,
        status="draft",
        origin="agent_draft",
        outcome=draft.outcome,
        rationale=draft.rationale,
        evidence_ids=draft.evidence_ids,
        finding_ids=draft.finding_ids,
        rating=None,
        draft_id=None,
        proposed_by=ctx.scope.user,
        proposer_role=role,
        proposed_at=ctx.now,
        case_status_at_proposal=None,
        case_version=None,
        registration_key=key,
        request_digest=request_digest,
    )
    try:
        with repo.atomic():
            repo.insert_decision(record)
            repo.append_event(
                _event(
                    ctx,
                    case=case,
                    action="decision.drafted",
                    role=role,
                    subject_type="decision",
                    subject_id=record.decision_id,
                    outcome="draft",
                    sequence=None,
                    payload={"outcome": record.outcome, "digest": request_digest},
                )
            )
    except Duplicate:
        existing = repo.decision_by_key(key)
        replay = _replay_decision(
            existing, request_digest, existing.request_digest if existing else None
        )
        if replay is None:
            raise
        return replay
    return decision_view(record, replayed=False)


def _check_citations(repo: Repository, case: CaseRef, evidence_ids: tuple[str, ...]) -> None:
    if not evidence_ids:
        return
    known = repo.known_evidence(case.tenant_id, case.taxpayer_id, evidence_ids)
    if set(evidence_ids) - known:
        raise CaseNotFound("Evidence not found.", "evidence_ids", "not_found")


def propose(
    ctx: Context,
    repo: Repository,
    lifecycle: CaseLifecycle,
    *,
    case_id: str,
    body: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    """An officer proposes a decision on a case; it waits for a different approver."""
    role = _authorize(ctx, policy.DECISION_PROPOSE)
    proposal = rules.Proposal.build(
        tenant_id=ctx.scope.tenant_id,
        case_id=case_id,
        outcome=body.get("outcome"),
        rationale=body.get("rationale"),
        evidence_ids=body.get("evidence_ids"),
        finding_ids=body.get("finding_ids"),
        rating=body.get("rating"),
        expected_case_status=body.get("expected_case_status"),
        expected_version=body.get("expected_version"),
        draft_id=body.get("draft_id"),
        idempotency_key=idempotency_key,
    )
    extra_permission = rules.OUTCOME_PERMISSIONS.get(proposal.outcome)
    if extra_permission is not None:
        _authorize(ctx, extra_permission)
    key = rules.operation_key("decision.propose", proposal.tenant_id, proposal.idempotency_key)
    request_digest = proposal.digest()
    existing = repo.decision_by_key(key)
    replay = _replay_decision(
        existing, request_digest, existing.request_digest if existing else None
    )
    if replay is not None:
        return replay
    case = _case(repo, proposal.tenant_id, proposal.case_id)
    _check_citations(repo, case, proposal.evidence_ids)
    if proposal.draft_id is not None:
        draft = repo.get_decision(proposal.draft_id)
        if (
            draft is None
            or draft.tenant_id != case.tenant_id
            or draft.case_id != case.case_id
            or draft.status != "draft"
        ):
            raise DecisionNotFound("Draft not found.", "draft_id", "not_found")
    if repo.open_decision(case.tenant_id, case.case_id) is not None:
        raise VersionConflict(
            "Another decision on this case is still open.", "case_id", "decision_pending"
        )
    version = _check_version(repo, case.tenant_id, case.case_id, proposal.expected_version)
    current = _jube_status(lifecycle, ctx, case.case_id)
    if current != proposal.expected_case_status:
        raise VersionConflict(
            "The case status in Investigation differs from the one you reviewed.",
            "expected_case_status",
            "case_state_changed",
        )
    rule = rules.OUTCOMES[proposal.outcome]
    if current == "RESOLVED":
        raise DecisionRefused("The case is already resolved.", "case_id", "case_closed")
    if rule.operation == "transition" and current == rule.target_status:
        raise DecisionRefused(
            "The case is already in the status this outcome sets.", "outcome", "no_change"
        )
    record = DecisionRecord(
        decision_id=str(uuid.uuid4()),
        tenant_id=case.tenant_id,
        case_id=case.case_id,
        taxpayer_id=case.taxpayer_id,
        status="proposed",
        origin="officer",
        outcome=proposal.outcome,
        rationale=proposal.rationale,
        evidence_ids=proposal.evidence_ids,
        finding_ids=proposal.finding_ids,
        rating=proposal.rating,
        draft_id=proposal.draft_id,
        proposed_by=ctx.scope.user,
        proposer_role=role,
        proposed_at=ctx.now,
        case_status_at_proposal=current,
        case_version=version,
        registration_key=key,
        request_digest=request_digest,
        open_key=rules.open_decision_key(case.tenant_id, case.case_id),
    )
    try:
        with repo.atomic():
            repo.insert_decision(record)
            repo.append_event(
                _event(
                    ctx,
                    case=case,
                    action="decision.proposed",
                    role=role,
                    subject_type="decision",
                    subject_id=record.decision_id,
                    outcome="proposed",
                    sequence=version,
                    payload={"outcome": record.outcome, "digest": request_digest},
                )
            )
    except Duplicate:
        existing = repo.decision_by_key(key)
        replay = _replay_decision(
            existing, request_digest, existing.request_digest if existing else None
        )
        if replay is not None:
            return replay
        raise VersionConflict(
            "Another review action on this case was recorded first; reload and try again.",
            "expected_version",
            "stale_case_version",
            current_version=repo.case_version(case.tenant_id, case.case_id),
        ) from None
    return decision_view(record, replayed=False)


# --- review -------------------------------------------------------------------------


def review(
    ctx: Context,
    repo: Repository,
    lifecycle: CaseLifecycle,
    *,
    action: Literal["approve", "reject", "withdraw"],
    decision_id: str,
    body: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    """Approve, reject or withdraw a proposal. Approval executes it in Jube."""
    request = rules.Review.build(
        action=action,
        decision_id=decision_id,
        expected_version=body.get("expected_version"),
        reason=body.get("reason"),
        idempotency_key=idempotency_key,
    )
    record = _decision(repo, ctx.scope.tenant_id, request.decision_id)
    if action == "withdraw":
        role = _authorize(ctx, policy.DECISION_PROPOSE)
        if record.proposed_by.lower() != ctx.scope.user.lower():
            raise policy.ActionDenied("Only the proposer can withdraw a proposal")
    else:
        # Separation of duties: the approver cannot be the proposer (policy).
        role = _authorize(ctx, policy.DECISION_APPROVE, proposed_by=record.proposed_by)
    key = rules.operation_key("decision.review", record.tenant_id, request.idempotency_key)
    request_digest = request.digest()
    reviewed = repo.decision_by_review_key(key)
    replay = _replay_decision(
        reviewed, request_digest, reviewed.review_digest if reviewed else None
    )
    if replay is not None:
        return replay
    rules.check_reviewable(
        status=record.status,
        origin=record.origin,
        proposer_role=record.proposer_role,
        action=action,
    )
    version = _check_version(repo, record.tenant_id, record.case_id, request.expected_version)
    if action == "approve":
        rules.check_fresh_proposal(record.proposed_at, ctx.now)
        rules.check_case_unchanged(
            record.case_status_at_proposal or "", _jube_status(lifecycle, ctx, record.case_id)
        )
    new_status = {"approve": "approved", "reject": "rejected", "withdraw": "withdrawn"}[action]
    updated = replace(
        record,
        status=new_status,
        reviewed_by=ctx.scope.user,
        reviewer_role=role,
        reviewed_at=ctx.now,
        review_reason=request.reason,
        review_key=key,
        review_digest=request_digest,
        sync_state="pending" if action == "approve" else record.sync_state,
        open_key=record.open_key if action == "approve" else None,
    )
    try:
        with repo.atomic():
            repo.save_decision(updated)
            repo.append_event(
                _event(
                    ctx,
                    case=updated,
                    action=f"decision.{new_status}",
                    role=role,
                    subject_type="decision",
                    subject_id=updated.decision_id,
                    outcome=new_status,
                    sequence=version,
                    payload={"digest": request_digest, "reason": request.reason},
                )
            )
    except Duplicate:
        raise VersionConflict(
            "Another review action on this case was recorded first; reload and try again.",
            "expected_version",
            "stale_case_version",
            current_version=repo.case_version(record.tenant_id, record.case_id),
        ) from None
    if action == "approve":
        updated = _execute(ctx, repo, lifecycle, updated)
    return decision_view(updated, replayed=False)


# --- execution ----------------------------------------------------------------------


def retry_execution(
    ctx: Context, repo: Repository, lifecycle: CaseLifecycle, *, decision_id: str
) -> dict[str, Any]:
    """Resend an approved decision whose Jube call failed with a retryable error.

    A supervisor or the service identity (outbox, TAX-033) may retry. Retrying
    never approves anything; it only completes an approval already recorded.
    """
    role = _authorize(ctx, policy.DECISION_EXECUTE)
    record = _decision(repo, ctx.scope.tenant_id, decision_id)
    if record.status != "approved":
        rules.check_reviewable(
            status=record.status,
            origin=record.origin,
            proposer_role=record.proposer_role,
            action="execute",
        )
        raise VersionConflict("Only an approved decision runs.", "decision_id", "not_approved")
    if record.sync_state == "synced":
        return decision_view(record, replayed=True)
    if record.sync_state == "failed" and not record.sync_retryable:
        raise VersionConflict(
            "This decision cannot be retried; propose a new decision.",
            "decision_id",
            "not_retryable",
        )
    return decision_view(_execute(ctx, repo, lifecycle, record, actor_role=role), replayed=False)


def _execute(
    ctx: Context,
    repo: Repository,
    lifecycle: CaseLifecycle,
    record: DecisionRecord,
    *,
    actor_role: str | None = None,
) -> DecisionRecord:
    if record.sync_state == "synced":
        return record
    role = actor_role or record.reviewer_role or policy.SUPERVISOR
    if record.reviewed_at is None or rules.approval_expired(record.reviewed_at, ctx.now):
        failed = replace(
            record,
            sync_state="failed",
            sync_error_code="approval_expired",
            sync_retryable=False,
            open_key=None,
            last_attempt_at=ctx.now,
        )
        _settle(ctx, repo, failed, role, "decision.execution_failed", "approval_expired")
        return failed
    rule = rules.OUTCOMES[record.outcome]
    expected = record.case_status_at_proposal or ""
    attempts = record.sync_attempts + 1
    try:
        if rule.operation == "transition":
            result = lifecycle.transition(
                record.tenant_id,
                record.case_id,
                rule.target_status,
                expected_status=expected,
                correlation_id=ctx.correlation_id,
                decision_id=record.decision_id,
                rating=record.rating,
            )
        else:
            result = lifecycle.escalate(
                record.tenant_id,
                record.case_id,
                expected_status=expected,
                correlation_id=ctx.correlation_id,
                rating=record.rating,
            )
    except Exception as exc:
        if getattr(exc, "code", None) is None and not isinstance(exc, OSError):
            raise
        code, retryable = rules.classify_upstream_error(exc)
        failed = replace(
            record,
            sync_state="failed",
            sync_attempts=attempts,
            sync_error_code=code,
            sync_retryable=retryable,
            last_attempt_at=ctx.now,
            open_key=record.open_key if retryable else None,
        )
        _settle(ctx, repo, failed, role, "decision.execution_failed", code)
        return failed
    synced = replace(
        record,
        sync_state="synced",
        sync_attempts=attempts,
        sync_error_code=None,
        sync_retryable=False,
        last_attempt_at=ctx.now,
        synced_at=ctx.now,
        jube_changed=bool(result.changed),
        jube_status_after=str(result.case.status),
        open_key=None,
    )
    _settle(
        ctx, repo, synced, role, "decision.executed", "changed" if result.changed else "replayed"
    )
    return synced


def _settle(
    ctx: Context,
    repo: Repository,
    record: DecisionRecord,
    role: str,
    action: str,
    outcome: str,
) -> None:
    with repo.atomic():
        repo.save_decision(record)
        repo.append_event(
            _event(
                ctx,
                case=record,
                action=action,
                role=role,
                subject_type="decision",
                subject_id=record.decision_id,
                outcome=outcome,
                sequence=None,
                payload={
                    "sync_state": record.sync_state,
                    "error_code": record.sync_error_code,
                    "jube_status_after": record.jube_status_after,
                    "attempt": record.sync_attempts,
                },
            )
        )


# --- evidence requests --------------------------------------------------------------


def authorise_evidence_request(
    ctx: Context,
    repo: Repository,
    *,
    case_id: str,
    body: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    """Record an officer-authorised evidence request for the case's taxpayer.

    Frappe owns the record (ADR-001). Delivery to the portal is TAX-045; moving
    the Jube case to AWAITING_EVIDENCE is a separate approved decision.
    """
    role = _authorize(ctx, policy.EVIDENCE_REQUEST_AUTHORISE)
    data = rules.EvidenceRequestInput.build(
        tenant_id=ctx.scope.tenant_id,
        case_id=case_id,
        body=body,
        idempotency_key=idempotency_key,
        today=ctx.now.date(),
    )
    key = rules.operation_key("evidence_request.authorise", data.tenant_id, data.idempotency_key)
    request_digest = data.digest()
    existing = repo.request_by_key(key)
    if existing is not None:
        if existing.request_digest != request_digest:
            raise IdempotencyConflict(
                "This idempotency key was used for a different request.",
                "idempotency_key",
                "idempotency_conflict",
            )
        return evidence_request_view(existing, replayed=True)
    case = _case(repo, data.tenant_id, data.case_id)
    if not case.taxpayer_id:
        raise DecisionRefused(
            "The case has no taxpayer to request evidence from.", "case_id", "no_taxpayer"
        )
    version = _check_version(repo, case.tenant_id, case.case_id, data.expected_version)
    record = EvidenceRequestRecord(
        request_id=str(uuid.uuid4()),
        tenant_id=case.tenant_id,
        case_id=case.case_id,
        taxpayer_id=case.taxpayer_id,
        status="recorded",
        authorised_by=ctx.scope.user,
        authoriser_role=role,
        finding_ids=data.finding_ids,
        items=data.items,
        due_date=data.due_date,
        message_to_taxpayer=data.message_to_taxpayer,
        case_version=version,
        created_at=ctx.now,
        registration_key=key,
        request_digest=request_digest,
    )
    try:
        with repo.atomic():
            repo.insert_request(record)
            repo.append_event(
                _event(
                    ctx,
                    case=case,
                    action="evidence_request.recorded",
                    role=role,
                    subject_type="evidence_request",
                    subject_id=record.request_id,
                    outcome="recorded",
                    sequence=version,
                    payload={"digest": request_digest},
                )
            )
    except Duplicate:
        existing = repo.request_by_key(key)
        if existing is not None and existing.request_digest == request_digest:
            return evidence_request_view(existing, replayed=True)
        raise VersionConflict(
            "Another review action on this case was recorded first; reload and try again.",
            "expected_version",
            "stale_case_version",
            current_version=repo.case_version(case.tenant_id, case.case_id),
        ) from None
    return evidence_request_view(record, replayed=False)


# --- reads --------------------------------------------------------------------------


def case_review_state(ctx: Context, repo: Repository, *, case_id: str) -> dict[str, Any]:
    """Case version, open decision and audit trail for a reviewer."""
    policy.authorize_action(ctx.scope, policy.CASE_READ)
    case = _case(repo, ctx.scope.tenant_id, canonical_uuid(case_id, "case_id"))
    open_record = repo.open_decision(case.tenant_id, case.case_id)
    return {
        "case_id": case.case_id,
        "tenant_id": case.tenant_id,
        "case_version": repo.case_version(case.tenant_id, case.case_id),
        "viewer": viewer_view(ctx.scope, open_record),
        "open_decision": decision_view(open_record) if open_record else None,
        "audit_events": [event_view(e) for e in repo.case_events(case.tenant_id, case.case_id)],
    }


# Review actions a screen offers. ``decision.approve`` is listed only when an open
# proposal exists that this principal did not make (separation of duties).
VIEWER_ACTIONS = (
    policy.DECISION_PROPOSE,
    policy.SPECIALIST_REFER,
    policy.EVIDENCE_REQUEST_AUTHORISE,
    policy.DECISION_APPROVE,
)


def viewer_view(scope: policy.TenantScope, open_record: DecisionRecord | None) -> dict[str, Any]:
    """Who is looking and which review actions they may take (TAX-042).

    A hint for the screen only: every action is authorised again when it is sent.
    """
    allowed: list[str] = []
    for action in VIEWER_ACTIONS:
        proposed_by = None
        if action == policy.DECISION_APPROVE:
            if open_record is None or open_record.status != "proposed":
                continue
            proposed_by = open_record.proposed_by
        try:
            policy.authorize_action(scope, action, proposed_by=proposed_by)
        except policy.ActionDenied:
            continue
        allowed.append(action)
    return {"actor": scope.user, "roles": sorted(scope.roles), "allowed_actions": allowed}


def _iso(value: datetime.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def decision_view(record: DecisionRecord, *, replayed: bool | None = None) -> dict[str, Any]:
    view: dict[str, Any] = {
        "decision_id": record.decision_id,
        "case_id": record.case_id,
        "tenant_id": record.tenant_id,
        "status": record.status,
        "origin": record.origin,
        "outcome": record.outcome,
        "operation": rules.OUTCOMES[record.outcome].operation,
        "target_status": rules.OUTCOMES[record.outcome].target_status,
        "rationale": record.rationale,
        "evidence_ids": list(record.evidence_ids),
        "finding_ids": list(record.finding_ids),
        "rating": record.rating,
        "draft_id": record.draft_id,
        "proposed_by": record.proposed_by,
        "proposed_at": _iso(record.proposed_at),
        "case_status_at_proposal": record.case_status_at_proposal,
        "case_version": record.case_version,
        "reviewed_by": record.reviewed_by,
        "reviewed_at": _iso(record.reviewed_at),
        "review_reason": record.review_reason,
        "jube_sync": {
            "state": record.sync_state,
            "attempts": record.sync_attempts,
            "error_code": record.sync_error_code,
            "retryable": record.sync_retryable,
            "last_attempt_at": _iso(record.last_attempt_at),
            "synced_at": _iso(record.synced_at),
            "changed": record.jube_changed,
            "status_after": record.jube_status_after,
            "status_authority": "jube",
        },
    }
    if replayed is not None:
        view["replayed"] = replayed
    return view


def evidence_request_view(
    record: EvidenceRequestRecord, *, replayed: bool | None = None
) -> dict[str, Any]:
    """``EvidenceRequest`` fields (openapi.json), plus ``replayed``.

    ``authorised_by`` is the officer's Frappe user. The contract types it as a
    UUID; no officer UUID exists in Frappe yet (see ADR-013).
    """
    view: dict[str, Any] = {
        "request_id": record.request_id,
        "case_id": record.case_id,
        "tenant_id": record.tenant_id,
        "status": record.status,
        "authorised_by": record.authorised_by,
        "finding_ids": list(record.finding_ids),
        "items": [asdict(item) for item in record.items],
        "due_date": record.due_date.isoformat(),
        "case_version": record.case_version,
        "service_links": [],
        "created_at": _iso(record.created_at),
    }
    if replayed is not None:
        view["replayed"] = replayed
    return view


def to_ledger(event: AuditEvent) -> ledger.Unsealed:
    """The ledger form of a decision or evidence-request event (TAX-059)."""
    return ledger.Unsealed(
        event_id=event.event_id,
        tenant_id=event.tenant_id,
        action=event.action,
        outcome=event.outcome,
        actor=event.actor,
        actor_role=event.actor_role,
        occurred_at=event.occurred_at,
        correlation_id=event.correlation_id,
        subject_type=event.subject_type,
        subject_id=event.subject_id,
        payload_hash=event.payload_hash,
        case_id=event.case_id,
        taxpayer_id=event.taxpayer_id,
        sequence=event.sequence,
    )


def event_view(event: AuditEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "sequence": event.sequence,
        "actor": event.actor,
        "actor_role": event.actor_role,
        "action": event.action,
        "subject_type": event.subject_type,
        "subject_id": event.subject_id,
        "occurred_at": _iso(event.occurred_at),
        "correlation_id": event.correlation_id,
        "payload_hash": event.payload_hash,
        "outcome": event.outcome,
    }


# --- in-memory repository (tests and local smoke) -----------------------------------


@dataclass
class MemoryRepository:
    """``Repository`` in memory with the same unique keys as the Frappe DocTypes."""

    cases: dict[tuple[str, str], CaseRef] = field(default_factory=dict)
    evidence: dict[str, tuple[str, str]] = field(default_factory=dict)  # id -> tenant, taxpayer
    decisions: dict[str, DecisionRecord] = field(default_factory=dict)
    requests: dict[str, EvidenceRequestRecord] = field(default_factory=dict)
    events: list[AuditEvent] = field(default_factory=list)
    # The tenant hash chain (TAX-059); every audit event is sealed into it.
    ledger: MemoryLedger = field(default_factory=MemoryLedger)

    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        snapshot = (
            {k: replace(v) for k, v in self.decisions.items()},
            dict(self.requests),
            list(self.events),
            list(self.ledger.rows),
        )
        try:
            yield
        except BaseException:
            self.decisions, self.requests, self.events, self.ledger.rows = snapshot
            raise

    def get_case(self, tenant_id: str, case_id: str) -> CaseRef | None:
        for (_tenant, cid), case in self.cases.items():
            if cid == case_id:
                return case
        return None

    def case_version(self, tenant_id: str, case_id: str) -> int:
        return 1 + sum(
            1
            for e in self.events
            if e.tenant_id == tenant_id and e.case_id == case_id and e.sequence is not None
        )

    def known_evidence(
        self, tenant_id: str, taxpayer_id: str | None, evidence_ids: tuple[str, ...]
    ) -> set[str]:
        return {
            eid
            for eid in evidence_ids
            if eid in self.evidence
            and self.evidence[eid][0] == tenant_id
            and (taxpayer_id is None or self.evidence[eid][1] == taxpayer_id)
        }

    def get_decision(self, decision_id: str) -> DecisionRecord | None:
        record = self.decisions.get(decision_id)
        return replace(record) if record else None

    def decision_by_key(self, registration_key: str) -> DecisionRecord | None:
        return next(
            (replace(d) for d in self.decisions.values() if d.registration_key == registration_key),
            None,
        )

    def decision_by_review_key(self, review_key: str) -> DecisionRecord | None:
        return next(
            (replace(d) for d in self.decisions.values() if d.review_key == review_key), None
        )

    def open_decision(self, tenant_id: str, case_id: str) -> DecisionRecord | None:
        key = rules.open_decision_key(tenant_id, case_id)
        return next((replace(d) for d in self.decisions.values() if d.open_key == key), None)

    def _unique(self, record: DecisionRecord) -> None:
        for other in self.decisions.values():
            if other.decision_id == record.decision_id:
                continue
            for attr in ("registration_key", "open_key", "review_key"):
                mine = getattr(record, attr)
                if mine is not None and mine == getattr(other, attr):
                    raise Duplicate(attr)

    def insert_decision(self, record: DecisionRecord) -> None:
        if record.decision_id in self.decisions:
            raise Duplicate("decision_id")
        self._unique(record)
        self.decisions[record.decision_id] = replace(record)

    def save_decision(self, record: DecisionRecord) -> None:
        self._unique(record)
        self.decisions[record.decision_id] = replace(record)

    def request_by_key(self, registration_key: str) -> EvidenceRequestRecord | None:
        return next(
            (r for r in self.requests.values() if r.registration_key == registration_key), None
        )

    def insert_request(self, record: EvidenceRequestRecord) -> None:
        if self.request_by_key(record.registration_key) is not None:
            raise Duplicate("registration_key")
        self.requests[record.request_id] = record

    def append_event(self, event: AuditEvent) -> None:
        if event.sequence is not None and any(
            e.tenant_id == event.tenant_id
            and e.case_id == event.case_id
            and e.sequence == event.sequence
            for e in self.events
        ):
            raise Duplicate("sequence_key")
        try:
            ledger.append(self.ledger, to_ledger(event))
        except ledger.Duplicate as exc:
            raise Duplicate(str(exc)) from exc
        self.events.append(event)

    def case_events(self, tenant_id: str, case_id: str) -> list[AuditEvent]:
        return [e for e in self.events if e.tenant_id == tenant_id and e.case_id == case_id]
