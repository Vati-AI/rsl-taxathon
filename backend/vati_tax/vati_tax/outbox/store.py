"""Write and administer outbox events in Frappe (TAX-033).

Internal server-side API. Nothing here is whitelisted except through
``vati_tax.outbox.api``. Callers pass the tenant they already authorised
(TAX-029); every lookup is scoped to it, so an event ID alone never reaches
another tenant's event. Inserts use ``ignore_permissions`` because the DocTypes
are read-only in desk; that bypass is limited to this package and covered by
negative tests in ``test_outbox_bench.py``.

``enqueue`` never commits. Call it in the same transaction as the domain change
that caused the event: both commit together or neither does. The worker only
ever sees committed events.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import frappe

from vati_tax.ids.canonical import CaseNotFound, TenantBoundaryError, canonical_uuid
from vati_tax.outbox import policy

EVENT = "Vati Outbox Event"
ATTEMPT = "Vati Outbox Attempt"
EFFECT = "Vati Outbox Effect"
CASE = "Vati Case Identity"
_RACE_ERRORS = (frappe.UniqueValidationError, frappe.DuplicateEntryError)


def utcnow() -> datetime:
    """Naive UTC, the form stored in the ``(UTC)`` Datetime fields."""
    return datetime.now(UTC).replace(tzinfo=None)


def naive(value: datetime) -> datetime:
    return policy.utc(value).replace(tzinfo=None)


def log(action: str, outcome: str, **ids: object) -> None:
    frappe.logger("vati_tax.outbox").info({"action": action, "outcome": outcome, **ids})


def _check_case(tenant_id: str, case_id: str) -> None:
    owner = frappe.db.get_value(CASE, case_id, "tenant_id")
    if owner is None:
        raise CaseNotFound("Case not found.", "case_id", "not_found")
    if owner != tenant_id:
        raise TenantBoundaryError("Case not found.", "case_id", "not_found")


def _result(row: Any, replayed: bool) -> dict[str, Any]:
    return {
        "event_id": row.event_id,
        "tenant_id": row.tenant_id,
        "case_id": row.case_id,
        "status": row.status,
        "sequence": row.sequence,
        "replayed": replayed,
    }


_RESULT_FIELDS = ["event_id", "tenant_id", "case_id", "status", "sequence", "request_digest"]


def enqueue(
    *,
    tenant_id: str,
    case_id: str,
    type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    correlation_id: str,
    source_service: str = "vati",
    event_id: str | None = None,
    occurred_at: datetime | None = None,
    max_attempts: int | None = None,
) -> dict[str, Any]:
    """Add an event to the outbox in the caller's transaction (no commit).

    The same ``idempotency_key`` with the same request returns the stored event
    (``replayed: True``); with a different request it raises
    ``IdempotencyConflict`` (422). A missing or foreign case is ``not_found``.
    """
    env = policy.Envelope.build(
        tenant_id=tenant_id,
        case_id=case_id,
        type=type,
        payload=payload,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        source_service=source_service,
        event_id=event_id,
        occurred_at=occurred_at,
    )
    budget = policy.RetryPolicy(max_attempts=max_attempts or policy.DEFAULT_POLICY.max_attempts)
    _check_case(env.tenant_id, env.case_id)
    for attempt in (1, 2, 3):
        stored = frappe.db.get_value(
            EVENT, {"dedupe_key": env.dedupe_key}, _RESULT_FIELDS, as_dict=True
        )
        try:
            action = policy.decide_enqueue(
                env.request_digest, stored.request_digest if stored else None
            )
        except policy.MappingError as exc:
            log("enqueue", exc.code, tenant_id=env.tenant_id, case_id=env.case_id)
            raise
        if action == "replay":
            log("enqueue", "replayed", tenant_id=env.tenant_id, event_id=stored.event_id)
            return _result(stored, replayed=True)
        last = frappe.db.sql(
            f"select coalesce(max(`sequence`), 0) from `tab{EVENT}` where ordering_key = %s",
            (env.ordering_key,),
        )[0][0]
        doc = frappe.get_doc(
            {
                "doctype": EVENT,
                "event_id": env.event_id,
                "tenant_id": env.tenant_id,
                "case_id": env.case_id,
                "event_type": env.type,
                "correlation_id": env.correlation_id,
                "source_service": env.source_service,
                "occurred_at": naive(env.occurred_at),
                "idempotency_key": env.idempotency_key,
                "payload_json": policy.canonical_json(env.payload),
                "status": "pending",
                "attempts": 0,
                "max_attempts": budget.max_attempts,
                "sequence": int(last) + 1,
            }
        )
        frappe.db.savepoint("vati_outbox_enqueue")
        try:
            doc.insert(ignore_permissions=True)
        except _RACE_ERRORS:
            # A concurrent enqueue took the key or the sequence slot: decide again.
            frappe.db.rollback(save_point="vati_outbox_enqueue")
            if attempt == 3:
                raise
            continue
        log("enqueue", "created", tenant_id=env.tenant_id, event_id=env.event_id, type=env.type)
        return _result(doc, replayed=False)
    raise AssertionError("unreachable")


def _scoped_event(tenant_id: str, event_id: str) -> Any:
    tenant = canonical_uuid(tenant_id, "tenant_id")
    name = canonical_uuid(event_id, "event_id")
    owner = frappe.db.get_value(EVENT, name, "tenant_id")
    if owner is None:
        raise CaseNotFound("Event not found.", "event_id", "not_found")
    if owner != tenant:
        raise TenantBoundaryError("Event not found.", "event_id", "not_found")
    return frappe.get_doc(EVENT, name)


def delivery_state(*, tenant_id: str, event_id: str) -> dict[str, Any]:
    """Delivery status, attempts and contract ``SyncState`` of one tenant event."""
    doc = _scoped_event(tenant_id, event_id)
    return {
        "event_id": doc.event_id,
        "status": doc.status,
        "attempts": doc.attempts,
        "max_attempts": doc.max_attempts,
        "next_attempt_at": policy.iso(frappe.utils.get_datetime(doc.next_attempt_at))
        if doc.next_attempt_at
        else None,
        "manual_retries": doc.manual_retries,
        "sync": doc.sync_state(),
    }


def retry_dead_letter(*, tenant_id: str, event_id: str, actor: str) -> dict[str, Any]:
    """Operator action: give a dead-lettered event a fresh retry budget.

    A live delivery is ``idempotency_in_progress`` (409); any other status is
    ``invalid_domain_input`` (422). The change is versioned (track_changes).
    """
    doc = _scoped_event(tenant_id, event_id)
    lease = frappe.utils.get_datetime(doc.lease_until) if doc.lease_until else None
    policy.manual_retry_allowed(doc.status, lease, utcnow())
    doc.update(
        {
            "status": "pending",
            "attempts": 0,
            "next_attempt_at": None,
            "dead_lettered_at": None,
            "last_error_code": None,
            "manual_retries": (doc.manual_retries or 0) + 1,
            "last_manual_retry_by": actor,
        }
    )
    doc.save(ignore_permissions=True)
    log("retry_dead_letter", "pending", tenant_id=doc.tenant_id, event_id=doc.event_id)
    return {"event_id": doc.event_id, "status": doc.status, "manual_retries": doc.manual_retries}


def _scoped_effect(tenant_id: str, event_id: str, effect_name: str) -> Any:
    event = _scoped_event(tenant_id, event_id)
    key = policy.effect_key(event.tenant_id, event.name, policy.validate_effect_name(effect_name))
    name = frappe.db.get_value(EFFECT, {"effect_key": key}, "name")
    if not name:
        raise CaseNotFound("Effect not found.", "effect", "not_found")
    return event, frappe.get_doc(EFFECT, name)


def resolve_effect(
    *, tenant_id: str, event_id: str, effect_name: str, result_ref: str, actor: str
) -> dict[str, Any]:
    """Operator action after reconciling with the upstream: the call did happen.

    Marks a ``started`` effect ``completed`` with the upstream reference, so the
    next delivery reuses it and never repeats the call.
    """
    event, effect = _scoped_effect(tenant_id, event_id, effect_name)
    if effect.state != "started":
        raise policy.InvalidTransition("Effect is already completed.", "state", "completed")
    ref = policy.validate_result_ref(result_ref)
    effect.update({"state": "completed", "result_ref": ref, "completed_at": utcnow()})
    effect.save(ignore_permissions=True)
    log("resolve_effect", "completed", tenant_id=event.tenant_id, event_id=event.name, by=actor)
    return {"event_id": event.name, "effect": effect.effect_name, "state": effect.state}


def release_effect(
    *, tenant_id: str, event_id: str, effect_name: str, actor: str
) -> dict[str, Any]:
    """Operator action after reconciling with the upstream: the call did not happen.

    Deletes the ``started`` ledger row so the next delivery performs the call.
    A completed effect can never be released.
    """
    event, effect = _scoped_effect(tenant_id, event_id, effect_name)
    if effect.state != "started":
        raise policy.InvalidTransition(
            "A completed effect cannot be released.", "state", "completed"
        )
    frappe.delete_doc(EFFECT, effect.name, ignore_permissions=True, force=True)
    log("release_effect", "released", tenant_id=event.tenant_id, event_id=event.name, by=actor)
    return {"event_id": event.name, "effect": effect_name, "state": "released"}
