"""Bench tests for the durable outbox and idempotent workers (TAX-033).

These need a connected Frappe site and run with
``bench --site <site> run-tests --module vati_tax.outbox.test_outbox_bench``
(``scripts/frappe_dev.py verify`` does this on a disposable site). Under plain
``unittest`` without a site they are skipped, not passed.

The worker commits, so every test uses fresh synthetic tenants and dispatches
only its own tenant's events. A crash is simulated by raising ``SimulatedCrash``
(a ``BaseException``, which the worker does not catch) and rolling back what was
not committed, as a killed process would. Fake upstreams live in memory, outside
the database, so they keep what they received across the simulated crash.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

try:
    import frappe
    from frappe.tests import IntegrationTestCase

    ON_SITE = bool(getattr(frappe.local, "site", None))
except ImportError:  # dev.py check runs without Frappe installed
    frappe = None
    IntegrationTestCase = unittest.TestCase
    ON_SITE = False

from vati_tax.ids.canonical import (
    CaseNotFound,
    IdempotencyConflict,
    InvalidMapping,
    TenantBoundaryError,
)
from vati_tax.outbox.policy import (
    IdempotencyInProgress,
    InvalidTransition,
    PermanentDeliveryError,
    RetryableDeliveryError,
    RetryPolicy,
)

T0 = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
FAST = RetryPolicy(
    max_attempts=3,
    base_delay=timedelta(seconds=10),
    max_delay=timedelta(seconds=40),
    lease=timedelta(minutes=5),
)
AFTER_LEASE = FAST.lease + timedelta(seconds=1)
CASE_TYPE = "case.registered"
REQUEST_TYPE = "evidence_request.recorded"


def uid() -> str:
    return str(uuid.uuid4())


def key() -> str:
    return "synthetic:test:" + uuid.uuid4().hex


class SimulatedCrash(BaseException):
    """The worker process dies: nothing after this point runs or commits."""


class FakeUpstream:
    """In-memory stand-in for Jube (cases) or the taxpayer channel (evidence requests).

    ``honours_keys`` models an API that deduplicates on Idempotency-Key. Without
    it every call creates a new record, like a non-idempotent endpoint.
    ``crash_after_call`` kills the worker right after the upstream accepted.
    """

    def __init__(self, prefix: str, honours_keys: bool = False):
        self.prefix = prefix
        self.honours_keys = honours_keys
        self.records: list[tuple[str, str]] = []  # (idempotency key, upstream id)
        self.calls = 0
        self.crash_after_call = 0

    def create(self, idem_key: str) -> str:
        self.calls += 1
        if self.honours_keys:
            for k, ref in self.records:
                if k == idem_key:
                    return ref
        ref = f"{self.prefix}-{len(self.records) + 1:04d}"
        self.records.append((idem_key, ref))
        if self.crash_after_call:
            self.crash_after_call -= 1
            raise SimulatedCrash()
        return ref

    def lookup(self, idem_key: str) -> str | None:
        return next((ref for k, ref in self.records if k == idem_key), None)


@unittest.skipUnless(ON_SITE, "needs a Frappe site: bench run-tests --module")
class OutboxBenchTests(IntegrationTestCase):
    def setUp(self) -> None:
        from vati_tax.ids import mapping
        from vati_tax.outbox import store, worker

        self.mapping, self.store, self.worker = mapping, store, worker
        self.tenant, self.other_tenant = uid(), uid()
        self.case_id = self.new_case(self.tenant)
        frappe.db.commit()

    def tearDown(self) -> None:
        frappe.db.rollback()
        frappe.set_user("Administrator")

    # --- helpers ---------------------------------------------------------------------

    def new_case(self, tenant: str) -> str:
        return self.mapping.register_case(tenant_id=tenant, idempotency_key=key())["case_id"]

    def enqueue(self, type: str = REQUEST_TYPE, case_id: str | None = None, **overrides):
        args: dict[str, Any] = dict(
            tenant_id=self.tenant,
            case_id=case_id or self.case_id,
            type=type,
            payload={"evidence_request_id": "ER-SYN-" + uuid.uuid4().hex[:6]},
            idempotency_key=key(),
            correlation_id=uid(),
            max_attempts=FAST.max_attempts,
        )
        args.update(overrides)
        return self.store.enqueue(**args)

    def dispatch(self, at: datetime = T0, **kwargs):
        return self.worker.dispatch(
            now=at, tenant_id=self.tenant, retry=FAST, rand=lambda: 1.0, **kwargs
        )

    def crash_dispatch(self, at: datetime = T0) -> None:
        with self.assertRaises(SimulatedCrash):
            self.dispatch(at)
        frappe.db.rollback()  # the dead process loses its uncommitted work

    def event(self, event_id: str):
        return frappe.get_doc("Vati Outbox Event", event_id)

    def attempts(self, event_id: str) -> list[str]:
        return frappe.get_all(
            "Vati Outbox Attempt",
            filters={"event": event_id},
            order_by="attempt_no asc",
            pluck="outcome",
        )

    def link_count(self) -> int:
        return frappe.db.count("Vati Service Link", {"case_id": self.case_id})

    def outward_handler(self, upstream: FakeUpstream, **effect_kwargs):
        """Handler: create the upstream record once, then record the link locally."""

        def handle(delivery) -> None:
            ref = delivery.effect(
                "upstream.create",
                upstream.create,
                reconcile=upstream.lookup if effect_kwargs.get("reconcile") else None,
                idempotent=upstream.honours_keys,
            )
            if delivery.envelope.type == CASE_TYPE:
                self.mapping.record_link(
                    tenant_id=delivery.envelope.tenant_id,
                    case_id=delivery.envelope.case_id,
                    service="jube",
                    external_type="case",
                    external_id=ref,
                    source_event_id=delivery.envelope.event_id,
                )
            if effect_kwargs.get("crash_before_finalize"):
                effect_kwargs["crash_before_finalize"] -= 1
                raise SimulatedCrash()

        return handle

    # --- transactional enqueue ---------------------------------------------------

    def test_event_commits_or_rolls_back_with_its_domain_change(self):
        frappe.db.savepoint("tax033_domain")
        case_id = self.new_case(self.tenant)
        event = self.enqueue(CASE_TYPE, case_id=case_id)
        frappe.db.rollback(save_point="tax033_domain")
        self.assertFalse(frappe.db.exists("Vati Case Identity", case_id))
        self.assertFalse(frappe.db.exists("Vati Outbox Event", event["event_id"]))
        self.assertEqual(self.dispatch(), {})  # the worker never sees a rolled-back event

        case_id = self.new_case(self.tenant)
        event = self.enqueue(CASE_TYPE, case_id=case_id)
        frappe.db.commit()
        self.assertTrue(frappe.db.exists("Vati Case Identity", case_id))
        self.assertEqual(self.event(event["event_id"]).status, "pending")
        self.assertEqual(self.event(event["event_id"]).sync_state()["state"], "pending")

    def test_enqueue_is_idempotent_and_rejects_a_changed_request(self):
        k = key()
        first = self.enqueue(idempotency_key=k, payload={"evidence_request_id": "ER-SYN-1"})
        again = self.enqueue(idempotency_key=k, payload={"evidence_request_id": "ER-SYN-1"})
        self.assertFalse(first["replayed"])
        self.assertTrue(again["replayed"])
        self.assertEqual(again["event_id"], first["event_id"])
        with self.assertRaises(IdempotencyConflict):
            self.enqueue(idempotency_key=k, payload={"evidence_request_id": "ER-SYN-2"})
        self.assertEqual(frappe.db.count("Vati Outbox Event", {"tenant_id": self.tenant}), 1)
        # The key is scoped to the tenant.
        other_case = self.new_case(self.other_tenant)
        other = self.enqueue(
            tenant_id=self.other_tenant,
            case_id=other_case,
            idempotency_key=k,
            payload={"evidence_request_id": "ER-SYN-1"},
        )
        self.assertFalse(other["replayed"])

    def test_database_keys_hold_without_the_service(self):
        first = self.enqueue()
        frappe.db.commit()
        stored = self.event(first["event_id"])
        clone = frappe.get_doc(
            {
                "doctype": "Vati Outbox Event",
                "event_id": uid(),
                "tenant_id": self.tenant,
                "case_id": self.case_id,
                "event_type": stored.event_type,
                "correlation_id": uid(),
                "source_service": "vati",
                "occurred_at": stored.occurred_at,
                "idempotency_key": stored.idempotency_key,
                "payload_json": stored.payload_json,
                "status": "pending",
                "sequence": 99,
            }
        )
        with self.assertRaises(frappe.UniqueValidationError):  # same dedupe key
            clone.insert(ignore_permissions=True)
        frappe.db.rollback()
        stored = self.event(first["event_id"])
        stored.payload_json = '{"evidence_request_id":"ER-SYN-CHANGED"}'
        with self.assertRaises(frappe.ValidationError):  # the envelope is immutable
            stored.save(ignore_permissions=True)

    def test_events_are_tenant_scoped(self):
        foreign_case = self.new_case(self.other_tenant)
        with self.assertRaises(TenantBoundaryError):
            self.enqueue(case_id=foreign_case)
        with self.assertRaises(CaseNotFound):
            self.enqueue(case_id=uid())
        with self.assertRaises(InvalidMapping):
            self.enqueue(payload={"document_text": "x" * 300})
        event = self.enqueue()
        frappe.db.commit()
        with self.assertRaises(TenantBoundaryError):
            self.store.delivery_state(tenant_id=self.other_tenant, event_id=event["event_id"])
        with self.assertRaises(TenantBoundaryError):
            self.store.retry_dead_letter(
                tenant_id=self.other_tenant, event_id=event["event_id"], actor="ops"
            )
        # Another tenant's dispatch never touches this tenant's events.
        self.worker.dispatch(now=T0, tenant_id=self.other_tenant, retry=FAST)
        self.assertEqual(self.event(event["event_id"]).attempts, 0)

    # --- crash and replay ---------------------------------------------------------

    def test_crash_after_local_write_replays_without_a_duplicate_case(self):
        registrations = []

        def handle(delivery) -> None:
            result = self.mapping.register_case(
                tenant_id=delivery.envelope.tenant_id,
                idempotency_key=delivery.idempotency_key + ":case",
                taxpayer_id=None,
            )
            registrations.append(result["replayed"])
            frappe.db.commit()  # worst case: the handler's write is already durable
            if len(registrations) == 1:
                raise SimulatedCrash()

        event = self.enqueue(CASE_TYPE)
        frappe.db.commit()
        before = frappe.db.count("Vati Case Identity", {"tenant_id": self.tenant})
        with self.worker.override_handler(CASE_TYPE, handle):
            self.crash_dispatch()
            self.assertEqual(self.event(event["event_id"]).status, "in_progress")
            self.assertEqual(self.dispatch(T0 + timedelta(seconds=30)), {})  # lease held
            self.assertEqual(self.dispatch(T0 + AFTER_LEASE), {"delivered": 1})
        self.assertEqual(registrations, [False, True])
        self.assertEqual(
            frappe.db.count("Vati Case Identity", {"tenant_id": self.tenant}), before + 1
        )
        self.assertEqual(self.attempts(event["event_id"]), ["lease_lost", "succeeded"])

    def test_crash_after_outward_call_reconciles_instead_of_resending(self):
        for event_type, prefix in ((CASE_TYPE, "JUBE"), (REQUEST_TYPE, "EVREQ")):
            with self.subTest(event_type=event_type):
                self.case_id = self.new_case(self.tenant)
                upstream = FakeUpstream(prefix)  # not idempotent
                upstream.crash_after_call = 1
                event = self.enqueue(event_type)
                frappe.db.commit()
                handler = self.outward_handler(upstream, reconcile=True)
                with self.worker.override_handler(event_type, handler):
                    self.crash_dispatch()
                    self.assertEqual(self.dispatch(T0 + AFTER_LEASE), {"delivered": 1})
                self.assertEqual(len(upstream.records), 1)  # exactly one outward record
                self.assertEqual(upstream.calls, 1)  # the replay looked it up, no re-send
                if event_type == CASE_TYPE:
                    self.assertEqual(self.link_count(), 1)
                effect = frappe.get_all(
                    "Vati Outbox Effect",
                    filters={"event": event["event_id"]},
                    fields=["state", "result_ref", "attempt_no"],
                )
                self.assertEqual(
                    [(e.state, e.result_ref, e.attempt_no) for e in effect],
                    [("completed", f"{prefix}-0001", 1)],
                )

    def test_crash_after_outward_call_reuses_an_idempotent_upstream(self):
        upstream = FakeUpstream("JUBE", honours_keys=True)
        upstream.crash_after_call = 1
        event = self.enqueue(CASE_TYPE)
        frappe.db.commit()
        with self.worker.override_handler(CASE_TYPE, self.outward_handler(upstream)):
            self.crash_dispatch()
            self.assertEqual(self.dispatch(T0 + AFTER_LEASE), {"delivered": 1})
        self.assertEqual(upstream.calls, 2)  # re-sent with the same key...
        self.assertEqual(len(upstream.records), 1)  # ...and deduplicated upstream
        self.assertEqual(self.link_count(), 1)
        self.assertEqual(self.event(event["event_id"]).sync_state()["state"], "synced")

    def test_crash_after_completed_effect_never_calls_again(self):
        upstream = FakeUpstream("EVREQ")
        event = self.enqueue(REQUEST_TYPE)
        frappe.db.commit()
        handler = self.outward_handler(upstream, crash_before_finalize=1)
        with self.worker.override_handler(REQUEST_TYPE, handler):
            self.crash_dispatch()
            self.assertEqual(self.dispatch(T0 + AFTER_LEASE), {"delivered": 1})
            # Replaying a delivered event is a no-op.
            self.assertEqual(self.dispatch(T0 + 2 * AFTER_LEASE), {})
        self.assertEqual((upstream.calls, len(upstream.records)), (1, 1))
        self.assertEqual(self.attempts(event["event_id"]), ["lease_lost", "succeeded"])

    def test_unknown_outcome_dead_letters_until_an_operator_reconciles(self):
        upstream = FakeUpstream("EVREQ")  # not idempotent and no lookup
        upstream.crash_after_call = 1
        event = self.enqueue(REQUEST_TYPE)
        frappe.db.commit()
        with self.worker.override_handler(REQUEST_TYPE, self.outward_handler(upstream)):
            self.crash_dispatch()
            self.assertEqual(self.dispatch(T0 + AFTER_LEASE), {"dead_letter": 1})
            doc = self.event(event["event_id"])
            self.assertEqual(doc.last_error_code, "effect_outcome_unknown")
            self.assertEqual(doc.sync_state()["state"], "failed")
            self.assertEqual(upstream.calls, 1)
            # The operator finds EVREQ-0001 upstream and records it, then retries.
            self.store.resolve_effect(
                tenant_id=self.tenant,
                event_id=event["event_id"],
                effect_name="upstream.create",
                result_ref="EVREQ-0001",
                actor="ops@example.test",
            )
            self.store.retry_dead_letter(
                tenant_id=self.tenant, event_id=event["event_id"], actor="ops@example.test"
            )
            frappe.db.commit()
            self.assertEqual(self.dispatch(T0 + 2 * AFTER_LEASE), {"delivered": 1})
        self.assertEqual((upstream.calls, len(upstream.records)), (1, 1))
        self.assertEqual(self.event(event["event_id"]).manual_retries, 1)

    def test_released_effect_is_performed_once_after_manual_retry(self):
        upstream = FakeUpstream("EVREQ")
        event = self.enqueue(REQUEST_TYPE)
        frappe.db.commit()

        def refused(_key: str) -> str:
            # The upstream rejected the request before creating anything, but the
            # worker died before it could record that.
            raise SimulatedCrash()

        def handle(delivery) -> None:
            delivery.effect("upstream.create", refused)

        with self.worker.override_handler(REQUEST_TYPE, handle):
            self.crash_dispatch()
            self.assertEqual(self.dispatch(T0 + AFTER_LEASE), {"dead_letter": 1})
        self.store.release_effect(
            tenant_id=self.tenant,
            event_id=event["event_id"],
            effect_name="upstream.create",
            actor="ops@example.test",
        )
        self.store.retry_dead_letter(
            tenant_id=self.tenant, event_id=event["event_id"], actor="ops@example.test"
        )
        frappe.db.commit()
        with self.worker.override_handler(REQUEST_TYPE, self.outward_handler(upstream)):
            self.assertEqual(self.dispatch(T0 + 2 * AFTER_LEASE), {"delivered": 1})
        self.assertEqual(len(upstream.records), 1)

    def test_stale_worker_is_fenced_after_its_lease_expires(self):
        upstream = FakeUpstream("JUBE")
        event = self.enqueue(CASE_TYPE)
        frappe.db.commit()
        stale = self.worker.claim(
            now=T0.replace(tzinfo=None),
            limit=5,
            worker="w-stale",
            tenant_id=self.tenant,
            retry=FAST,
        )
        self.assertEqual(len(stale), 1)
        # A second worker cannot take the event while the lease is live...
        self.assertEqual(
            self.worker.claim(
                now=T0.replace(tzinfo=None),
                limit=5,
                worker="w-other",
                tenant_id=self.tenant,
                retry=FAST,
            ),
            [],
        )
        # ...but takes it over once the stale worker's lease has expired.
        later = (T0 + AFTER_LEASE).replace(tzinfo=None)
        fresh = self.worker.claim(
            now=later, limit=5, worker="w-fresh", tenant_id=self.tenant, retry=FAST
        )
        self.assertEqual([c.attempt_no for c in fresh], [2])
        with self.worker.override_handler(CASE_TYPE, self.outward_handler(upstream)):
            self.assertEqual(self.worker.deliver(stale[0], now=later, retry=FAST), "lease_lost")
            self.assertEqual(upstream.calls, 0)  # the fenced worker never called out
            self.assertEqual(self.worker.deliver(fresh[0], now=later, retry=FAST), "delivered")
        self.assertEqual(len(upstream.records), 1)
        self.assertEqual(self.link_count(), 1)
        self.assertEqual(self.attempts(event["event_id"]), ["lease_lost", "succeeded"])

    # --- retries, dead letters and ordering --------------------------------------

    def test_poison_message_dead_letters_after_bounded_backoff(self):
        calls = []

        def flaky(delivery) -> None:
            calls.append(delivery.attempt_no)
            self.mapping.register_case(
                tenant_id=self.tenant, idempotency_key=delivery.idempotency_key + ":x"
            )
            raise RetryableDeliveryError("upstream_unavailable")

        event = self.enqueue(REQUEST_TYPE)
        frappe.db.commit()
        cases_before = frappe.db.count("Vati Case Identity", {"tenant_id": self.tenant})
        with self.worker.override_handler(REQUEST_TYPE, flaky):
            self.assertEqual(self.dispatch(T0), {"retry": 1})
            doc = self.event(event["event_id"])
            self.assertEqual(
                frappe.utils.get_datetime(doc.next_attempt_at),
                (T0 + timedelta(seconds=10)).replace(tzinfo=None),
            )
            self.assertEqual(self.dispatch(T0 + timedelta(seconds=9)), {})  # backoff holds
            self.assertEqual(self.dispatch(T0 + timedelta(seconds=10)), {"retry": 1})
            self.assertEqual(self.dispatch(T0 + timedelta(seconds=29)), {})  # 20 s backoff
            self.assertEqual(self.dispatch(T0 + timedelta(seconds=30)), {"dead_letter": 1})
            self.assertEqual(self.dispatch(T0 + timedelta(days=1)), {})
        self.assertEqual(calls, [1, 2, 3])
        doc = self.event(event["event_id"])
        self.assertEqual(
            (doc.status, doc.attempts, doc.last_error_code),
            ("dead_letter", 3, "upstream_unavailable"),
        )
        self.assertEqual(self.attempts(event["event_id"]), ["failed"] * 3)
        # Each failed attempt's local writes were rolled back.
        self.assertEqual(
            frappe.db.count("Vati Case Identity", {"tenant_id": self.tenant}), cases_before
        )

    def test_permanent_failure_and_missing_handler_dead_letter_at_once(self):
        def invalid(delivery) -> None:
            raise PermanentDeliveryError("invalid_domain_input")

        bad = self.enqueue(REQUEST_TYPE)
        orphan = self.enqueue("finding.recorded", case_id=self.new_case(self.tenant))
        frappe.db.commit()
        with self.worker.override_handler(REQUEST_TYPE, invalid):
            self.assertEqual(self.dispatch(), {"dead_letter": 2})
        self.assertEqual(self.event(bad["event_id"]).last_error_code, "invalid_domain_input")
        self.assertEqual(self.event(orphan["event_id"]).last_error_code, "no_handler")

    def test_crash_loop_is_bounded(self):
        calls = []

        def crashing(delivery) -> None:
            calls.append(delivery.attempt_no)
            raise SimulatedCrash()

        event = self.enqueue(REQUEST_TYPE)
        frappe.db.commit()
        at = T0
        with self.worker.override_handler(REQUEST_TYPE, crashing):
            for _ in range(FAST.max_attempts):
                self.crash_dispatch(at)
                at += AFTER_LEASE
            self.assertEqual(self.dispatch(at), {})
        self.assertEqual(calls, [1, 2, 3])
        doc = self.event(event["event_id"])
        self.assertEqual((doc.status, doc.last_error_code), ("dead_letter", "lease_expired"))
        self.assertEqual(self.attempts(event["event_id"]), ["lease_lost"] * 3)

    def test_manual_retry_rules(self):
        event = self.enqueue(REQUEST_TYPE)
        frappe.db.commit()
        with self.assertRaises(InvalidTransition):
            self.store.retry_dead_letter(
                tenant_id=self.tenant, event_id=event["event_id"], actor="o"
            )
        self.worker.claim(
            now=self.store.utcnow(), limit=1, worker="w", tenant_id=self.tenant, retry=FAST
        )
        with self.assertRaises(IdempotencyInProgress) as ctx:
            self.store.retry_dead_letter(
                tenant_id=self.tenant, event_id=event["event_id"], actor="o"
            )
        self.assertEqual(ctx.exception.http_status, 409)

    def test_events_of_one_case_are_delivered_in_order(self):
        seen = []
        failures = {"left": 1}

        def handle(delivery) -> None:
            label = delivery.envelope.payload["evidence_request_id"]
            if label == "ER-A1" and failures["left"]:
                failures["left"] -= 1
                raise RetryableDeliveryError("upstream_timeout")
            seen.append(label)

        other_case = self.new_case(self.tenant)
        for label in ("ER-A1", "ER-A2", "ER-A3"):
            self.enqueue(REQUEST_TYPE, payload={"evidence_request_id": label})
        self.enqueue(REQUEST_TYPE, case_id=other_case, payload={"evidence_request_id": "ER-B1"})
        frappe.db.commit()
        with self.worker.override_handler(REQUEST_TYPE, handle):
            # A1 fails; A2/A3 wait behind it, while case B is not blocked.
            self.assertEqual(self.dispatch(T0), {"retry": 1, "delivered": 1})
            self.assertEqual(seen, ["ER-B1"])
            self.assertEqual(self.dispatch(T0 + timedelta(minutes=1)), {"delivered": 3})
        self.assertEqual(seen, ["ER-B1", "ER-A1", "ER-A2", "ER-A3"])

    def test_dead_letter_blocks_later_events_of_the_case_until_retried(self):
        seen = []
        broken = {"on": True}

        def handle(delivery) -> None:
            label = delivery.envelope.payload["evidence_request_id"]
            if label == "ER-1" and broken["on"]:
                raise PermanentDeliveryError("invalid_domain_input")
            seen.append(label)

        first = self.enqueue(REQUEST_TYPE, payload={"evidence_request_id": "ER-1"})
        self.enqueue(REQUEST_TYPE, payload={"evidence_request_id": "ER-2"})
        frappe.db.commit()
        with self.worker.override_handler(REQUEST_TYPE, handle):
            self.assertEqual(self.dispatch(T0), {"dead_letter": 1})
            self.assertEqual(self.dispatch(T0 + timedelta(hours=1)), {})
            broken["on"] = False
            self.store.retry_dead_letter(
                tenant_id=self.tenant, event_id=first["event_id"], actor="o"
            )
            frappe.db.commit()
            self.assertEqual(self.dispatch(T0 + timedelta(hours=2)), {"delivered": 2})
        self.assertEqual(seen, ["ER-1", "ER-2"])

    # --- desk permissions -------------------------------------------------------

    def test_desk_access_is_read_only_and_tenant_isolated(self):
        from vati_tax.identity.provision import ensure_tenant, grant_membership
        from vati_tax.outbox import api

        event = self.enqueue(REQUEST_TYPE)
        ensure_tenant(self.tenant, "Synthetic tenant A")
        ensure_tenant(self.other_tenant, "Synthetic tenant B")
        run = uuid.uuid4().hex[:8]
        users = {}
        people: tuple[tuple[str, str, Any, str | None], ...] = (
            ("sup-a", self.tenant, "supervisor", None),
            ("sup-b", self.other_tenant, "supervisor", None),
            ("tp-a", self.tenant, "taxpayer", uid()),
        )
        for name, tenant, role, taxpayer in people:
            email = f"tax033-{name}-{run}@example.test"
            frappe.get_doc(
                {
                    "doctype": "User",
                    "email": email,
                    "first_name": "Synthetic",
                    "send_welcome_email": 0,
                }
            ).insert(ignore_permissions=True)
            grant_membership(email, tenant, role, taxpayer)
            users[name] = email
        frappe.db.commit()
        doc = self.event(event["event_id"])
        try:
            frappe.set_user(users["sup-a"])
            self.assertTrue(frappe.has_permission("Vati Outbox Event", "read", doc=doc))
            self.assertFalse(frappe.has_permission("Vati Outbox Event", "write", doc=doc))
            self.assertFalse(frappe.has_permission("Vati Outbox Event", "create"))
            with self.assertRaises(frappe.PermissionError):
                api.retry_event(self.tenant, event["event_id"])
            frappe.set_user(users["sup-b"])
            self.assertFalse(frappe.has_permission("Vati Outbox Event", "read", doc=doc))
            self.assertNotIn(
                event["event_id"],
                frappe.get_list("Vati Outbox Event", pluck="name", limit_page_length=0),
            )
            frappe.set_user(users["tp-a"])
            self.assertFalse(frappe.has_permission("Vati Outbox Event", "read", doc=doc))
        finally:
            frappe.set_user("Administrator")
