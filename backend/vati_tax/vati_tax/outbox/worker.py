"""Deliver committed outbox events at least once, idempotently (TAX-033).

One dispatch run:

1. **Claim** (one short transaction, committed): due events are selected with
   ``FOR UPDATE SKIP LOCKED`` so concurrent workers never claim the same row. An
   event is due when it is ``pending``/``retry`` and its backoff has passed, or
   ``in_progress`` with an expired lease (the holder crashed). An event is skipped
   while an earlier event of the same case is undelivered, which keeps per-case
   order. Each claim increments ``attempts``, sets a fresh lease token and writes
   a ``started`` attempt row before any handler runs, so a crash still uses up
   budget. A reclaimed event that already used its whole budget is dead-lettered
   instead of run again.
2. **Deliver**: the event's handler runs with a ``Delivery`` context. Its local
   writes and the ``delivered`` mark commit in one transaction, after checking the
   lease token is still ours (a fenced finalize). Outward calls go through
   ``Delivery.effect`` and its ledger.
3. **Fail**: the handler's uncommitted writes are rolled back. A retryable failure
   with budget left schedules a retry with backoff; anything else is dead-lettered.

Handlers are registered per event type in an app's ``hooks.py``::

    vati_outbox_handlers = {"case.registered": "vati_tax.some.module.handle_case_registered"}

A handler receives a ``Delivery`` and returns ``None``. It must be idempotent: a
replay after a crash calls it again with the same envelope and keys.
"""

from __future__ import annotations

import socket
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import frappe

from vati_tax.outbox import policy
from vati_tax.outbox.store import ATTEMPT, EFFECT, EVENT, log, naive, utcnow

Handler = Callable[["Delivery"], None]
HOOK = "vati_outbox_handlers"
_OVERRIDES: dict[str, Handler] = {}


@contextmanager
def override_handler(event_type: str, handler: Handler) -> Iterator[None]:
    """Route ``event_type`` to ``handler`` for the duration (tests and fixtures)."""
    previous = _OVERRIDES.get(event_type)
    _OVERRIDES[event_type] = handler
    try:
        yield
    finally:
        if previous is None:
            _OVERRIDES.pop(event_type, None)
        else:
            _OVERRIDES[event_type] = previous


def handler_for(event_type: str) -> Handler | None:
    if event_type in _OVERRIDES:
        return _OVERRIDES[event_type]
    # get_hooks returns [] (not {}) when no app declares the hook.
    hooks = frappe.get_hooks(HOOK) or {}
    paths = hooks.get(event_type) or [] if isinstance(hooks, dict) else []
    if isinstance(paths, str):
        paths = [paths]
    if len(paths) != 1:
        # None or ambiguous: both are configuration errors, not retryable.
        return None
    return frappe.get_attr(paths[0])


@dataclass
class Delivery:
    """What a handler sees: the envelope plus the effect ledger."""

    envelope: policy.Envelope
    event: str
    attempt_no: int
    lease_token: str
    _effects: list[str] = field(default_factory=list)

    @property
    def idempotency_key(self) -> str:
        return self.envelope.idempotency_key

    def effect(
        self,
        name: str,
        perform: Callable[[str], str | None],
        *,
        idempotent: bool = False,
        reconcile: Callable[[str], str | None] | None = None,
    ) -> str | None:
        """Run a named outward side effect at most once per event.

        ``perform(key)`` makes the call with a stable upstream idempotency key and
        returns the upstream reference. Set ``idempotent=True`` only if the
        upstream honours that key; then a replay after a crash calls again and the
        upstream deduplicates. Otherwise ``reconcile(key)`` looks the call up by
        key (returning its reference, or ``None`` when the upstream has no record);
        without it, an earlier call with an unknown outcome dead-letters the event
        rather than risk a duplicate.

        The ``started`` and ``completed`` ledger rows are committed immediately, so
        any local writes the handler made before this call commit with them.
        """
        policy.validate_effect_name(name)
        env = self.envelope
        key = policy.effect_key(env.tenant_id, self.event, name)
        upstream_key = policy.effect_idempotency_key(env.idempotency_key, name)
        row = frappe.db.get_value(
            EFFECT, {"effect_key": key}, ["name", "state", "result_ref"], as_dict=True
        )
        action = policy.decide_effect(
            row.state if row else None, idempotent=idempotent, can_reconcile=reconcile is not None
        )
        if action == "reuse":
            return row.result_ref or None
        if action == "unknown":
            raise policy.EffectOutcomeUnknown()
        if action == "reconcile" and reconcile is not None:
            found = reconcile(upstream_key)
            if found is not None:
                return self._complete(row.name, found)
            # The upstream confirms it never received the call: performing is safe.
        self._hold_lease()  # fence: a worker whose lease expired must not call out
        if row is None:
            ledger = frappe.get_doc(
                {
                    "doctype": EFFECT,
                    "tenant_id": env.tenant_id,
                    "event": self.event,
                    "effect_name": name,
                    "state": "started",
                    "idempotency_key": upstream_key,
                    "attempt_no": self.attempt_no,
                }
            )
            try:
                ledger.insert(ignore_permissions=True)
            except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
                frappe.db.rollback()
                raise policy.RetryableDeliveryError("effect_in_progress") from None
            row_name = ledger.name
        else:
            row_name = row.name
        frappe.db.commit()  # durable intent before the outward call; releases the lock
        ref = policy.validate_result_ref(perform(upstream_key))
        return self._complete(row_name, ref)

    def _complete(self, row_name: str, ref: str | None) -> str | None:
        frappe.db.set_value(
            EFFECT, row_name, {"state": "completed", "result_ref": ref, "completed_at": utcnow()}
        )
        frappe.db.commit()  # the call happened: remember it whatever happens next
        self._effects.append(row_name)
        return ref

    def _hold_lease(self) -> None:
        if not _owns_lease(self.event, self.lease_token):
            frappe.db.rollback()
            raise LeaseLost()


class LeaseLost(Exception):
    """Another worker reclaimed the event; this worker must stop without writing."""


def _owns_lease(event: str, lease_token: str) -> bool:
    row = frappe.db.sql(
        f"select status, lease_token from `tab{EVENT}` where name = %s for update",
        (event,),
        as_dict=True,
    )
    return bool(row) and row[0].status == "in_progress" and row[0].lease_token == lease_token


@dataclass(frozen=True)
class Claim:
    event: str
    attempt_no: int
    lease_token: str


def worker_name() -> str:
    return f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"[:140]


def _due(now: datetime, limit: int, tenant_id: str | None) -> list[Any]:
    tenant_filter = "and e.tenant_id = %(tenant)s" if tenant_id else ""
    return frappe.db.sql(
        f"""
        select e.name, e.tenant_id, e.status, e.attempts, e.max_attempts
        from `tab{EVENT}` e
        where (
            (e.status in ('pending', 'retry')
                and (e.next_attempt_at is null or e.next_attempt_at <= %(now)s))
            or (e.status = 'in_progress'
                and (e.lease_until is null or e.lease_until <= %(now)s))
        )
        {tenant_filter}
        and not exists (
            select 1 from `tab{EVENT}` p
            where p.ordering_key = e.ordering_key
            and p.`sequence` < e.`sequence`
            and p.status != 'delivered'
        )
        order by e.creation, e.`sequence`
        limit %(limit)s
        for update skip locked
        """,
        {"now": now, "limit": limit, "tenant": tenant_id},
        as_dict=True,
    )


def _close_started_attempts(event: str, now: datetime) -> None:
    """Attempts left ``started`` by a crashed holder become ``lease_lost``."""
    for name in frappe.get_all(
        ATTEMPT, filters={"event": event, "outcome": "started"}, pluck="name"
    ):
        frappe.db.set_value(
            ATTEMPT,
            name,
            {"outcome": "lease_lost", "error_code": "lease_expired", "finished_at": now},
        )


def claim(
    *,
    now: datetime,
    limit: int,
    worker: str,
    tenant_id: str | None = None,
    retry: policy.RetryPolicy = policy.DEFAULT_POLICY,
) -> list[Claim]:
    """Claim due events and persist their attempts (commits)."""
    frappe.db.commit()  # start from a fresh snapshot
    claims: list[Claim] = []
    for row in _due(now, limit, tenant_id):
        if row.status == "in_progress":
            _close_started_attempts(row.name, now)
            if row.attempts >= row.max_attempts:
                # Crashed on its last allowed attempt: stop instead of looping.
                frappe.db.set_value(
                    EVENT,
                    row.name,
                    {
                        "status": "dead_letter",
                        "dead_lettered_at": now,
                        "last_error_code": "lease_expired",
                        "lease_token": None,
                        "lease_until": None,
                        "locked_by": None,
                    },
                )
                log("claim", "dead_letter", tenant_id=row.tenant_id, event_id=row.name)
                continue
        # attempts is the retry budget used (reset by a manual retry); attempt_no
        # numbers every attempt row of the event and never repeats.
        attempt_no = _last_attempt_no(row.name) + 1
        token = str(uuid.uuid4())
        frappe.db.set_value(
            EVENT,
            row.name,
            {
                "status": "in_progress",
                "attempts": int(row.attempts) + 1,
                "lease_token": token,
                "lease_until": now + retry.lease,
                "locked_by": worker,
                "next_attempt_at": None,
            },
        )
        frappe.get_doc(
            {
                "doctype": ATTEMPT,
                "tenant_id": row.tenant_id,
                "event": row.name,
                "attempt_no": attempt_no,
                "outcome": "started",
                "worker": worker,
                "lease_token": token,
                "started_at": now,
            }
        ).insert(ignore_permissions=True)
        claims.append(Claim(row.name, attempt_no, token))
    frappe.db.commit()
    return claims


def _last_attempt_no(event: str) -> int:
    value = frappe.db.sql(
        f"select coalesce(max(attempt_no), 0) from `tab{ATTEMPT}` where event = %s", (event,)
    )[0][0]
    return int(value)


def _attempt(event: str, attempt_no: int) -> str | None:
    return frappe.db.get_value(ATTEMPT, {"event": event, "attempt_no": attempt_no}, "name")


def _finish_attempt(event: str, attempt_no: int, values: dict[str, Any]) -> None:
    name = _attempt(event, attempt_no)
    if name:
        frappe.db.set_value(ATTEMPT, name, values)


def deliver(
    item: Claim,
    *,
    now: datetime,
    retry: policy.RetryPolicy = policy.DEFAULT_POLICY,
    rand: Callable[[], float] | None = None,
) -> str:
    """Run one claimed event; returns ``delivered``, ``retry``, ``dead_letter`` or ``lease_lost``."""
    doc = frappe.get_doc(EVENT, item.event)
    try:
        delivery = Delivery(doc.envelope(), doc.name, item.attempt_no, item.lease_token)
        handler = handler_for(doc.event_type)
        if handler is None:
            raise policy.PermanentDeliveryError("no_handler")
        handler(delivery)
    except LeaseLost:
        return _lease_lost(item, now)
    except Exception as exc:  # classified and persisted; never reported as success
        frappe.db.rollback()
        return _fail(item, doc.tenant_id, policy.classify(exc), now, retry, rand)
    if not _owns_lease(item.event, item.lease_token):
        frappe.db.rollback()
        return _lease_lost(item, now)
    frappe.db.set_value(
        EVENT,
        item.event,
        {
            "status": "delivered",
            "delivered_at": now,
            "last_error_code": None,
            "lease_token": None,
            "lease_until": None,
            "locked_by": None,
        },
    )
    _finish_attempt(item.event, item.attempt_no, {"outcome": "succeeded", "finished_at": now})
    frappe.db.commit()
    log("deliver", "delivered", tenant_id=doc.tenant_id, event_id=doc.name, attempt=item.attempt_no)
    return "delivered"


def _lease_lost(item: Claim, now: datetime) -> str:
    _finish_attempt(
        item.event,
        item.attempt_no,
        {"outcome": "lease_lost", "error_code": "lease_lost", "finished_at": now},
    )
    frappe.db.commit()
    log("deliver", "lease_lost", event_id=item.event, attempt=item.attempt_no)
    return "lease_lost"


def _fail(
    item: Claim,
    tenant_id: str,
    failure: policy.Failure,
    now: datetime,
    retry: policy.RetryPolicy,
    rand: Callable[[], float] | None,
) -> str:
    if not _owns_lease(item.event, item.lease_token):
        frappe.db.rollback()
        return _lease_lost(item, now)
    used, allowed = frappe.db.get_value(EVENT, item.event, ["attempts", "max_attempts"])
    budget = policy.RetryPolicy(
        max_attempts=int(allowed or 1),
        base_delay=retry.base_delay,
        max_delay=retry.max_delay,
        lease=retry.lease,
    )
    decision = policy.after_failure(int(used), failure, now, budget, rand)
    next_at = naive(decision.next_attempt_at) if decision.next_attempt_at else None
    frappe.db.set_value(
        EVENT,
        item.event,
        {
            "status": decision.status,
            "next_attempt_at": next_at,
            "dead_lettered_at": now if decision.status == "dead_letter" else None,
            "last_error_code": failure.code,
            "lease_token": None,
            "lease_until": None,
            "locked_by": None,
        },
    )
    _finish_attempt(
        item.event,
        item.attempt_no,
        {
            "outcome": "failed",
            "error_code": failure.code,
            "retryable": int(failure.retryable),
            "finished_at": now,
            "next_attempt_at": next_at,
        },
    )
    frappe.db.commit()
    log(
        "deliver",
        decision.status,
        tenant_id=tenant_id,
        event_id=item.event,
        attempt=item.attempt_no,
        error_code=failure.code,
    )
    return decision.status


def dispatch(
    *,
    limit: int = 20,
    now: datetime | None = None,
    tenant_id: str | None = None,
    worker: str | None = None,
    retry: policy.RetryPolicy = policy.DEFAULT_POLICY,
    rand: Callable[[], float] | None = None,
) -> dict[str, int]:
    """Claim and deliver up to ``limit`` due events; returns counts by outcome.

    Claims repeat until nothing is due or the limit is reached, so the next event
    of a case is picked up in the same run once its predecessor is delivered.
    """
    moment = naive(now) if now else utcnow()
    name = worker or worker_name()
    counts: dict[str, int] = {}
    handled = 0
    while handled < limit:
        batch = claim(
            now=moment, limit=limit - handled, worker=name, tenant_id=tenant_id, retry=retry
        )
        if not batch:
            break
        for item in batch:
            outcome = deliver(item, now=moment, retry=retry, rand=rand)
            counts[outcome] = counts.get(outcome, 0) + 1
        handled += len(batch)
    return counts


def dispatch_scheduled() -> None:
    """Scheduler entry point (hooks.py ``scheduler_events``)."""
    counts = dispatch(limit=50)
    if counts:
        log("dispatch", "ran", **counts)
