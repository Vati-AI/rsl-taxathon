"""Bench tests for the audit ledger (TAX-059).

Run on a disposable site: ``python3 scripts/frappe_dev.py verify``. Jube is
replaced with the in-process ``FakeLifecycle`` (as in the TAX-046 bench tests).
"""

from __future__ import annotations

import datetime
import unittest
import uuid

try:
    import frappe
    from frappe.tests import IntegrationTestCase

    from vati_tax.audit import ledger
    from vati_tax.audit import store as audit_store
    from vati_tax.decisions import store
    from vati_tax.decisions.test_engine import FakeCase, FakeLifecycle
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import TenantScopeDenied, clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.ids import mapping
    from vati_tax.patches.v0_3 import seal_audit_ledger
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

RUN = uuid.uuid4().hex[:8]
AUDIT = audit_store.AUDIT
REASON = "Assigned officer is on leave; reassigned so the evidence deadline is kept."


def email(name: str) -> str:
    return f"tax059-{name}-{RUN}@example.test"


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


class LedgerBenchTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.t1 = ensure_tenant(str(uuid.uuid4()), "Synthetic ledger tenant one")
        cls.t2 = ensure_tenant(str(uuid.uuid4()), "Synthetic ledger tenant two")
        cls.taxpayer = str(uuid.uuid4())
        cls.users = {}
        grants: list[tuple[str, str, policy.Role, str | None]] = [
            ("taxpayer", cls.t1, policy.TAXPAYER, cls.taxpayer),
            ("auditor", cls.t1, policy.AUDITOR, None),
            ("supervisor", cls.t1, policy.SUPERVISOR, None),
            ("supervisor-2", cls.t1, policy.SUPERVISOR, None),
            ("supervisor-t2", cls.t2, policy.SUPERVISOR, None),
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

    def propose(self, who: str = "auditor") -> dict:
        version = store.FrappeRepository().case_version(self.t1, self.case_id)
        self.as_user(who)
        return store.propose_decision(
            tenant_id=self.t1,
            case_id=self.case_id,
            body={
                "outcome": "continue_investigation",
                "rationale": "Import declarations need comparing with the invoices first.",
                "rating": 2,
                "expected_case_status": "AWAITING_HUMAN",
                "expected_version": version,
            },
            idempotency_key=key(),
        )

    def approve(self, decision_id: str, who: str = "supervisor") -> dict:
        version = store.FrappeRepository().case_version(self.t1, self.case_id)
        self.as_user(who)
        return store.review_decision(
            tenant_id=self.t1,
            decision_id=decision_id,
            action="approve",
            body={"expected_version": version},
            idempotency_key=key(),
        )

    def override(self, who: str = "supervisor", **changes) -> dict:
        body = {
            "control": "case_assignment",
            "subject_type": "case",
            "subject_id": self.case_id,
            "case_id": self.case_id,
            "reason": REASON,
            "target_actor": self.users["auditor"],
        }
        body.update(changes)
        self.as_user(who)
        return audit_store.record_override(tenant_id=self.t1, body=body, idempotency_key=key())

    def entries(self, **filters) -> list:
        frappe.set_user("Administrator")
        return frappe.get_all(
            AUDIT,
            filters={"tenant_id": self.t1, **filters},
            fields=["name", "action", "category", "actor", "chain_seq"],
            order_by="chain_seq asc",
        )

    # --- recording ------------------------------------------------------------------

    def test_decision_events_are_sealed_with_reviewer_identity(self):
        proposed = self.propose()
        self.approve(proposed["decision_id"])
        rows = self.entries(case_id=self.case_id, category=["in", ["write", "approval"]])
        self.assertEqual(
            [(r.action, r.category) for r in rows],
            [
                ("decision.proposed", "write"),
                ("decision.approved", "approval"),
                ("decision.executed", "write"),
            ],
        )
        self.assertEqual(rows[1].actor, self.users["supervisor"])
        self.as_user("supervisor-2")
        self.assertTrue(audit_store.verify_ledger(tenant_id=self.t1)["intact"])

    def test_case_and_ledger_reads_are_recorded(self):
        self.as_user("auditor")
        store.case_review_state(tenant_id=self.t1, case_id=self.case_id)
        self.as_user("supervisor")
        audit_store.ledger_entries(tenant_id=self.t1)
        reads = self.entries(category="read")
        self.assertEqual(
            [(r.action, r.actor) for r in reads[-2:]],
            [("case.read", self.users["auditor"]), ("ledger.read", self.users["supervisor"])],
        )

    # --- unauthorised override ------------------------------------------------------

    def test_unauthorised_override_is_refused_and_the_attempt_is_kept(self):
        for who in ("auditor", "service", "taxpayer"):
            with self.assertRaises(frappe.PermissionError):
                self.override(who)
        denied = self.entries(category="denied", action="override.record.denied")
        self.assertEqual(
            sorted(r.actor for r in denied[-3:]),
            sorted(self.users[n] for n in ("auditor", "service", "taxpayer")),
        )
        self.assertEqual(self.entries(category="override"), [])

    def test_supervisor_override_on_own_work_is_refused(self):
        with self.assertRaises(frappe.PermissionError):
            self.override(target_actor=self.users["supervisor"])
        recorded = self.override()
        self.assertEqual(recorded["category"], "override")
        self.assertEqual(recorded["reason"], REASON)

    def test_self_approval_refusal_is_kept(self):
        proposed = self.propose(who="supervisor")
        with self.assertRaises(frappe.PermissionError):
            self.approve(proposed["decision_id"], who="supervisor")
        denied = self.entries(action="decision.approve.denied")
        self.assertEqual(denied[-1].actor, self.users["supervisor"])
        frappe.set_user("Administrator")
        self.assertEqual(
            frappe.db.get_value(store.DECISION, proposed["decision_id"], "status"), "proposed"
        )

    # --- immutable history ----------------------------------------------------------

    def test_entries_cannot_be_edited_deleted_or_forged(self):
        self.override()
        name = self.entries(category="override")[-1].name
        frappe.set_user("Administrator")
        doc = frappe.get_doc(AUDIT, name)
        doc.reason = "Edited after the fact to look routine."
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(AUDIT, name, ignore_permissions=True)
        head = audit_store.FrappeLedger().head(self.t1)
        assert head is not None
        forged = frappe.get_doc(
            {
                "doctype": AUDIT,
                "event_id": str(uuid.uuid4()),
                "tenant_id": self.t1,
                "case_id": self.case_id,
                "action": "override.case_assignment",
                "outcome": "recorded",
                "category": "override",
                "actor": self.users["supervisor-2"],
                "actor_role": "supervisor",
                "occurred_at": frappe.utils.now_datetime(),
                "correlation_id": str(uuid.uuid4()),
                "subject_type": "case",
                "subject_id": self.case_id,
                "payload_hash": "0" * 64,
                "chain_seq": head.chain_seq + 1,
                "prev_hash": head.entry_hash,
                "entry_hash": "f" * 64,
            }
        )
        with self.assertRaises(frappe.ValidationError):
            forged.insert(ignore_permissions=True)

    def test_a_direct_database_edit_is_detected(self):
        # A tenant of its own: the recorded reads commit, and the shared
        # tenant must stay intact for the other tests.
        tenant = ensure_tenant(str(uuid.uuid4()), "Synthetic tamper tenant")
        grant_membership(self.users["supervisor-2"], tenant, policy.SUPERVISOR)
        clear_principal_cache()
        names = self.legacy_entries(tenant, 2)
        frappe.db.set_value(AUDIT, names[0], "outcome", "rewritten", update_modified=False)
        self.as_user("supervisor-2")
        report = audit_store.verify_ledger(tenant_id=tenant)
        self.assertFalse(report["intact"])
        self.assertIn("hash_mismatch", {p["code"] for p in report["problems"]})
        signals = audit_store.review_signals(tenant_id=tenant, window_days=365)
        self.assertIn("ledger_integrity", {s["kind"] for s in signals["signals"]})

    def legacy_entries(self, tenant: str, n: int) -> list[str]:
        """Sealed decision events written straight to the store, oldest first."""
        names = []
        head = audit_store.FrappeLedger().head(tenant)
        now = datetime.datetime.now(datetime.UTC)
        for i in range(n):
            entry = ledger.seal(
                ledger.Unsealed(
                    event_id=str(uuid.uuid4()),
                    tenant_id=tenant,
                    action="decision.proposed",
                    outcome="proposed",
                    actor="legacy@example.test",
                    actor_role="auditor",
                    occurred_at=now - datetime.timedelta(minutes=n - i),
                    correlation_id=str(uuid.uuid4()),
                    subject_type="decision",
                    subject_id=str(uuid.uuid4()),
                    payload_hash="a" * 64,
                    case_id=str(uuid.uuid4()),
                ),
                head,
            )
            audit_store.FrappeLedger().insert(entry)
            names.append(entry.event_id)
            head = entry
        return names

    # --- restricted supervisor access -----------------------------------------------

    def test_only_supervisors_of_the_tenant_read_the_ledger(self):
        for who in ("auditor", "service", "taxpayer"):
            self.as_user(who)
            with self.assertRaises(frappe.PermissionError):
                audit_store.ledger_entries(tenant_id=self.t1)
            self.assertFalse(frappe.has_permission(AUDIT, "read"), who)
        self.as_user("supervisor-t2")
        with self.assertRaises(TenantScopeDenied):
            audit_store.ledger_entries(tenant_id=self.t1)
        own = audit_store.ledger_entries(tenant_id=self.t2)
        self.assertTrue(all(e["action"] == "ledger.read" for e in own["entries"]))
        self.assertEqual(frappe.get_list(AUDIT, filters={"tenant_id": self.t1}, pluck="name"), [])

    def test_summary_for_the_management_view(self):
        self.override()
        self.as_user("supervisor-2")
        now = datetime.datetime.now(datetime.UTC)
        result = audit_store.ledger_summary(
            tenant_id=self.t1, since=now - datetime.timedelta(hours=1), until=now
        )
        self.assertGreaterEqual(result["by_category"]["override"], 1)
        self.assertTrue(result["ledger_intact"])

    # --- migration ------------------------------------------------------------------

    def test_patch_seals_events_recorded_before_the_ledger(self):
        frappe.set_user("Administrator")
        tenant = ensure_tenant(str(uuid.uuid4()), "Synthetic legacy tenant")
        names = self.legacy_entries(tenant, 3)
        for name in names:
            frappe.db.set_value(
                AUDIT,
                name,
                {"chain_seq": 0, "prev_hash": None, "entry_hash": None, "chain_key": None},
                update_modified=False,
            )
        seal_audit_ledger.execute()
        rows = audit_store.FrappeLedger().entries(tenant)
        self.assertEqual([r.chain_seq for r in rows], [1, 2, 3])
        self.assertTrue(ledger.verify_chain(rows)["intact"])
