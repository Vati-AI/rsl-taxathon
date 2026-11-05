"""Bench tests for objections, appeals and reviewed outcomes (TAX-069).

Run on a disposable site (``scripts/frappe_dev.py verify`` does this)::

    bench --site SITE run-tests --module vati_tax.challenges.test_challenges_bench

Plain ``unittest`` without a site skips this module. Decisions are made with
the TAX-046 store and the in-process ``FakeLifecycle`` (as in its bench tests).
Every user, tenant and taxpayer is synthetic.
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
    from vati_tax.challenges import rules, store
    from vati_tax.decisions import store as decisions_store
    from vati_tax.decisions.test_engine import FakeCase, FakeLifecycle
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_tenant, grant_membership
    from vati_tax.ids import mapping
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

CHALLENGE = store.CHALLENGE
DECISION = "Vati Decision"
AUDIT = audit_store.AUDIT
RUN = uuid.uuid4().hex[:8]
GROUNDS = "The supplier invoices for March were omitted from the reconciliation."
REASON = "The March invoices change the reconciled input VAT for the period."


def email(name: str) -> str:
    return f"tax069-{name}-{RUN}@example.test"


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


class ChallengeBenchTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.t1 = ensure_tenant(str(uuid.uuid4()), "Synthetic challenge tenant one")
        cls.taxpayer = str(uuid.uuid4())
        cls.other_taxpayer = str(uuid.uuid4())
        cls.users = {}
        grants: list[tuple[str, str, policy.Role, str | None]] = [
            ("taxpayer", cls.t1, policy.TAXPAYER, cls.taxpayer),
            ("other-taxpayer", cls.t1, policy.TAXPAYER, cls.other_taxpayer),
            ("auditor", cls.t1, policy.AUDITOR, None),
            ("supervisor", cls.t1, policy.SUPERVISOR, None),
            ("supervisor-2", cls.t1, policy.SUPERVISOR, None),
            ("supervisor-3", cls.t1, policy.SUPERVISOR, None),
        ]
        for name, tenant, role, taxpayer in grants:
            cls.users[name] = make_user(name)
            grant_membership(cls.users[name], tenant, role, taxpayer)
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        decisions_store.set_lifecycle_factory(None)
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        clear_principal_cache()
        self.jube = FakeLifecycle()
        decisions_store.set_lifecycle_factory(lambda _tenant: self.jube)
        self.case_id = mapping.register_case(
            tenant_id=self.t1, idempotency_key=key(), taxpayer_id=self.taxpayer
        )["case_id"]
        self.jube.cases[self.case_id] = FakeCase("AWAITING_HUMAN")
        self.decision_id = self._approved_decision()

    def tearDown(self):
        frappe.set_user("Administrator")
        decisions_store.set_lifecycle_factory(None)

    def as_user(self, name: str) -> str:
        frappe.set_user(self.users[name])
        clear_principal_cache()
        return self.users[name]

    def _version(self) -> int:
        return decisions_store.FrappeRepository().case_version(self.t1, self.case_id)

    def _approved_decision(self) -> str:
        self.as_user("auditor")
        proposed = decisions_store.propose_decision(
            tenant_id=self.t1,
            case_id=self.case_id,
            body={
                "outcome": "continue_investigation",
                "rationale": "Import declarations need comparing with the invoices first.",
                "rating": 2,
                "expected_case_status": "AWAITING_HUMAN",
                "expected_version": self._version(),
            },
            idempotency_key=key(),
        )
        self.as_user("supervisor")
        decisions_store.review_decision(
            tenant_id=self.t1,
            decision_id=proposed["decision_id"],
            action="approve",
            body={"expected_version": self._version()},
            idempotency_key=key(),
        )
        frappe.set_user("Administrator")
        return str(proposed["decision_id"])

    def lodge(self, who="taxpayer", **changes):
        self.as_user(who)
        body = {"stage": "objection", "decision_id": self.decision_id, "grounds": GROUNDS}
        body.update(changes)
        return store.lodge(tenant_id=self.t1, body=body, idempotency_key=key(), correlation_id=None)

    def review(self, challenge, who="supervisor-2", outcome="overturned"):
        self.as_user(who)
        return store.review(
            tenant_id=self.t1,
            challenge_id=challenge["challenge_id"],
            expected_version=challenge["version"],
            outcome=outcome,
            reason=REASON,
            correlation_id=None,
        )

    def test_objection_appeal_and_final_outcome_are_linked_and_persisted(self):
        objection = self.lodge()
        self.review(objection, outcome="partially_overturned")
        appeal = self.lodge(
            stage="appeal",
            decision_id=None,
            parent_challenge_id=objection["challenge_id"],
            grounds="The objection review did not consider the credit notes.",
        )
        self.review(appeal, who="supervisor-3", outcome="overturned")
        self.as_user("auditor")
        view = store.history(tenant_id=self.t1, decision_id=self.decision_id)
        self.assertEqual([c["stage"] for c in view["challenges"]], ["objection", "appeal"])
        self.assertEqual(view["final_outcome"]["outcome"], "overturned")
        self.assertTrue(view["original_decision_unchanged"])
        frappe.set_user("Administrator")
        row = frappe.get_doc(CHALLENGE, appeal["challenge_id"])
        self.assertEqual(
            (row.status, row.version, row.parent_challenge_id),
            ("reviewed", 2, objection["challenge_id"]),
        )

    def test_overturning_leaves_the_decision_row_and_its_ledger_entries_unchanged(self):
        frappe.set_user("Administrator")
        before = frappe.get_doc(DECISION, self.decision_id).as_dict()
        entries_before = audit_store.FrappeLedger().entries(self.t1)
        self.review(self.lodge())
        frappe.set_user("Administrator")
        after = frappe.get_doc(DECISION, self.decision_id).as_dict()
        for field in rules.DECIDED_FIELDS:
            name = "name" if field == "decision_id" else field
            self.assertEqual(before.get(name), after.get(name), field)
        self.assertEqual(before["modified"], after["modified"])
        entries_after = audit_store.FrappeLedger().entries(self.t1)
        self.assertEqual(entries_after[: len(entries_before)], entries_before)
        self.assertTrue(ledger.verify_chain(entries_after)["intact"])
        self.assertEqual(len(self.jube.calls), 1)  # only the original approval

    def test_overturn_signal_is_review_only_and_hidden_from_the_decider(self):
        self.review(self.lodge())
        frappe.set_user("Administrator")
        entries = audit_store.FrappeLedger().entries(self.t1)
        now = datetime.datetime.now(datetime.UTC)
        seen = ledger.integrity_signals(entries, now=now, viewer=self.users["supervisor-3"])
        kinds = {s["kind"]: s for s in seen["signals"]}
        self.assertEqual(kinds["decision_overturned"]["status"], "review_only")
        self.assertEqual(
            kinds["decision_overturned"]["subject_actor"], self.users["auditor"].lower()
        )
        as_decider = ledger.integrity_signals(entries, now=now, viewer=self.users["auditor"])
        self.assertNotIn("decision_overturned", [s["kind"] for s in as_decider["signals"]])

    def test_deciders_cannot_review_and_refusals_are_kept(self):
        objection = self.lodge()
        for who in ("supervisor", "auditor"):  # approver, proposer
            with self.subTest(who=who), self.assertRaises(frappe.PermissionError):
                self.review(objection, who=who)
        frappe.set_user("Administrator")
        self.assertEqual(
            frappe.db.get_value(CHALLENGE, objection["challenge_id"], "status"), "lodged"
        )
        self.assertEqual(
            frappe.db.count(
                AUDIT,
                {"subject_id": objection["challenge_id"], "action": "challenge.review.denied"},
            ),
            2,
        )

    def test_another_taxpayer_cannot_object(self):
        with self.assertRaises(rules.ChallengeNotFound):
            self.lodge("other-taxpayer")

    def test_challenge_rows_are_frozen_and_kept(self):
        objection = self.lodge()
        frappe.set_user("Administrator")
        doc = frappe.get_doc(CHALLENGE, objection["challenge_id"])
        doc.grounds = "Rewritten grounds that were never lodged by anyone."
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(CHALLENGE, objection["challenge_id"], ignore_permissions=True)
