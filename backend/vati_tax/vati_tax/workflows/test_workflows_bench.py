"""Bench tests for learned-workflow approval, versioning and rollback (TAX-065).

Run on a disposable site (``scripts/frappe_dev.py verify`` does this)::

    bench --site SITE run-tests --module vati_tax.workflows.test_workflows_bench

Plain ``unittest`` without a site skips this module. All data is synthetic.
"""

from __future__ import annotations

import unittest
import uuid

try:
    import frappe
    from frappe.tests import IntegrationTestCase

    from vati_tax.audit import ledger
    from vati_tax.audit import store as audit_store
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.workflows import rules, store
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

AUDIT = audit_store.AUDIT
RUN = uuid.uuid4().hex[:8]
REASON = "Held-out comparison reviewed with the team; sequence matches current practice."


def email(name: str) -> str:
    return f"tax065-{name}-{RUN}@example.test"


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


def proposal() -> dict:
    return {
        "kind": "workflow_proposal",
        "proposal_id": "wfp-" + uuid.uuid4().hex[:16],
        "version": "0.1.0",
        "observer_version": "tax064-observer-v1",
        "status": "proposed",
        "auto_activate": False,
        "approved_by": None,
        "steps": ["request_evidence", "close_no_further_action"],
        "holdout_evaluation": {"status": "evaluated", "holdout_cases": 3, "coverage": "2/3"},
    }


class WorkflowBenchTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.t1 = ensure_tenant(str(uuid.uuid4()), "Synthetic workflow tenant one")
        cls.t2 = ensure_tenant(str(uuid.uuid4()), "Synthetic workflow tenant two")
        cls.users = {}
        grants: list[tuple[str, str, policy.Role]] = [
            ("auditor", cls.t1, policy.AUDITOR),
            ("supervisor", cls.t1, policy.SUPERVISOR),
            ("supervisor-2", cls.t1, policy.SUPERVISOR),
            ("supervisor-t2", cls.t2, policy.SUPERVISOR),
        ]
        for name, tenant, role in grants:
            cls.users[name] = make_user(name)
            grant_membership(cls.users[name], tenant, role, None)
        cls.users["observer"] = ensure_service_identity(email("observer"), [cls.t1])
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        clear_principal_cache()
        self.wf = "wf_" + uuid.uuid4().hex[:10]

    def tearDown(self):
        frappe.set_user("Administrator")

    def as_user(self, name: str) -> None:
        frappe.set_user(self.users[name])
        clear_principal_cache()

    def propose(self, version: str, who: str = "observer") -> dict:
        self.as_user(who)
        return store.propose_workflow(
            tenant_id=self.t1,
            body={"workflow": self.wf, "version": version, "proposal": proposal()},
            idempotency_key=key(),
            correlation_id=None,
        )

    def approve(self, version: str, who: str = "supervisor") -> dict:
        self.as_user(who)
        state = store.get_workflow(tenant_id=self.t1, workflow=self.wf)
        current = next(v for v in state["versions"] if v["version"] == version)
        return store.decide_workflow(
            tenant_id=self.t1,
            workflow=self.wf,
            version=version,
            expected_version=current["record_version"],
            approve=True,
            reason=REASON,
            holdout_reviewed=True,
            correlation_id=None,
        )

    def activate(self, version: str, who: str = "supervisor") -> dict:
        self.as_user(who)
        seq = store.get_workflow(tenant_id=self.t1, workflow=self.wf)["activation_seq"]
        return store.activate_workflow(
            tenant_id=self.t1,
            workflow=self.wf,
            version=version,
            expected_seq=seq,
            reason=REASON,
            correlation_id=None,
        )

    def check(self, version: str) -> dict:
        self.as_user("observer")
        return store.check_execution(tenant_id=self.t1, workflow=self.wf, version=version)

    # --- acceptance -------------------------------------------------------------

    def test_self_approval_is_refused_and_the_refusal_is_kept(self):
        self.propose("1.0.0", who="supervisor")
        with self.assertRaises(frappe.PermissionError):
            self.approve("1.0.0", who="supervisor")
        frappe.set_user("Administrator")
        self.assertTrue(
            frappe.db.exists(
                AUDIT, {"actor": self.users["supervisor"], "action": "playbook.approve.denied"}
            )
        )
        self.assertEqual(self.approve("1.0.0", who="supervisor-2")["status"], "approved")

    def test_unapproved_version_cannot_activate_or_execute(self):
        self.propose("1.0.0")
        with self.assertRaises(rules.WorkflowConflict):
            self.activate("1.0.0")
        with self.assertRaises(rules.ExecutionRefused) as refused:
            self.check("1.0.0")
        self.assertEqual(refused.exception.field_errors[0]["code"], "unapproved_version")
        frappe.set_user("Administrator")
        self.assertTrue(
            frappe.db.exists(
                AUDIT, {"action": "workflow.execute.denied", "actor": self.users["observer"]}
            )
        )

    def test_rollback_restores_the_previous_approved_version(self):
        for v in ("1.0.0", "1.1.0"):
            self.propose(v)
            self.approve(v)
        self.activate("1.0.0")
        self.activate("1.1.0", who="supervisor-2")
        self.assertTrue(self.check("1.1.0")["executable"])
        self.as_user("supervisor")
        back = store.rollback_workflow(
            tenant_id=self.t1,
            workflow=self.wf,
            expected_seq=2,
            reason=REASON,
            correlation_id=None,
        )
        self.assertEqual((back["version"], back["replaced_version"]), ("1.0.0", "1.1.0"))
        self.assertTrue(self.check("1.0.0")["executable"])
        with self.assertRaises(rules.ExecutionRefused):
            self.check("1.1.0")

    def test_service_cannot_approve_or_activate(self):
        self.propose("1.0.0", who="auditor")
        with self.assertRaises(frappe.PermissionError):
            self.approve("1.0.0", who="observer")
        self.approve("1.0.0")
        with self.assertRaises(frappe.PermissionError):
            self.activate("1.0.0", who="observer")

    # --- record protection --------------------------------------------------------

    def test_versions_are_frozen_and_history_is_append_only(self):
        self.propose("1.0.0")
        self.approve("1.0.0")
        self.activate("1.0.0")
        frappe.set_user("Administrator")
        version = frappe.get_all(store.VERSION, filters={"workflow": self.wf}, pluck="name")[0]
        doc = frappe.get_doc(store.VERSION, version)
        doc.steps = '["request_evidence", "continue_investigation"]'
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
        entry = frappe.get_all(store.ACTIVATION, filters={"workflow": self.wf}, pluck="name")[0]
        act = frappe.get_doc(store.ACTIVATION, entry)
        act.reason = "Rewritten reason that should never be accepted by the controller."
        with self.assertRaises(frappe.ValidationError):
            act.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(store.ACTIVATION, entry, ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(store.VERSION, version, ignore_permissions=True)

    def test_other_tenant_sees_nothing_and_ledger_stays_intact(self):
        self.propose("1.0.0")
        self.approve("1.0.0")
        self.as_user("supervisor-t2")
        with self.assertRaises(rules.WorkflowNotFound):
            store.get_workflow(tenant_id=self.t2, workflow=self.wf)
        frappe.set_user("Administrator")
        entries = audit_store.FrappeLedger().entries(self.t1)
        self.assertTrue(ledger.verify_chain(entries)["intact"])
