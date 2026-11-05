"""Bench tests for correction reminders (TAX-078).

Run on a disposable site (``scripts/frappe_dev.py verify`` does this)::

    bench --site SITE run-tests --module vati_tax.reminders.test_reminders_bench

Plain ``unittest`` without a site skips this module. Decisions are made with
the TAX-046 store and the in-process ``FakeLifecycle`` (as in its bench tests);
challenges with the TAX-069 store. Every user, tenant and taxpayer is synthetic.
"""

from __future__ import annotations

import io
import json
import unittest
import uuid
from typing import Any

try:
    import frappe
    from frappe.tests import IntegrationTestCase
    from werkzeug.datastructures import FileStorage
    from werkzeug.test import EnvironBuilder
    from werkzeug.wrappers import Request

    from vati_tax.audit import store as audit_store
    from vati_tax.challenges import store as challenge_store
    from vati_tax.decisions import store as decisions_store
    from vati_tax.decisions.test_engine import FakeCase, FakeLifecycle
    from vati_tax.evidence import api as evidence_api
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_tenant, grant_membership
    from vati_tax.ids import mapping
    from vati_tax.portal import api as portal_api
    from vati_tax.portal.test_refund_claim_bench import pdf
    from vati_tax.reminders import rules, store
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

REMINDER = store.REMINDER
AUDIT = audit_store.AUDIT
RUN = uuid.uuid4().hex[:8]
EXPLANATION = (
    "Your March claim listed fewer purchase invoices than the VAT it claimed. "
    "List every purchase invoice for the period."
)
REASON = "The earlier wording named the wrong month for the missing invoices."


def email(name: str) -> str:
    return f"tax078-{name}-{RUN}@example.test"


def make_user(name: str) -> str:
    user = email(name)
    frappe.get_doc(
        {
            "doctype": "User",
            "email": user,
            "first_name": f"Synthetic {name}",
            "send_welcome_email": 0,
        }  # fmt: skip
    ).insert(ignore_permissions=True)
    return user


def key() -> str:
    return "synthetic-" + uuid.uuid4().hex


def taxpayer(tenant: str, name: str, tin: str) -> str:
    return (
        frappe.get_doc(
            {
                "doctype": "Vati Taxpayer",
                "tenant_id": tenant,
                "display_name": name,
                "taxpayer_type": "company",
                "tin": tin,
            }  # fmt: skip
        )
        .insert()
        .name
    )


class ReminderBenchTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.t1 = ensure_tenant(str(uuid.uuid4()), "Synthetic reminder tenant one")
        cls.t2 = ensure_tenant(str(uuid.uuid4()), "Synthetic reminder tenant two")
        cls.tp_a = taxpayer(cls.t1, "Synthetic Reminder Wholesale", "430078001")
        cls.tp_b = taxpayer(cls.t1, "Synthetic Reminder Retail", "430078002")
        cls.tp_c = taxpayer(cls.t2, "Synthetic Reminder Imports", "430078001")
        cls.users = {}
        grants: list[tuple[str, str, policy.Role, str | None]] = [
            ("taxpayer-a", cls.t1, policy.TAXPAYER, cls.tp_a),
            ("taxpayer-b", cls.t1, policy.TAXPAYER, cls.tp_b),
            ("taxpayer-c", cls.t2, policy.TAXPAYER, cls.tp_c),
            ("auditor", cls.t1, policy.AUDITOR, None),
            ("specialist", cls.t1, policy.SPECIALIST, None),
            ("supervisor", cls.t1, policy.SUPERVISOR, None),
            ("supervisor-2", cls.t1, policy.SUPERVISOR, None),
            ("auditor-t2", cls.t2, policy.AUDITOR, None),
        ]
        for name, tenant, role, tp in grants:
            cls.users[name] = make_user(name)
            grant_membership(cls.users[name], tenant, role, tp)
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.local.request = None
        decisions_store.set_lifecycle_factory(None)
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.local.response = frappe._dict()
        clear_principal_cache()
        self.jube = FakeLifecycle()
        decisions_store.set_lifecycle_factory(lambda _tenant: self.jube)
        self.decision_id = self.approved_decision(self.tp_a, "close_after_correction")

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.request = None
        decisions_store.set_lifecycle_factory(None)

    # --- helpers ---------------------------------------------------------------------

    def as_user(self, name: str) -> str:
        frappe.set_user(self.users[name])
        clear_principal_cache()
        return self.users[name]

    def _version(self, case_id: str) -> int:
        return decisions_store.FrappeRepository().case_version(self.t1, case_id)

    def upload(self, who: str = "taxpayer-a", tenant: str | None = None) -> str:
        """The portal upload path (TAX-032) with the tenant header."""
        self.as_user(who)
        frappe.local.request = Request(
            EnvironBuilder(
                method="POST",
                headers={"X-Vati-Tenant-ID": tenant or self.t1},
                data={"file": FileStorage(stream=io.BytesIO(pdf(key())), filename="invoice.pdf")},
            ).get_environ()
        )
        evidence = evidence_api.upload_evidence(document_type="invoice", idempotency_key=key())
        frappe.local.request = None
        return str(evidence["evidence_id"])

    def approved_decision(self, tp: str, outcome: str) -> str:
        evidence_id = self.upload()
        case_id = mapping.register_case(tenant_id=self.t1, idempotency_key=key(), taxpayer_id=tp)[
            "case_id"
        ]
        self.jube.cases[case_id] = FakeCase("AWAITING_HUMAN")
        self.as_user("auditor")
        proposed = decisions_store.propose_decision(
            tenant_id=self.t1,
            case_id=case_id,
            body={
                "outcome": outcome,
                "rationale": "The March purchase invoices were missing from the schedule.",
                "rating": 2,
                "evidence_ids": [evidence_id],
                "expected_case_status": "AWAITING_HUMAN",
                "expected_version": self._version(case_id),
            },
            idempotency_key=key(),
        )
        self.as_user("supervisor")
        decisions_store.review_decision(
            tenant_id=self.t1,
            decision_id=proposed["decision_id"],
            action="approve",
            body={"expected_version": self._version(case_id)},
            idempotency_key=key(),
        )
        frappe.set_user("Administrator")
        return str(proposed["decision_id"])

    def record(self, who: str = "auditor", tenant: str | None = None, **changes):
        self.as_user(who)
        body = {"decision_id": self.decision_id, "topic": "missing_invoices",
                "explanation": EXPLANATION}  # fmt: skip
        body.update(changes)
        return store.record(
            tenant_id=tenant or self.t1, body=body, idempotency_key=key(), correlation_id=None
        )

    def correct(self, reminder, who: str = "auditor", **changes):
        self.as_user(who)
        args: dict[str, Any] = {
            "action": "correct",
            "reason": REASON,
            "topic": None,
            "explanation": None,
        }
        args.update(changes)
        return store.correct(
            tenant_id=self.t1,
            reminder_id=reminder["reminder_id"],
            expected_version=reminder["version"],
            correlation_id=None,
            **args,
        )

    def portal(self, who: str):
        self.as_user(who)
        return portal_api.get_correction_reminders()

    def shown(self, reminder_id: str, who: str = "taxpayer-a") -> dict | None:
        """This test's reminder in the portal list, if visible.

        Earlier tests leave committed reminders for the same synthetic taxpayer,
        so assertions look for this test's reminder rather than an empty list.
        """
        return next((r for r in self.portal(who)["data"] if r["reminder_id"] == reminder_id), None)

    def ledger_actions(self, subject_id: str) -> list[str]:
        return frappe.get_all(
            AUDIT, filters={"subject_id": subject_id}, pluck="action", order_by="creation asc"
        )

    # --- tests -----------------------------------------------------------------------

    def test_recorded_reminder_is_persisted_audited_and_shown_to_its_taxpayer(self):
        view = self.record()
        frappe.set_user("Administrator")
        row = frappe.get_doc(REMINDER, view["reminder_id"])
        self.assertEqual((row.tenant_id, row.taxpayer_id, row.status, row.version),
                         (self.t1, self.tp_a, "active", 1))  # fmt: skip
        self.assertEqual(row.reminder_key, rules.reminder_key(self.t1, self.decision_id,
                                                              "missing_invoices"))  # fmt: skip
        self.assertEqual(self.ledger_actions(view["reminder_id"]), ["reminder.recorded"])
        result = self.portal("taxpayer-a")
        self.assertEqual(result["mode"], "live")
        shown = next(r for r in result["data"] if r["reminder_id"] == view["reminder_id"])
        self.assertEqual(shown["explanation"], EXPLANATION)
        text = json.dumps(result)
        for internal in (self.decision_id, row.case_id, self.users["auditor"], "history"):
            self.assertNotIn(internal, text)

    def test_no_cross_taxpayer_or_cross_tenant_leakage(self):
        self.record()
        self.assertEqual(self.portal("taxpayer-b")["data"], [])
        self.assertEqual(self.portal("taxpayer-c")["data"], [])
        # No DocType access for taxpayers: the portal method is the only way in.
        for who in ("taxpayer-a", "taxpayer-b"):
            user = self.as_user(who)
            self.assertFalse(frappe.has_permission(REMINDER, "read", user=user))
            with self.assertRaises(frappe.PermissionError):
                frappe.get_list(REMINDER, fields=["name"])
        # A taxpayer cannot use the staff history either.
        self.as_user("taxpayer-a")
        with self.assertRaises(frappe.PermissionError):
            store.staff_list(tenant_id=self.t1, taxpayer_id=self.tp_a)
        # Staff in another tenant cannot record from, or see, tenant one's records.
        with self.assertRaises(rules.ReminderNotFound):
            self.record("auditor-t2", tenant=self.t2, topic="payment_details")
        self.as_user("auditor-t2")
        self.assertEqual(
            store.staff_list(tenant_id=self.t2, taxpayer_id=self.tp_a)["reminders"], []
        )

    def test_correction_and_withdrawal_are_kept_and_change_what_the_taxpayer_sees(self):
        first = self.record()
        corrected = self.correct(
            first, explanation="Your February claim listed fewer purchase invoices than it claimed."
        )
        self.assertEqual(corrected["version"], 2)
        self.assertIn("February", (self.shown(first["reminder_id"]) or {})["explanation"])
        withdrawn = self.correct(corrected, who="supervisor", action="withdraw")
        self.assertEqual(withdrawn["status"], "withdrawn")
        self.assertIsNone(self.shown(first["reminder_id"]))
        frappe.set_user("Administrator")
        row = frappe.get_doc(REMINDER, first["reminder_id"])
        history = json.loads(row.history)
        self.assertEqual([h["version"] for h in history], [1, 2])
        self.assertEqual(history[0]["explanation"], EXPLANATION)
        self.assertEqual(
            self.ledger_actions(first["reminder_id"]),
            ["reminder.recorded", "reminder.corrected", "reminder.withdrawn"],
        )
        with self.assertRaises(rules.ReminderConflict):
            self.correct(withdrawn, explanation="Bring this reminder back with new wording.")
        self.assertIn("reminder.correct.denied", self.ledger_actions(first["reminder_id"]))

    def test_specialist_and_taxpayer_cannot_record_and_refusals_are_kept(self):
        for who in ("specialist", "taxpayer-a"):
            with self.subTest(who=who), self.assertRaises(frappe.PermissionError):
                self.record(who)
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.count(REMINDER, {"decision_id": self.decision_id}), 0)
        denied = frappe.get_all(AUDIT, filters={"action": "reminder.record.denied",
                                                "tenant_id": self.t1}, pluck="actor")  # fmt: skip
        self.assertTrue({self.users["specialist"], self.users["taxpayer-a"]} <= set(denied))

    def test_overturned_source_hides_the_reminder(self):
        reminder = self.record()
        self.assertIsNotNone(self.shown(reminder["reminder_id"]))
        self.as_user("taxpayer-a")
        objection = challenge_store.lodge(
            tenant_id=self.t1,
            body={
                "stage": "objection",
                "decision_id": self.decision_id,
                "grounds": "The March invoices were in the second upload, not missing.",
            },  # fmt: skip
            idempotency_key=key(),
            correlation_id=None,
        )
        self.assertIsNone(self.shown(reminder["reminder_id"]))  # under review
        self.as_user("supervisor-2")
        challenge_store.review(
            tenant_id=self.t1,
            challenge_id=objection["challenge_id"],
            expected_version=objection["version"],
            outcome="overturned",
            reason="The second upload did contain the March purchase invoices.",
            correlation_id=None,
        )
        self.assertIsNone(self.shown(reminder["reminder_id"]))
        self.as_user("auditor")
        staff = next(
            r
            for r in store.staff_list(tenant_id=self.t1, taxpayer_id=self.tp_a)["reminders"]
            if r["reminder_id"] == reminder["reminder_id"]
        )
        self.assertEqual(staff["source_state"], "overturned")
        self.assertFalse(staff["visible_to_taxpayer"])

    def test_doctype_backstop_freezes_the_source_and_keeps_rows(self):
        view = self.record()
        frappe.set_user("Administrator")
        doc = frappe.get_doc(REMINDER, view["reminder_id"])
        doc.decision_id = str(uuid.uuid4())
        doc.version = 2
        doc.updated_by = "Administrator"
        doc.change_reason = REASON
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
        doc = frappe.get_doc(REMINDER, view["reminder_id"])
        doc.explanation = "Changed without moving the version forward at all."
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(REMINDER, view["reminder_id"], ignore_permissions=True)
        with self.assertRaises(frappe.UniqueValidationError):
            frappe.get_doc(
                {
                    "doctype": REMINDER,
                    "reminder_id": str(uuid.uuid4()),
                    "tenant_id": self.t1,
                    "taxpayer_id": self.tp_a,
                    "case_id": doc.case_id,
                    "decision_id": self.decision_id,
                    "topic": "missing_invoices",
                    "status": "active",
                    "version": 1,
                    "explanation": EXPLANATION,
                    "request_digest": "0" * 64,
                    "recorded_by": "Administrator",
                    "recorded_at": frappe.utils.now_datetime(),
                }  # fmt: skip
            ).insert(ignore_permissions=True)

    def test_presubmission_review_marks_prompts_the_taxpayer_was_reminded_about(self):
        reminder = self.record()
        evidence = {"evidence_id": self.upload()}
        self.as_user("taxpayer-a")
        frappe.local.request = Request(EnvironBuilder(method="POST").get_environ())
        result = portal_api.review_refund_claim(
            period="2026-09",
            claimed_amount="20000.00",
            claim_reason="excess_input_vat",
            evidence_ids=[evidence["evidence_id"]],
            period_output_vat="0.00",
            period_input_vat="20000.00",
            invoices=[
                {
                    "invoice_number": "INV-0912",
                    "invoice_date": "2026-09-12",
                    "supplier_name": "Synthetic Supplies",
                    "net_amount": "100000.00",
                    "vat_amount": "15000.00",
                    "gross_amount": "115000.00",
                    "invoice_evidence_id": evidence["evidence_id"],
                }
            ],  # fmt: skip
        )
        marked = {p["code"]: p["reminder_id"] for p in result["data"]["prompts"]}
        self.assertEqual(marked["refund_claim_exceeds_documented_input_vat"],
                         reminder["reminder_id"])  # fmt: skip
        self.assertIsNone(marked["payment_evidence_missing"])
        # Another taxpayer's review is never marked with taxpayer A's reminder.
        self.assertNotIn(reminder["reminder_id"], json.dumps(self.portal("taxpayer-b")))
