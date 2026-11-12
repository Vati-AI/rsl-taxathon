"""Bench tests for the Frappe to Jube bridge (TAX-039).

Run on a disposable site: ``python3 scripts/frappe_dev.py verify``. Jube is
replaced with in-process fakes through ``store.set_factories``; the real adapter
and risk invoker are covered by tests/integration/test_jube_bridge.py.

Covers the task's verification list through Frappe: a sent claim reaches the
risk and case services exactly once, a webhook replay is a no-op, a stale update
is refused, a mirror write never loops back into Jube, and a taxpayer sees only
the review status. Every tenant, taxpayer and amount is synthetic.
"""

from __future__ import annotations

import json
import types
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

try:
    import frappe
    from frappe.tests import IntegrationTestCase
    from werkzeug.test import EnvironBuilder
    from werkzeug.wrappers import Request

    from vati_tax.bridge import api, rules, store
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import TenantScopeDenied, clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.ids import mapping
    from vati_tax.ids.canonical import CaseNotFound, IdempotencyConflict
    from vati_tax.outbox import policy as outbox_policy
    from vati_tax.outbox import worker
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

RUN = uuid.uuid4().hex[:8]
SUBMISSION = "Vati Submission"
OUTBOX = "Vati Outbox Event"
FAST = outbox_policy.RetryPolicy(
    max_attempts=3, base_delay=timedelta(seconds=10), max_delay=timedelta(seconds=20)
)
INTERNAL_WORDS = ("jube", "escalat", "risk", "rule", "activation", "case_status", "officer")


def uid() -> str:
    return str(uuid.uuid4())


def email(name: str) -> str:
    return f"tax039-{name}-{RUN}@example.test"


def make_user(name: str) -> str:
    user = email(name)
    frappe.get_doc(
        {"doctype": "User", "email": user, "first_name": f"Synthetic {name}",
         "send_welcome_email": 0}
    ).insert(ignore_permissions=True)  # fmt: skip
    return user


class UpstreamError(Exception):
    """Shape of the adapter's JubeAdapterError: code, reason, retryable."""

    def __init__(self, reason: str, retryable: bool):
        super().__init__(reason)
        self.code = "upstream_unavailable"
        self.reason = reason
        self.retryable = retryable


class FakeJube:
    """Lifecycle and risk ports in one, keeping Jube's ordering rule:
    a case can be raised only after the risk model has been invoked for it."""

    def __init__(self) -> None:
        self.archived: set[str] = set()
        self.cases: dict[str, types.SimpleNamespace] = {}
        self.invocations: list[dict[str, Any]] = []
        self.creates = 0
        self.fail_ensure: list[Exception] = []

    # RiskEvaluator
    def invoke_declared(self, *, case_id, taxpayer_id, period_end, facts, correlation_id):
        self.invocations.append({"case_id": case_id, "facts": dict(facts), "period": period_end})
        self.archived.add(case_id)
        return f"jube-invoke:{case_id}:0"

    # CaseLifecycle
    def ensure_case(self, tenant_id, case_id, *, correlation_id, source_event_id=None):
        if self.fail_ensure:
            raise self.fail_ensure.pop(0)
        if case_id not in self.cases:
            if case_id not in self.archived:
                raise UpstreamError("risk_evaluation_missing", retryable=True)
            self.creates += 1
            self.cases[case_id] = types.SimpleNamespace(
                jube_case_id=100 + self.creates,
                status="RECEIVED",
                escalated=False,
                closed_state="open",
            )
            mapping.record_link(
                tenant_id=tenant_id,
                case_id=case_id,
                service="jube",
                external_type="case",
                external_id=str(self.cases[case_id].jube_case_id),
            )
        return types.SimpleNamespace(case=self.cases[case_id], changed=False)

    def get_case(self, tenant_id, case_id, *, correlation_id):
        if case_id not in self.cases:
            raise UpstreamError("no_jube_link", retryable=False)
        return self.cases[case_id]


class BridgeBenchTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.users = {name: make_user(name) for name in ("taxpayer-a", "taxpayer-b", "auditor")}
        cls.users["auditor-other"] = make_user("auditor-other")
        cls.users["refund-supervisor"] = make_user("refund-supervisor")
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        store.set_factories(None, None)
        frappe.local.request = None
        super().tearDownClass()

    def setUp(self):
        # A fresh tenant per test: the outbox worker commits, so tenants keep tests apart.
        frappe.set_user("Administrator")
        clear_principal_cache()
        self.tenant = ensure_tenant(uid(), "Synthetic bridge tenant")
        self.other = ensure_tenant(uid(), "Synthetic bridge tenant two")
        self.tp_a = self.taxpayer(self.tenant, "Synthetic Mountain Wholesale")
        self.tp_b = self.taxpayer(self.tenant, "Synthetic Border Retail")
        grant_membership(self.users["taxpayer-a"], self.tenant, policy.TAXPAYER, self.tp_a)
        grant_membership(self.users["taxpayer-b"], self.tenant, policy.TAXPAYER, self.tp_b)
        grant_membership(self.users["auditor"], self.tenant, policy.AUDITOR)
        grant_membership(self.users["auditor-other"], self.other, policy.AUDITOR)
        # TAX-142: refund-claim cases are refund-team data.
        grant_membership(self.users["refund-supervisor"], self.tenant, policy.SUPERVISOR,
                         refund_team=True)  # fmt: skip
        self.users["service"] = ensure_service_identity(email("service"), [self.tenant])
        self.users["service-other"] = ensure_service_identity(email("service-o"), [self.other])
        frappe.db.commit()
        self.jube = FakeJube()
        store.set_factories(lambda _t: self.jube, lambda _t: self.jube)

    def tearDown(self):
        frappe.set_user("Administrator")
        store.set_factories(None, None)
        frappe.local.request = None
        frappe.db.rollback()

    # --- helpers ---------------------------------------------------------------------

    @staticmethod
    def taxpayer(tenant: str, name: str) -> str:
        tin = str(400000000 + uuid.uuid4().int % 99999999)
        return (
            frappe.get_doc(
                {
                    "doctype": "Vati Taxpayer",
                    "tenant_id": tenant,
                    "display_name": name,
                    "taxpayer_type": "company",
                    "tin": tin,
                }
            )
            .insert()
            .name  # fmt: skip
        )

    def as_user(self, name: str) -> str:
        frappe.set_user(self.users[name])
        clear_principal_cache()
        return self.users[name]

    def send_claim(self, who: str = "taxpayer-a", amount: str = "15000.00") -> Any:
        """A taxpayer's own refund claim, sent the way the portal sends it."""
        self.as_user(who)
        taxpayer = self.tp_a if who == "taxpayer-a" else self.tp_b
        sub = frappe.get_doc(
            {"doctype": SUBMISSION, "tenant_id": self.tenant, "taxpayer_id": taxpayer,
             "submission_type": "refund_claim", "period": "2026-09", "declaration": 1,
             "contact_email": "accounts@example.test", "source": "portal"}
        ).insert()  # fmt: skip
        frappe.get_doc(
            {"doctype": "Vati Refund Claim", "submission_id": sub.name, "tenant_id": self.tenant,
             "taxpayer_id": taxpayer, "claimed_amount": amount,
             "claim_reason": "excess_input_vat", "evidence_ids": "[]"}
        ).insert()  # fmt: skip
        sub.reload()
        sub.status = "submitted"
        sub.save()
        frappe.set_user("Administrator")
        frappe.db.commit()
        return frappe.get_doc(SUBMISSION, sub.name)

    def events(self, case_id: str, *, everything: bool = False) -> list[Any]:
        """The case's bridge events; TAX-121's assurance step (first per send) only if asked."""
        filters: dict[str, Any] = {"case_id": case_id}
        if not everything:
            filters["event_type"] = ["!=", "assurance.requested"]
        return frappe.get_all(
            OUTBOX,
            filters=filters,
            fields=["name", "event_type", "status", "source_service", "payload_json",
                    "last_error_code"],
            order_by="sequence asc",
        )  # fmt: skip

    def dispatch(self, at: datetime | None = None) -> dict[str, int]:
        moment = at or datetime.now(UTC)
        return worker.dispatch(now=moment, tenant_id=self.tenant, retry=FAST, rand=lambda: 1.0)

    def envelope(self, case_id: str, status: str, at: datetime, **kw: Any) -> dict[str, Any]:
        payload = {
            "jube_case_id": kw.pop("jube_case_id", "101"),
            "status": status,
            "escalated": kw.pop("escalated", False),
            "closed": status == "RESOLVED",
            "observed_at": at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
        payload.update(kw)
        return {
            "schema_version": "1",
            "event_id": uid(),
            "tenant_id": self.tenant,
            "case_id": case_id,
            "correlation_id": uid(),
            "type": "case.status_projected",
            "occurred_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "idempotency_key": "jube:observed:" + uuid.uuid4().hex,
            "source_service": "jube",
            "payload": payload,
        }

    def post(self, who: str, tenant: str, event: dict[str, Any]) -> dict[str, Any]:
        self.as_user(who)
        frappe.local.request = Request(
            EnvironBuilder(method="POST", headers={"X-Vati-Tenant-ID": tenant}).get_environ()
        )
        try:
            return api.receive_case_status(event=json.dumps(event))
        finally:
            frappe.set_user("Administrator")

    def delivered_case(self) -> tuple[Any, str]:
        sub = self.send_claim()
        # TAX-121: the send's assurance step is delivered first (not_run here: no RSL fixture).
        self.assertEqual(self.dispatch(), {"delivered": 2})
        return sub, sub.case_id

    # --- outward: submit to risk and case services -------------------------------------

    def test_sending_a_claim_registers_one_case_and_enqueues_one_event(self):
        sub = self.send_claim()
        self.assertTrue(sub.case_id)
        self.assertEqual(sub.review_status, "under_review")
        identity = frappe.db.get_value(
            "Vati Case Identity", sub.case_id, ["tenant_id", "submission_id"], as_dict=True
        )
        self.assertEqual((identity.tenant_id, identity.submission_id), (self.tenant, sub.name))
        events = self.events(sub.case_id)
        self.assertEqual([(e.event_type, e.source_service) for e in events],
                         [("case.registered", "frappe")])  # fmt: skip
        # TAX-121: the assurance step comes first, so it never waits for Jube.
        self.assertEqual([e.event_type for e in self.events(sub.case_id, everything=True)],
                         ["assurance.requested", "case.registered"])  # fmt: skip
        # References only: no amounts, names or explanations travel in the event.
        self.assertEqual(set(json.loads(events[0].payload_json)),
                         {"submission_id", "submission_version"})  # fmt: skip
        self.assertTrue(frappe.db.exists("Vati Case Sync", sub.case_id))

    def test_delivery_evaluates_risk_raises_the_jube_case_and_mirrors_it(self):
        sub, case_id = self.delivered_case()
        self.assertEqual(len(self.jube.invocations), 1)
        facts = self.jube.invocations[0]["facts"]
        self.assertEqual(facts["refund_claim"], "15000.00")
        self.assertEqual(facts["documented_input_vat"], "0.00")
        self.assertEqual(self.jube.creates, 1)
        sync = frappe.get_doc("Vati Case Sync", case_id)
        self.assertEqual((sync.jube_case_id, sync.case_status), ("101", "RECEIVED"))
        self.assertEqual(frappe.db.get_value(SUBMISSION, sub.name, "review_status"),
                         "under_review")  # fmt: skip
        self.assertEqual(self.dispatch(), {})  # nothing left to deliver

    def test_risk_not_archived_yet_retries_without_a_second_invocation(self):
        sub = self.send_claim()
        self.jube.fail_ensure.append(UpstreamError("risk_evaluation_missing", retryable=True))
        now = datetime.now(UTC)
        self.assertEqual(self.dispatch(now), {"delivered": 1, "retry": 1})
        self.assertEqual(self.events(sub.case_id)[0].last_error_code, "risk_evaluation_missing")
        self.assertEqual(self.dispatch(now + timedelta(minutes=5)), {"delivered": 1})
        self.assertEqual(len(self.jube.invocations), 1)  # the completed effect is reused
        self.assertEqual(self.jube.creates, 1)

    def test_a_permanent_upstream_refusal_dead_letters_with_its_code_only(self):
        sub = self.send_claim()
        self.jube.fail_ensure.append(UpstreamError("jube_invoker_unauthorised", retryable=False))
        self.assertEqual(self.dispatch(), {"delivered": 1, "dead_letter": 1})
        self.assertEqual(self.events(sub.case_id)[0].last_error_code, "jube_invoker_unauthorised")

    def test_resending_after_a_correction_reuses_the_case(self):
        sub, case_id = self.delivered_case()
        frappe.db.set_value(SUBMISSION, sub.name, "status", "correction_needed")
        frappe.db.commit()
        self.as_user("taxpayer-a")
        doc = frappe.get_doc(SUBMISSION, sub.name)
        doc.status = "submitted"
        doc.save()
        frappe.set_user("Administrator")
        frappe.db.commit()
        self.assertEqual(frappe.db.get_value(SUBMISSION, sub.name, "case_id"), case_id)
        self.assertEqual(len(self.events(case_id)), 2)
        self.assertEqual(self.dispatch(), {"delivered": 2})  # assurance and Jube for the resend
        self.assertEqual(self.jube.creates, 1)  # same Jube case
        self.assertEqual(len(self.jube.invocations), 2)  # re-evaluated with the new send

    # --- inward: replay, stale, loop -------------------------------------------------

    def test_webhook_replay_is_a_noop_and_a_changed_replay_conflicts(self):
        sub, case_id = self.delivered_case()
        later = datetime.now(UTC) + timedelta(minutes=1)
        event = self.envelope(case_id, "AWAITING_EVIDENCE", later)
        first = self.post("service", self.tenant, event)
        self.assertEqual((first["outcome"], first["replayed"]), ("applied", False))
        self.assertEqual(frappe.db.get_value(SUBMISSION, sub.name, "review_status"),
                         "evidence_requested")  # fmt: skip
        again = self.post("service", self.tenant, event)
        self.assertEqual((again["outcome"], again["replayed"]), ("applied", True))
        self.assertEqual(frappe.db.count("Vati Bridge Inbound Event", {"case_id": case_id}), 1)
        changed = json.loads(json.dumps(event))
        changed["payload"]["status"] = "INVESTIGATING"
        with self.assertRaises(frappe.ValidationError):
            self.post("service", self.tenant, changed)
        with self.assertRaises(IdempotencyConflict):
            store.receive_observation(changed, tenant_id=self.tenant, actor="test")

    def test_stale_update_is_refused_and_nothing_changes(self):
        sub, case_id = self.delivered_case()
        later = datetime.now(UTC) + timedelta(minutes=2)
        self.post("service", self.tenant, self.envelope(case_id, "AWAITING_EVIDENCE", later))
        older = self.envelope(case_id, "RESOLVED", later - timedelta(minutes=1))
        with self.assertRaises(api.BridgeConflict):
            self.post("service", self.tenant, older)
        self.assertEqual(frappe.db.get_value("Vati Case Sync", case_id, "case_status"),
                         "AWAITING_EVIDENCE")  # fmt: skip
        self.assertEqual(frappe.db.get_value(SUBMISSION, sub.name, "review_status"),
                         "evidence_requested")  # fmt: skip

    def test_an_observation_for_another_jube_case_is_refused(self):
        _, case_id = self.delivered_case()
        later = datetime.now(UTC) + timedelta(minutes=1)
        with self.assertRaises(api.BridgeUnprocessable):
            self.post("service", self.tenant,
                      self.envelope(case_id, "INVESTIGATING", later, jube_case_id="999"))  # fmt: skip

    def test_a_mirror_write_never_loops_back_to_jube(self):
        sub, case_id = self.delivered_case()
        before = frappe.db.get_value(
            SUBMISSION, sub.name, ["status", "version", "modified"], as_dict=True
        )
        invocations, creates = len(self.jube.invocations), self.jube.creates
        later = datetime.now(UTC) + timedelta(minutes=1)
        for offset, status in enumerate(["INVESTIGATING", "AWAITING_EVIDENCE", "RESOLVED"]):
            self.post("service", self.tenant,
                      self.envelope(case_id, status, later + timedelta(seconds=offset)))  # fmt: skip
        after = frappe.db.get_value(
            SUBMISSION, sub.name, ["status", "version", "modified", "review_status"], as_dict=True
        )
        self.assertEqual((after.status, after.version, after.modified),
                         (before.status, before.version, before.modified))  # fmt: skip
        self.assertEqual(after.review_status, "completed")
        self.assertEqual(len(self.events(case_id)), 1)  # still only the original send
        self.assertEqual(self.dispatch(), {})
        self.assertEqual((len(self.jube.invocations), self.jube.creates), (invocations, creates))

    def test_poller_mirrors_a_change_and_skips_unchanged_cases(self):
        sub, case_id = self.delivered_case()
        self.assertEqual(store.poll_case_states().get("applied", 0), 0)
        self.jube.cases[case_id].status = "AWAITING_EVIDENCE"
        counts = store.poll_case_states()
        self.assertGreaterEqual(counts.get("applied", 0), 1)
        self.assertEqual(frappe.db.get_value(SUBMISSION, sub.name, "review_status"),
                         "evidence_requested")  # fmt: skip
        self.assertEqual(len(self.events(case_id)), 1)

    # --- who may post, who may read -----------------------------------------------------

    def test_only_the_tenants_service_identity_may_post_case_state(self):
        _, case_id = self.delivered_case()
        event = self.envelope(case_id, "INVESTIGATING", datetime.now(UTC) + timedelta(minutes=1))
        for who in ("taxpayer-a", "auditor"):
            with self.subTest(who=who), self.assertRaises(frappe.PermissionError):
                self.post(who, self.tenant, event)
        with self.assertRaises(TenantScopeDenied):
            self.post("service-other", self.tenant, event)
        foreign = {**event, "event_id": uid()}
        with self.assertRaises(CaseNotFound):
            store.receive_observation(foreign, tenant_id=self.other, actor="test")
        self.assertEqual(frappe.db.count("Vati Bridge Inbound Event", {"case_id": case_id}), 0)

    def test_taxpayer_sees_only_the_review_status(self):
        sub, case_id = self.delivered_case()
        later = datetime.now(UTC) + timedelta(minutes=1)
        self.post("service", self.tenant,
                  self.envelope(case_id, "AWAITING_HUMAN", later, escalated=True))  # fmt: skip
        self.as_user("taxpayer-a")
        view = api.get_review_status(sub.name)["data"]
        self.assertEqual(view, {"review_status": "under_review",
                                "review_status_at": view["review_status_at"]})  # fmt: skip
        own = frappe.get_doc(SUBMISSION, sub.name).as_dict()
        leaked = [k for k in own if any(word in k for word in INTERNAL_WORDS)]
        self.assertEqual(leaked, [])
        for doctype in ("Vati Case Sync", "Vati Bridge Inbound Event"):
            with self.subTest(doctype=doctype):
                with self.assertRaises(frappe.PermissionError):
                    frappe.get_list(doctype, filters={"case_id": case_id})
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc("Vati Case Sync", case_id).check_permission("read")
        self.as_user("taxpayer-b")
        with self.assertRaises(frappe.DoesNotExistError):
            api.get_review_status(sub.name)

    def test_staff_read_the_mirror_only_in_their_own_tenant(self):
        _, case_id = self.delivered_case()
        self.as_user("refund-supervisor")
        self.assertEqual(frappe.get_list("Vati Case Sync", filters={"case_id": case_id},
                                         pluck="name"), [case_id])  # fmt: skip
        # TAX-142: a refund claim's mirror is hidden from staff outside the refund team.
        self.as_user("auditor")
        self.assertEqual(frappe.get_list("Vati Case Sync", filters={"case_id": case_id},
                                         pluck="name"), [])  # fmt: skip
        self.as_user("auditor-other")
        self.assertEqual(frappe.get_list("Vati Case Sync", filters={"case_id": case_id},
                                         pluck="name"), [])  # fmt: skip

    def test_no_client_can_write_the_mirrored_review_status(self):
        self.as_user("taxpayer-a")
        draft = frappe.get_doc(
            {"doctype": SUBMISSION, "tenant_id": self.tenant, "taxpayer_id": self.tp_a,
             "submission_type": "refund_claim", "period": "2026-09",
             "review_status": "completed", "source": "portal"}
        ).insert()  # fmt: skip
        self.assertFalse(draft.review_status)  # cleared on insert
        draft.review_status = "completed"
        draft.contact_email = "accounts@example.test"
        draft.save()
        self.assertFalse(frappe.db.get_value(SUBMISSION, draft.name, "review_status"))
        frappe.set_user("Administrator")
        sub = self.send_claim()
        doc = frappe.get_doc(SUBMISSION, sub.name)
        doc.review_status = "completed"
        doc.save()  # an operator save keeps the mirrored value
        self.assertEqual(frappe.db.get_value(SUBMISSION, sub.name, "review_status"),
                         "under_review")  # fmt: skip

    def test_sync_rule_constants_match_the_doctype_options(self):
        options = frappe.get_meta(SUBMISSION).get_field("review_status").options.split("\n")
        self.assertEqual([o for o in options if o], list(rules.REVIEW_STATUSES))
        statuses = frappe.get_meta("Vati Case Sync").get_field("case_status").options.split("\n")
        self.assertEqual([s for s in statuses if s], list(rules.CASE_STATUSES))
