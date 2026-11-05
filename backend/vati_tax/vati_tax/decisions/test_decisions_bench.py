"""Bench tests for decisions, approvals and evidence requests (TAX-046).

Run on a disposable site: ``python3 scripts/frappe_dev.py verify``. Jube is
replaced with the in-process ``FakeLifecycle`` from test_engine.py through
``store.set_lifecycle_factory``; tests/integration/test_decision_execution.py
covers the real adapter.
"""

from __future__ import annotations

import unittest
import uuid

try:
    import frappe
    from frappe.tests import IntegrationTestCase

    from vati_tax.decisions import rules, store
    from vati_tax.decisions.test_engine import FakeCase, FakeLifecycle, UpstreamError
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.ids import mapping
    from vati_tax.ids.canonical import CaseNotFound, IdempotencyConflict
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

RUN = uuid.uuid4().hex[:8]


def email(name: str) -> str:
    return f"tax046-{name}-{RUN}@example.test"


def make_user(name: str) -> str:
    user = email(name)
    frappe.get_doc(
        {
            "doctype": "User",
            "email": user,
            "first_name": f"Synthetic {name}",
            "send_welcome_email": 0,
        }
    ).insert(ignore_permissions=True)
    return user


def key() -> str:
    return "synthetic-" + uuid.uuid4().hex


class DecisionBenchTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.t1 = ensure_tenant(str(uuid.uuid4()), "Synthetic decision tenant one")
        cls.t2 = ensure_tenant(str(uuid.uuid4()), "Synthetic decision tenant two")
        cls.taxpayer = str(uuid.uuid4())
        cls.users = {}
        grants: list[tuple[str, str, policy.Role, str | None]] = [
            ("taxpayer", cls.t1, policy.TAXPAYER, cls.taxpayer),
            ("auditor", cls.t1, policy.AUDITOR, None),
            ("supervisor", cls.t1, policy.SUPERVISOR, None),
            ("supervisor-2", cls.t1, policy.SUPERVISOR, None),
            ("auditor-t2", cls.t2, policy.AUDITOR, None),
        ]
        for name, tenant, role, taxpayer in grants:
            cls.users[name] = make_user(name)
            grant_membership(cls.users[name], tenant, role, taxpayer)
        cls.users["service"] = ensure_service_identity(email("service"), [cls.t1])
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        store.set_lifecycle_factory(None)
        frappe.db.rollback()
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        clear_principal_cache()
        self.jube = FakeLifecycle()
        store.set_lifecycle_factory(lambda _tenant: self.jube)
        self.case_id = mapping.register_case(
            tenant_id=self.t1, idempotency_key=key(), taxpayer_id=self.taxpayer
        )["case_id"]
        self.jube.cases[self.case_id] = FakeCase("AWAITING_HUMAN")

    def tearDown(self):
        frappe.set_user("Administrator")
        store.set_lifecycle_factory(None)

    def as_user(self, name: str) -> str:
        frappe.set_user(self.users[name])
        return self.users[name]

    def version(self) -> int:
        return store.FrappeRepository().case_version(self.t1, self.case_id)

    def propose(self, who: str = "auditor", **overrides):
        body = {
            "outcome": "continue_investigation",
            "rationale": "Import declarations need comparing with the invoices first.",
            "rating": 2,
            "expected_case_status": "AWAITING_HUMAN",
            "expected_version": self.version(),
        }
        body.update(overrides)
        self.as_user(who)
        return store.propose_decision(
            tenant_id=self.t1, case_id=self.case_id, body=body, idempotency_key=key()
        )

    def approve(
        self,
        decision_id: str,
        who: str = "supervisor",
        idem: str | None = None,
        expected: int | None = None,
    ):
        if expected is None:
            expected = self.version()
        self.as_user(who)
        return store.review_decision(
            tenant_id=self.t1,
            decision_id=decision_id,
            action="approve",
            body={"expected_version": expected},
            idempotency_key=idem or key(),
        )

    def test_approved_decision_is_persisted_audited_and_executed_once(self):
        proposed = self.propose()
        idem = key()
        expected = self.version()
        approved = self.approve(proposed["decision_id"], idem=idem, expected=expected)
        self.assertEqual(
            (approved["status"], approved["jube_sync"]["state"]), ("approved", "synced")
        )
        # A retry after a lost response resends the identical request, including
        # the case version the officer saw, even though approval has moved it on.
        again = self.approve(proposed["decision_id"], idem=idem, expected=expected)
        self.assertTrue(again["replayed"])
        self.assertEqual(len(self.jube.calls), 1)
        frappe.set_user("Administrator")
        doc = frappe.get_doc(store.DECISION, proposed["decision_id"])
        self.assertEqual(
            (doc.proposed_by, doc.reviewed_by), (self.users["auditor"], self.users["supervisor"])
        )
        self.assertIsNone(doc.open_key)
        actions = frappe.get_all(
            store.AUDIT, filters={"case_id": self.case_id}, order_by="creation asc", pluck="action"
        )
        self.assertEqual(actions, ["decision.proposed", "decision.approved", "decision.executed"])

    def test_service_and_proposer_cannot_decide(self):
        with self.assertRaises(frappe.PermissionError):
            self.propose(who="service")
        proposed = self.propose(who="supervisor")
        with self.assertRaises(frappe.PermissionError):
            self.approve(proposed["decision_id"], who="supervisor")
        with self.assertRaises(frappe.PermissionError):
            self.approve(proposed["decision_id"], who="auditor")
        self.assertEqual(self.jube.calls, [])

    def test_agent_draft_cannot_be_approved(self):
        self.as_user("service")
        draft = store.record_draft(
            tenant_id=self.t1,
            case_id=self.case_id,
            body={
                "outcome": "continue_investigation",
                "rationale": "Agent summary: invoices and import records disagree.",
            },
            idempotency_key=key(),
        )
        with self.assertRaises(rules.DecisionRefused):
            self.approve(draft["decision_id"])

    def test_stale_approval_after_jube_moved(self):
        proposed = self.propose()
        self.jube.cases[self.case_id].status = "INVESTIGATING"
        with self.assertRaises(rules.VersionConflict):
            self.approve(proposed["decision_id"])
        frappe.set_user("Administrator")
        self.assertEqual(
            frappe.db.get_value(store.DECISION, proposed["decision_id"], "status"), "proposed"
        )

    def test_failed_jube_call_is_recorded_not_applied(self):
        proposed = self.propose()
        self.jube.fail.append(UpstreamError("upstream_timeout", True))
        approved = self.approve(proposed["decision_id"])
        self.assertEqual(approved["jube_sync"]["state"], "failed")
        self.as_user("service")
        retried = store.retry_decision(tenant_id=self.t1, decision_id=proposed["decision_id"])
        self.assertEqual(retried["jube_sync"]["state"], "synced")

    def test_concurrent_proposals_cannot_share_a_case_version(self):
        self.propose()
        with self.assertRaises(rules.VersionConflict):
            self.propose(who="supervisor", expected_version=1)

    def test_tenant_and_taxpayer_isolation(self):
        proposed = self.propose()
        with self.assertRaises(CaseNotFound):
            self.as_user("auditor-t2")
            store.review_decision(
                tenant_id=self.t2,
                decision_id=proposed["decision_id"],
                action="approve",
                body={"expected_version": 2},
                idempotency_key=key(),
            )
        for name in ("auditor-t2", "taxpayer"):
            self.as_user(name)
            self.assertFalse(
                frappe.has_permission(store.DECISION, "read", doc=proposed["decision_id"]), name
            )
            if name == "taxpayer":
                # Taxpayers hold no Vati Decision role permission at all (ADR-013),
                # so listing is refused rather than filtered to an empty page.
                with self.assertRaises(frappe.PermissionError):
                    frappe.get_list(store.DECISION, pluck="name")
            else:
                # An officer of another tenant may list decisions, but the tenant
                # filter leaves nothing from this tenant.
                self.assertEqual(frappe.get_list(store.DECISION, pluck="name"), [], name)
            # TAX-059 (ADR-015): only supervisors may read the audit ledger, so a
            # direct list is refused outright for both.
            self.assertFalse(frappe.has_permission(store.AUDIT, "read"), name)
            with self.assertRaises(frappe.PermissionError):
                frappe.get_list(store.AUDIT, pluck="name")

    def test_records_cannot_be_edited_or_deleted(self):
        proposed = self.propose()
        frappe.set_user("Administrator")
        doc = frappe.get_doc(store.DECISION, proposed["decision_id"])
        doc.rationale = "Changed after the fact by someone else entirely."
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(store.DECISION, proposed["decision_id"], ignore_permissions=True)
        event = frappe.get_all(store.AUDIT, filters={"case_id": self.case_id}, pluck="name")[0]
        event_doc = frappe.get_doc(store.AUDIT, event)
        event_doc.outcome = "tampered"
        with self.assertRaises(frappe.ValidationError):
            event_doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(store.AUDIT, event, ignore_permissions=True)

    def test_evidence_request_is_recorded_once(self):
        idem = key()
        body = {
            "expected_version": self.version(),
            "finding_ids": [str(uuid.uuid4())],
            "items": [{"document_type": "invoice", "description": "Supplier invoices."}],
            "due_date": frappe.utils.add_days(frappe.utils.nowdate(), 14),
            "message_to_taxpayer": "Please upload the supplier invoices.",
        }
        self.as_user("auditor")
        first = store.authorise_evidence_request(
            tenant_id=self.t1, case_id=self.case_id, body=body, idempotency_key=idem
        )
        again = store.authorise_evidence_request(
            tenant_id=self.t1, case_id=self.case_id, body=body, idempotency_key=idem
        )
        self.assertEqual(first["request_id"], again["request_id"])
        with self.assertRaises(IdempotencyConflict):
            store.authorise_evidence_request(
                tenant_id=self.t1,
                case_id=self.case_id,
                body={**body, "message_to_taxpayer": "Different text."},
                idempotency_key=idem,
            )
        self.as_user("service")
        with self.assertRaises(frappe.PermissionError):
            store.authorise_evidence_request(
                tenant_id=self.t1,
                case_id=self.case_id,
                body={**body, "expected_version": self.version()},
                idempotency_key=key(),
            )
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.count(store.REQUEST, {"case_id": self.case_id}), 1)

    def test_missing_lifecycle_configuration_records_nothing(self):
        store.set_lifecycle_factory(None)
        with self.assertRaises(rules.UpstreamUnavailable):
            self.propose()
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.count(store.DECISION, {"case_id": self.case_id}), 0)
