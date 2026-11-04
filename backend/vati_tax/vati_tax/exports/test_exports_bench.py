"""Bench tests for evidence export, manifest and retention (TAX-062).

Run on a disposable site (``scripts/frappe_dev.py verify`` does this)::

    bench --site SITE run-tests --module vati_tax.exports.test_exports_bench

Plain ``unittest`` without a site skips this module. Every user, tenant,
taxpayer and document is synthetic.
"""

from __future__ import annotations

import hashlib
import json
import unittest
import uuid

try:
    import frappe
    from frappe.tests import IntegrationTestCase

    from vati_tax.audit import ledger
    from vati_tax.audit import store as audit_store
    from vati_tax.evidence import store as evidence_store
    from vati_tax.evidence.test_evidence_bench import pdf
    from vati_tax.exports import rules, store
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.ids import mapping
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

EXPORT = store.EXPORT
AUDIT = audit_store.AUDIT
RUN = uuid.uuid4().hex[:8]
REASON = "Supervisory review of the refund evidence before the case conference."


def email(name: str) -> str:
    return f"tax062-{name}-{RUN}@example.test"


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


def reason(ctx) -> str:
    return ctx.exception.field_errors[0]["code"]


class ExportBenchTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.t1 = ensure_tenant(str(uuid.uuid4()), "Synthetic export tenant one")
        cls.t2 = ensure_tenant(str(uuid.uuid4()), "Synthetic export tenant two")
        cls.tp_a = str(uuid.uuid4())
        cls.users = {}
        grants: list[tuple[str, str, policy.Role, str | None]] = [
            ("taxpayer-a", cls.t1, policy.TAXPAYER, cls.tp_a),
            ("auditor", cls.t1, policy.AUDITOR, None),
            ("auditor-2", cls.t1, policy.AUDITOR, None),
            ("supervisor", cls.t1, policy.SUPERVISOR, None),
            ("supervisor-2", cls.t1, policy.SUPERVISOR, None),
            ("auditor-t2", cls.t2, policy.AUDITOR, None),
        ]
        for name, tenant, role, taxpayer in grants:
            cls.users[name] = make_user(name)
            grant_membership(cls.users[name], tenant, role, taxpayer)
        cls.users["service"] = ensure_service_identity(email("service"), [cls.t1])
        cls.case = mapping.register_case(
            tenant_id=cls.t1, idempotency_key=key(), taxpayer_id=cls.tp_a
        )["case_id"]
        cls.other_case = mapping.register_case(
            tenant_id=cls.t1, idempotency_key=key(), taxpayer_id=cls.tp_a
        )["case_id"]
        cls.t2_case = mapping.register_case(tenant_id=cls.t2, idempotency_key=key())["case_id"]
        frappe.db.commit()
        cls.content = {}
        cls.e1 = cls._upload("auditor", cls.t1, cls.case, "one")
        cls.e2 = cls._upload("auditor", cls.t1, cls.case, "two")
        cls.e_other_case = cls._upload("auditor", cls.t1, cls.other_case, "other")
        cls.e_t2 = cls._upload("auditor-t2", cls.t2, cls.t2_case, "t2")
        frappe.set_user("Administrator")
        frappe.db.commit()

    @classmethod
    def _upload(cls, who: str, tenant: str, case: str, tag: str) -> str:
        frappe.set_user(cls.users[who])
        clear_principal_cache()
        content = pdf(f"tax062-{tag}-{RUN}")
        view = evidence_store.register_upload(
            tenant_id=tenant,
            content=content,
            filename=f"synthetic-{tag}.pdf",
            document_type="invoice",
            idempotency_key=key(),
            taxpayer_id=cls.tp_a if tenant == cls.t1 else str(uuid.uuid4()),
            case_id=case,
        )
        cls.content[view["evidence_id"]] = content
        return view["evidence_id"]

    def setUp(self):
        frappe.set_user("Administrator")
        clear_principal_cache()

    def tearDown(self):
        frappe.set_user("Administrator")

    def as_user(self, name: str) -> str:
        frappe.set_user(self.users[name])
        clear_principal_cache()
        return self.users[name]

    def request(self, who="auditor", evidence=None, **changes):
        self.as_user(who)
        body = {
            "case_id": self.case,
            "evidence_ids": evidence or [self.e1, self.e2],
            "purpose": "supervisory_review",
            "recipient": "RSL case conference panel (synthetic)",
            "reason": REASON,
        }
        body.update(changes)
        return store.request_export(
            tenant_id=self.t1, body=body, idempotency_key=key(), correlation_id=None
        )

    def decide(self, export, who="supervisor", approve=True, text=None):
        self.as_user(who)
        return store.decide_export(
            tenant_id=self.t1,
            export_id=export["export_id"],
            expected_version=export["version"],
            approve=approve,
            reason=text,
            correlation_id=None,
        )

    def ledger_actions(self, export_id: str) -> list[str]:
        frappe.set_user("Administrator")
        return frappe.get_all(
            AUDIT, filters={"subject_id": export_id}, order_by="chain_seq asc", pluck="action"
        )

    # --- acceptance: hash/provenance manifest ---------------------------------------

    def test_approved_export_manifest_carries_the_stored_hashes(self):
        export = self.request()
        self.decide(export)
        self.as_user("auditor")
        manifest = store.get_manifest(tenant_id=self.t1, export_id=export["export_id"])
        self.assertTrue(rules.verify_manifest(manifest))
        self.assertFalse(manifest["content_included"])
        hashes = {e["evidence_id"]: e["document_sha256"] for e in manifest["entries"]}
        for evidence_id in (self.e1, self.e2):
            self.assertEqual(
                hashes[evidence_id], hashlib.sha256(self.content[evidence_id]).hexdigest()
            )
        self.assertNotIn("/files/", json.dumps(manifest))
        self.assertEqual(
            self.ledger_actions(export["export_id"]),
            ["export.requested", "export.approved", "export.read"],
        )
        frappe.set_user("Administrator")
        row = frappe.get_doc(EXPORT, export["export_id"])
        self.assertEqual((row.status, row.version), ("approved", 2))
        self.assertEqual(row.manifest_sha256, manifest["manifest_sha256"])

    # --- acceptance: unauthorised records are denied -------------------------------

    def test_evidence_outside_the_case_or_tenant_is_denied_and_recorded(self):
        before = frappe.db.count(EXPORT)
        for evidence in ([self.e1, self.e_other_case], [self.e_t2]):
            with self.subTest(evidence=evidence), self.assertRaises(rules.ExportNotFound):
                self.request(evidence=evidence)
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.count(EXPORT), before)
        denied = frappe.get_all(
            AUDIT,
            filters={
                "tenant_id": self.t1,
                "action": "export.request.denied",
                "actor": self.users["auditor"],
            },
            pluck="outcome",
        )
        self.assertGreaterEqual(denied.count("not_found"), 2)

    def test_taxpayer_and_service_are_refused_and_the_refusal_is_kept(self):
        for who in ("taxpayer-a", "service"):
            with self.subTest(who=who), self.assertRaises(frappe.PermissionError):
                self.request(who)
            frappe.set_user("Administrator")
            self.assertTrue(
                frappe.db.exists(
                    AUDIT, {"actor": self.users[who], "action": "export.request.denied"}
                )
            )

    def test_requester_cannot_approve_and_a_different_supervisor_must(self):
        export = self.request("supervisor")
        with self.assertRaises(frappe.PermissionError):
            self.decide(export, "supervisor")
        with self.assertRaises(frappe.PermissionError):
            self.decide(export, "auditor-2")
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.get_value(EXPORT, export["export_id"], "status"), "requested")
        approved = self.decide(export, "supervisor-2")
        self.assertEqual(approved["status"], "approved")

    def test_only_requester_and_approver_read_the_manifest(self):
        export = self.request()
        self.decide(export)
        self.as_user("auditor-2")
        with self.assertRaises(rules.ExportNotFound):
            store.get_manifest(tenant_id=self.t1, export_id=export["export_id"])
        self.as_user("supervisor")
        store.get_manifest(tenant_id=self.t1, export_id=export["export_id"])

    # --- record protection ----------------------------------------------------------

    def test_export_records_are_kept_and_frozen(self):
        export = self.request()
        frappe.set_user("Administrator")
        doc = frappe.get_doc(EXPORT, export["export_id"])
        doc.recipient = "Someone else entirely"
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(EXPORT, export["export_id"], ignore_permissions=True)

    def test_staff_cannot_read_export_rows_in_desk(self):
        export = self.request()
        for who in ("auditor", "supervisor"):
            self.as_user(who)
            self.assertFalse(frappe.has_permission(EXPORT, "read", doc=export["export_id"]))

    def test_ledger_stays_intact_after_exports(self):
        export = self.request()
        self.decide(export, text=None)
        frappe.set_user("Administrator")
        entries = audit_store.FrappeLedger().entries(self.t1)
        self.assertTrue(ledger.verify_chain(entries)["intact"])

    def test_retention_policy_is_unverified_and_ledger_is_permanent(self):
        self.as_user("auditor")
        view = store.retention_policy(tenant_id=self.t1)
        self.assertFalse(view["policy_verified"])
        classes = {c["retention_class"]: c for c in view["classes"]}
        self.assertTrue(classes["audit_ledger"]["permanent"])
        self.as_user("taxpayer-a")
        with self.assertRaises(frappe.PermissionError):
            store.retention_policy(tenant_id=self.t1)
