"""Correction reminder workflow with policy checks (TAX-078), independent of Frappe.

``store.py`` binds the ports below to Frappe; tests use ``MemoryReminders``
with the TAX-046 ``MemoryRepository`` and the TAX-069 ``MemoryChallenges``.

- ``record``: an auditor or supervisor records a reminder from an approved
  decision on the taxpayer's own case whose outcome recorded an omission.
- ``correct``: an auditor or supervisor corrects the topic or wording, or
  withdraws the reminder, with a reason. The earlier wording is kept.
- ``staff_list``: every reminder of one taxpayer, with its history and whether
  the taxpayer can see it.
- ``taxpayer_list``: what the portal shows. Only the caller's own reminders,
  only active ones whose decision still stands, one per topic.

Every staff step, read and refusal is sealed into the TAX-059 ledger (subject
``reminder``). Nothing here changes a decision, a challenge, a claim or a case.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, NoReturn, Protocol, TypeVar

from vati_tax.audit import ledger
from vati_tax.audit import service as audit
from vati_tax.audit.ledger import LedgerStore
from vati_tax.challenges.rules import ChallengeRecord
from vati_tax.decisions.engine import DecisionRecord
from vati_tax.identity import policy
from vati_tax.ids.canonical import IdempotencyConflict, MappingError, canonical_uuid
from vati_tax.reminders import rules
from vati_tax.reminders.rules import ReminderRecord, SourceState

T = TypeVar("T")
SUBJECT = "reminder"


class ReminderStore(Protocol):
    def atomic(self) -> contextlib.AbstractContextManager[None]: ...

    def get(self, tenant_id: str, reminder_id: str) -> ReminderRecord | None: ...

    def for_taxpayer(self, tenant_id: str, taxpayer_id: str) -> list[ReminderRecord]: ...

    def insert(self, record: ReminderRecord) -> None:
        """Store a new record; raise ``ledger.Duplicate`` if its decision and topic exist."""
        ...

    def update(self, record: ReminderRecord, expected_version: int) -> bool:
        """Compare-and-set on ``version``; ``ledger.Duplicate`` on a clashing topic."""
        ...


class DecisionSource(Protocol):
    def get_decision(self, decision_id: str) -> DecisionRecord | None: ...


class ChallengeSource(Protocol):
    def for_decision(self, tenant_id: str, decision_id: str) -> list[ChallengeRecord]: ...


@dataclass(frozen=True)
class Ports:
    reminders: ReminderStore
    decisions: DecisionSource
    challenges: ChallengeSource
    ledger: LedgerStore


def _state(ports: Ports, tenant_id: str, decision_id: str) -> SourceState:
    return rules.source_state(ports.challenges.for_decision(tenant_id, decision_id))


def _append(
    ctx: audit.Context,
    ports: Ports,
    *,
    action: str,
    outcome: str,
    record: ReminderRecord,
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
            subject_id=record.reminder_id,
            payload_hash=ledger.digest(payload),
            case_id=record.case_id,
            taxpayer_id=record.taxpayer_id,
            reason=reason,
        ),
    )


def _refuse(
    ctx: audit.Context,
    ports: Ports,
    *,
    attempted: str,
    subject_id: str,
    case_id: str | None,
    exc: MappingError,
) -> NoReturn:
    audit.record_denied(
        ctx,
        ports.ledger,
        attempted_action=attempted,
        subject_type=SUBJECT,
        subject_id=subject_id,
        code=exc.field_errors[0]["code"] if exc.field_errors else exc.code,
        case_id=case_id,
    )
    raise exc


def _guard(
    ctx: audit.Context,
    ports: Ports,
    *,
    action: str,
    subject_id: str,
    case_id: str | None,
    fn: Callable[[], T],
) -> T:
    return audit.guarded(
        ctx,
        ports.ledger,
        attempted_action=action,
        subject_type=SUBJECT,
        subject_id=subject_id,
        case_id=case_id,
        fn=fn,
    )


# --- record -------------------------------------------------------------------------


def record(
    ctx: audit.Context, ports: Ports, *, body: Mapping[str, Any], idempotency_key: str
) -> dict[str, Any]:
    """Record a reminder from a reviewed omission. Replaying the key returns it."""
    request = rules.RecordRequest.build(body, idempotency_key)
    rid = rules.reminder_id(ctx.scope.tenant_id, request.idempotency_key)
    _guard(
        ctx,
        ports,
        action=policy.REMINDER_RECORD,
        subject_id=rid,
        case_id=None,
        fn=lambda: policy.authorize_action(ctx.scope, policy.REMINDER_RECORD),
    )
    request_digest = rules.digest(request.payload())
    existing = ports.reminders.get(ctx.scope.tenant_id, rid)
    if existing is not None:
        if (
            existing.request_digest != request_digest
            or existing.recorded_by.lower() != ctx.scope.user.lower()
        ):
            raise IdempotencyConflict(
                "This idempotency key was used for a different request.",
                "idempotency_key",
                "idempotency_conflict",
            )
        state = _state(ports, existing.tenant_id, existing.decision_id)
        return {**rules.staff_view(existing, state), "replayed": True}
    case_id: str | None = None
    try:
        decision = ports.decisions.get_decision(request.decision_id)
        if decision is None or decision.tenant_id != ctx.scope.tenant_id:
            raise rules.ReminderNotFound("Decision not found.", "decision_id", "not_found")
        case_id = decision.case_id
        if not decision.taxpayer_id:
            raise rules.ReminderConflict(
                "The decision's case has no taxpayer to remind.", "decision_id", "no_taxpayer"
            )
        if decision.status != "approved":
            raise rules.ReminderConflict(
                "Only an approved decision can be the source of a reminder.",
                "decision_id",
                "decision_not_final",
            )
        if decision.outcome not in rules.SOURCE_OUTCOMES:
            raise rules.ReminderConflict(
                "The decision did not record an omission for the taxpayer to correct.",
                "decision_id",
                "not_an_omission",
            )
        state = _state(ports, ctx.scope.tenant_id, decision.decision_id)
        if state != "stands":
            raise rules.ReminderConflict(
                "The decision is challenged or was overturned; it cannot be the source "
                "of a reminder.",
                "decision_id",
                "source_" + state,
            )
        new = ReminderRecord(
            reminder_id=rid,
            tenant_id=ctx.scope.tenant_id,
            taxpayer_id=decision.taxpayer_id,
            case_id=decision.case_id,
            decision_id=decision.decision_id,
            topic=request.topic,
            explanation=request.explanation,
            request_digest=request_digest,
            recorded_by=ctx.scope.user,
            recorded_at=rules.utc(ctx.now),
        )
        with ports.reminders.atomic():
            try:
                ports.reminders.insert(new)
            except ledger.Duplicate:
                raise rules.ReminderConflict(
                    "This decision already has a reminder on this topic; correct it instead.",
                    "topic",
                    "reminder_exists",
                ) from None
            _append(
                ctx,
                ports,
                action="reminder.recorded",
                outcome=new.topic,
                record=new,
                payload=request.payload(),
                privilege=policy.REMINDER_RECORD,
            )
    except MappingError as exc:
        _refuse(
            ctx, ports, attempted=policy.REMINDER_RECORD, subject_id=rid, case_id=case_id, exc=exc
        )
    return {**rules.staff_view(new, state), "replayed": False}


# --- correct or withdraw ------------------------------------------------------------


def correct(
    ctx: audit.Context,
    ports: Ports,
    *,
    reminder_id: object,
    expected_version: object,
    action: object,
    reason: object,
    topic: object = None,
    explanation: object = None,
) -> dict[str, Any]:
    """Correct a reminder's topic or wording (``correct``) or withdraw it (``withdraw``)."""
    rid = canonical_uuid(reminder_id, "reminder_id")
    current = ports.reminders.get(ctx.scope.tenant_id, rid)
    if current is None:
        raise rules.ReminderNotFound("Reminder not found.", "reminder_id", "not_found")
    _guard(
        ctx,
        ports,
        action=policy.REMINDER_CORRECT,
        subject_id=current.reminder_id,
        case_id=current.case_id,
        fn=lambda: policy.authorize_action(ctx.scope, policy.REMINDER_CORRECT),
    )
    try:
        version = rules.check_version(expected_version)
        if action not in ("correct", "withdraw"):
            raise rules.ReminderConflict(
                "action must be correct or withdraw.", "action", "unknown_action"
            )
        why = rules.text(reason, "reason", rules.REASON_MIN, rules.REASON_MAX)
        if current.status != "active":
            raise rules.ReminderConflict(
                "A withdrawn reminder cannot change; record a new one.", "reminder_id", "not_active"
            )
        if version != current.version:
            raise rules.ReminderConflict(
                "The reminder changed since you loaded it.", "expected_version", "stale_version"
            )
        if len(current.history) >= rules.MAX_HISTORY:
            raise rules.ReminderConflict(
                "This reminder has been changed too often; withdraw it and record a new one.",
                "reminder_id",
                "too_many_changes",
            )
        changes: dict[str, Any] = {}
        if action == "withdraw":
            changes["status"] = "withdrawn"
        else:
            if topic not in (None, ""):
                changes["topic"] = rules.check_topic(topic)
            if explanation not in (None, ""):
                changes["explanation"] = rules.text(
                    explanation, "explanation", rules.EXPLANATION_MIN, rules.EXPLANATION_MAX
                )
            changes = {k: v for k, v in changes.items() if getattr(current, k) != v}
            if not changes:
                raise rules.ReminderConflict(
                    "Change the topic or the explanation, or withdraw the reminder.",
                    "explanation",
                    "no_change",
                )
        previous = {
            "version": current.version,
            "topic": current.topic,
            "explanation": current.explanation,
            "status": current.status,
            "changed_by": ctx.scope.user,
            "changed_at": rules.iso(ctx.now),
            "reason": why,
        }
        updated = replace(
            current,
            **changes,
            version=current.version + 1,
            updated_by=ctx.scope.user,
            updated_at=rules.utc(ctx.now),
            change_reason=why,
            history=(*current.history, previous),
        )
        with ports.reminders.atomic():
            try:
                ok = ports.reminders.update(updated, current.version)
            except ledger.Duplicate:
                raise rules.ReminderConflict(
                    "This decision already has a reminder on that topic.",
                    "topic",
                    "reminder_exists",
                ) from None
            if not ok:
                raise rules.ReminderConflict(
                    "The reminder changed since you loaded it.", "expected_version", "stale_version"
                )
            _append(
                ctx,
                ports,
                action="reminder.withdrawn" if action == "withdraw" else "reminder.corrected",
                outcome=updated.status if action == "withdraw" else updated.topic,
                record=updated,
                payload={"changes": changes, "version": updated.version},
                privilege=policy.REMINDER_CORRECT,
                reason=why,
            )
    except MappingError as exc:
        _refuse(
            ctx,
            ports,
            attempted=policy.REMINDER_CORRECT,
            subject_id=current.reminder_id,
            case_id=current.case_id,
            exc=exc,
        )
    return rules.staff_view(updated, _state(ports, updated.tenant_id, updated.decision_id))


# --- read ---------------------------------------------------------------------------


def staff_list(ctx: audit.Context, ports: Ports, *, taxpayer_id: object) -> dict[str, Any]:
    """Every reminder of one taxpayer in the tenant, newest first, for staff."""
    tid = canonical_uuid(taxpayer_id, "taxpayer_id")

    def authorise() -> None:
        # The staff view shows case IDs, staff identities and withdrawn wording;
        # taxpayers use ``taxpayer_list`` (the portal) instead.
        if not ctx.scope.roles & policy.STAFF_ROLES:
            raise policy.ActionDenied("Only staff can read the reminder history")
        policy.authorize_action(ctx.scope, policy.REMINDER_READ, taxpayer_id=tid)

    _guard(
        ctx,
        ports,
        action=policy.REMINDER_READ,
        subject_id=tid,
        case_id=None,
        fn=authorise,
    )
    records = ports.reminders.for_taxpayer(ctx.scope.tenant_id, tid)
    records.sort(key=lambda r: (r.recorded_at, r.reminder_id), reverse=True)
    states: dict[str, SourceState] = {}
    for item in records:
        if item.decision_id not in states:
            states[item.decision_id] = _state(ports, item.tenant_id, item.decision_id)
    audit.record_read(
        ctx,
        ports.ledger,
        action="reminder.read",
        subject_type=SUBJECT,
        subject_id=tid,
        taxpayer_id=tid,
        detail={"reminders": len(records)},
    )
    return {
        "taxpayer_id": tid,
        "reminders": [rules.staff_view(r, states[r.decision_id]) for r in records],
    }


def taxpayer_list(ports: Ports, scope: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
    """The portal view for the caller's own (tenant, taxpayer) memberships only."""
    records: list[ReminderRecord] = []
    for tenant_id, taxpayer_id in scope:
        records.extend(
            r
            for r in ports.reminders.for_taxpayer(tenant_id, taxpayer_id)
            # Defence in depth: the store already filters on both.
            if r.tenant_id == tenant_id and r.taxpayer_id == taxpayer_id
        )
    states: dict[str, SourceState] = {}
    for item in records:
        if item.status == "active" and item.decision_id not in states:
            states[item.decision_id] = _state(ports, item.tenant_id, item.decision_id)
    return rules.taxpayer_reminders(records, states)


# --- in-memory port -----------------------------------------------------------------


@dataclass
class MemoryReminders:
    rows: dict[str, ReminderRecord] = field(default_factory=dict)

    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        snapshot = dict(self.rows)
        try:
            yield
        except BaseException:
            self.rows = snapshot
            raise

    def get(self, tenant_id: str, reminder_id: str) -> ReminderRecord | None:
        found = self.rows.get(reminder_id)
        return found if found and found.tenant_id == tenant_id else None

    def for_taxpayer(self, tenant_id: str, taxpayer_id: str) -> list[ReminderRecord]:
        return [
            r
            for r in self.rows.values()
            if r.tenant_id == tenant_id and r.taxpayer_id == taxpayer_id
        ]

    def _clash(self, candidate: ReminderRecord) -> bool:
        key = rules.reminder_key(candidate.tenant_id, candidate.decision_id, candidate.topic)
        return any(
            other.reminder_id != candidate.reminder_id
            and rules.reminder_key(other.tenant_id, other.decision_id, other.topic) == key
            for other in self.rows.values()
        )

    def insert(self, record: ReminderRecord) -> None:
        if record.reminder_id in self.rows or self._clash(record):
            raise ledger.Duplicate("reminder_key")
        self.rows[record.reminder_id] = record

    def update(self, record: ReminderRecord, expected_version: int) -> bool:
        current = self.rows.get(record.reminder_id)
        if current is None or current.version != expected_version:
            return False
        if self._clash(record):
            raise ledger.Duplicate("reminder_key")
        self.rows[record.reminder_id] = record
        return True
