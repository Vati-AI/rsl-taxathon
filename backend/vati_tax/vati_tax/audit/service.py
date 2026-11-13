"""Ledger operations with policy checks (TAX-059), independent of Frappe.

``store.py`` binds ``LedgerStore`` to the ``Vati Audit Event`` DocType; tests
bind it to ``ledger.MemoryLedger``. Every function authorises the caller with
the TAX-029 policy matrix first.

- Reads of the ledger, its integrity check, signals and summary need
  ``ledger.read`` (supervisors only) and are themselves recorded as reads.
- An override is a supervisor's recorded exception to a named control, with a
  reason. Nobody overrides a control on their own work.
- A refused action is recorded as a ``denied`` entry before the refusal is
  raised, so attempts stay visible to the supervisor review.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, TypeVar

from vati_tax.audit import ledger
from vati_tax.audit.ledger import LedgerEntry, LedgerStore, Unsealed
from vati_tax.identity import policy
from vati_tax.ids.canonical import (
    IdempotencyConflict,
    InvalidMapping,
    canonical_uuid,
    optional_uuid,
    validate_idempotency_key,
)

T = TypeVar("T")

# Controls a supervisor may record an exception to. Other tasks add names here
# when they introduce a control (TAX-065 workflow steps, TAX-077 views).
CONTROLS: Final[frozenset[str]] = frozenset(
    {
        "case_assignment",  # moving a case away from its assigned officer
        "evidence_due_date",  # extending a taxpayer's evidence deadline
        # recording a case rating that departs from the Vati rule signals; the key is kept
        # for stored entries, but Vati has no risk score of its own (TAX-131, ADR-043 §6)
        "risk_rating",
        "playbook_step",  # skipping a step of an approved playbook
    }
)
OVERRIDE_SUBJECTS: Final[frozenset[str]] = frozenset(
    {"case", "decision", "evidence_request", "evidence"}
)
REASON_MIN: Final = 20
REASON_MAX: Final = 2000
PAGE_MAX: Final = 500
_OVERRIDE_NS: Final = uuid.UUID("6f1d2a8e-5b1c-4e0a-9d59-0c9e5a7d0590")


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


def acting_role(scope: policy.TenantScope, action: str | None = None) -> str:
    roles = scope.roles & policy.ACTION_ROLES[action] if action else scope.roles
    pool = roles or scope.roles
    return sorted(pool)[0] if pool else "none"


def _record(
    ctx: Context,
    *,
    action: str,
    outcome: str,
    subject_type: str,
    subject_id: str,
    payload: Any,
    role: str,
    case_id: str | None = None,
    taxpayer_id: str | None = None,
    reason: str | None = None,
    target_actor: str | None = None,
    event_id: str | None = None,
) -> Unsealed:
    return Unsealed(
        event_id=event_id or ledger.new_event_id(),
        tenant_id=ctx.scope.tenant_id,
        action=action,
        outcome=outcome,
        actor=ctx.scope.user,
        actor_role=role,
        occurred_at=ctx.now,
        correlation_id=ctx.correlation_id,
        subject_type=subject_type,
        subject_id=subject_id,
        payload_hash=ledger.digest(payload),
        case_id=case_id,
        taxpayer_id=taxpayer_id,
        reason=reason,
        target_actor=target_actor,
    )


# --- reads and refusals -------------------------------------------------------------


def record_read(
    ctx: Context,
    store: LedgerStore,
    *,
    action: str,
    subject_type: str,
    subject_id: str,
    case_id: str | None = None,
    taxpayer_id: str | None = None,
    detail: Any = None,
) -> LedgerEntry:
    """Record that the caller read ``subject``. ``action`` ends with ``.read``."""
    if not action.endswith(".read"):
        raise ValueError("read actions end with .read")
    return ledger.append(
        store,
        _record(
            ctx,
            action=action,
            outcome="read",
            subject_type=subject_type,
            subject_id=subject_id,
            payload={"detail": detail},
            role=acting_role(ctx.scope),
            case_id=case_id,
            taxpayer_id=taxpayer_id,
        ),
    )


def record_denied(
    ctx: Context,
    store: LedgerStore,
    *,
    attempted_action: str,
    subject_type: str,
    subject_id: str,
    code: str,
    case_id: str | None = None,
    target_actor: str | None = None,
) -> LedgerEntry:
    """Record a refused attempt (category ``denied``). The refusal text is not stored."""
    return ledger.append(
        store,
        _record(
            ctx,
            action=f"{attempted_action}.denied",
            outcome=code,
            subject_type=subject_type,
            subject_id=subject_id,
            payload={"attempted": attempted_action, "code": code},
            role=acting_role(ctx.scope),
            case_id=case_id,
            target_actor=target_actor,
        ),
    )


def guarded(
    ctx: Context,
    store: LedgerStore,
    *,
    attempted_action: str,
    subject_type: str,
    subject_id: str,
    fn: Callable[[], T],
    case_id: str | None = None,
    target_actor: str | None = None,
    on_denied: Callable[[], None] | None = None,
) -> T:
    """Run ``fn``; if the policy refuses it, record the attempt and re-raise.

    ``on_denied`` runs before the entry is written (the Frappe binding rolls
    back the request there, so only the refusal is kept).
    """
    try:
        return fn()
    except policy.ActionDenied as exc:
        if on_denied is not None:
            on_denied()
        record_denied(
            ctx,
            store,
            attempted_action=attempted_action,
            subject_type=subject_type,
            subject_id=subject_id,
            code=exc.code,
            case_id=case_id,
            target_actor=target_actor,
        )
        raise


# --- overrides ----------------------------------------------------------------------


@dataclass(frozen=True)
class Override:
    control: str
    subject_type: str
    subject_id: str
    case_id: str | None
    reason: str
    target_actor: str | None
    idempotency_key: str

    @classmethod
    def build(cls, body: dict[str, Any], idempotency_key: str) -> Override:
        control = body.get("control")
        if control not in CONTROLS:
            raise InvalidMapping("Unknown control.", "control", "unknown_control")
        subject_type = body.get("subject_type")
        if subject_type not in OVERRIDE_SUBJECTS:
            raise InvalidMapping("Unknown subject type.", "subject_type", "unknown_subject_type")
        reason = body.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise InvalidMapping("A reason is required.", "reason", "required")
        reason = reason.strip()
        if not REASON_MIN <= len(reason) <= REASON_MAX:
            raise InvalidMapping(
                f"The reason must be {REASON_MIN} to {REASON_MAX} characters.",
                "reason",
                "invalid_length",
            )
        target = body.get("target_actor")
        if target is not None and (not isinstance(target, str) or not 0 < len(target) <= 140):
            raise InvalidMapping("Invalid target actor.", "target_actor", "invalid")
        return cls(
            control=str(control),
            subject_type=str(subject_type),
            subject_id=canonical_uuid(body.get("subject_id"), "subject_id"),
            case_id=optional_uuid(body.get("case_id"), "case_id"),
            reason=reason,
            target_actor=target,
            idempotency_key=validate_idempotency_key(idempotency_key),
        )

    def payload(self) -> dict[str, Any]:
        return {
            "control": self.control,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "case_id": self.case_id,
            "reason": self.reason,
            "target_actor": self.target_actor,
        }


def _override_event_id(tenant_id: str, idempotency_key: str) -> str:
    return str(uuid.uuid5(_OVERRIDE_NS, f"{tenant_id}|{idempotency_key}"))


def record_override(
    ctx: Context, store: LedgerStore, *, body: dict[str, Any], idempotency_key: str
) -> dict[str, Any]:
    """Record a supervisor's exception to a control. Replays return the stored entry."""
    request = Override.build(body, idempotency_key)

    def authorise() -> None:
        policy.authorize_action(ctx.scope, policy.OVERRIDE_RECORD)
        if request.target_actor and request.target_actor.lower() == ctx.scope.user.lower():
            raise policy.ActionDenied("You cannot override a control on your own work")

    guarded(
        ctx,
        store,
        attempted_action=policy.OVERRIDE_RECORD,
        subject_type=request.subject_type,
        subject_id=request.subject_id,
        case_id=request.case_id,
        target_actor=request.target_actor,
        fn=authorise,
    )
    event_id = _override_event_id(ctx.scope.tenant_id, request.idempotency_key)
    payload_hash = ledger.digest(request.payload())
    existing = next((e for e in store.entries(ctx.scope.tenant_id) if e.event_id == event_id), None)
    if existing is not None:
        if (
            existing.payload_hash != payload_hash
            or existing.actor.lower() != ctx.scope.user.lower()
        ):
            raise IdempotencyConflict(
                "This idempotency key was used for a different request.",
                "idempotency_key",
                "idempotency_conflict",
            )
        return {**ledger.entry_view(existing), "replayed": True}
    entry = ledger.append(
        store,
        _record(
            ctx,
            action=f"override.{request.control}",
            outcome="recorded",
            subject_type=request.subject_type,
            subject_id=request.subject_id,
            payload=request.payload(),
            role=acting_role(ctx.scope, policy.OVERRIDE_RECORD),
            case_id=request.case_id,
            reason=request.reason,
            target_actor=request.target_actor,
            event_id=event_id,
        ),
    )
    return {**ledger.entry_view(entry), "replayed": False}


# --- supervisor reads (TAX-060 contract) --------------------------------------------


def _authorise_read(ctx: Context, store: LedgerStore) -> None:
    guarded(
        ctx,
        store,
        attempted_action=policy.LEDGER_READ,
        subject_type="ledger",
        subject_id=ctx.scope.tenant_id,
        fn=lambda: policy.authorize_action(ctx.scope, policy.LEDGER_READ),
    )


def _read(ctx: Context, store: LedgerStore, detail: dict[str, Any]) -> None:
    record_read(
        ctx,
        store,
        action="ledger.read",
        subject_type="ledger",
        subject_id=ctx.scope.tenant_id,
        detail=detail,
    )


def ledger_entries(
    ctx: Context,
    store: LedgerStore,
    *,
    case_id: str | None = None,
    actor: str | None = None,
    category: str | None = None,
    since_seq: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """A page of the tenant ledger, oldest first, for a supervisor."""
    _authorise_read(ctx, store)
    if category is not None and category not in ledger.CATEGORIES:
        raise InvalidMapping("Unknown category.", "category", "unknown_category")
    if not 1 <= limit <= PAGE_MAX or since_seq < 0:
        raise InvalidMapping("Invalid page.", "limit", "invalid")
    case = optional_uuid(case_id, "case_id")
    rows = [
        e
        for e in store.entries(ctx.scope.tenant_id)
        if e.chain_seq > since_seq
        and (case is None or e.case_id == case)
        and (actor is None or e.actor.lower() == actor.lower())
        and (category is None or e.category == category)
    ]
    page = rows[:limit]
    head = store.head(ctx.scope.tenant_id)
    _read(ctx, store, {"view": "entries", "case_id": case, "actor": actor, "category": category})
    return {
        "tenant_id": ctx.scope.tenant_id,
        "entries": [ledger.entry_view(e) for e in page],
        "next_since_seq": page[-1].chain_seq if len(rows) > limit else None,
        "chain_head": {
            "chain_seq": head.chain_seq if head else 0,
            "entry_hash": head.entry_hash if head else ledger.GENESIS,
        },
    }


def verify_ledger(ctx: Context, store: LedgerStore) -> dict[str, Any]:
    _authorise_read(ctx, store)
    report = ledger.verify_chain(store.entries(ctx.scope.tenant_id))
    _read(ctx, store, {"view": "verify", "intact": report["intact"]})
    return {"tenant_id": ctx.scope.tenant_id, **report}


def review_signals(
    ctx: Context, store: LedgerStore, *, window_days: int = ledger.DEFAULT_WINDOW_DAYS
) -> dict[str, Any]:
    _authorise_read(ctx, store)
    try:
        result = ledger.integrity_signals(
            store.entries(ctx.scope.tenant_id),
            now=ctx.now,
            viewer=ctx.scope.user,
            window_days=window_days,
        )
    except ValueError as exc:
        raise InvalidMapping(str(exc), "window_days", "invalid") from None
    _read(ctx, store, {"view": "signals", "window_days": window_days})
    return {"tenant_id": ctx.scope.tenant_id, **result}


def ledger_summary(
    ctx: Context, store: LedgerStore, *, since: datetime.datetime, until: datetime.datetime
) -> dict[str, Any]:
    _authorise_read(ctx, store)
    try:
        result = ledger.summary(store.entries(ctx.scope.tenant_id), since=since, until=until)
    except ValueError as exc:
        raise InvalidMapping(str(exc), "since", "invalid") from None
    _read(ctx, store, {"view": "summary", "since": ledger.iso(since), "until": ledger.iso(until)})
    return {"tenant_id": ctx.scope.tenant_id, **result}
