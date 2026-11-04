"""Bench tests for the TAX-043 VAT refund claim form API.

Run on a disposable site (``scripts/frappe_dev.py verify`` does this)::

    bench --site SITE run-tests --module vati_tax.portal.test_refund_claim_bench

Under plain ``unittest`` without a site this module is skipped, not passed. All
users, tenants, taxpayers, amounts and documents are synthetic.

Covers the task's verification list: a valid submission, a duplicate retry, an
invalid document and cross-tenant (and cross-taxpayer) evidence access.
"""

from __future__ import annotations

import io
import unittest
import uuid

try:
    import frappe
    from frappe.tests import IntegrationTestCase
    from werkzeug.datastructures import FileStorage
    from werkzeug.test import EnvironBuilder
    from werkzeug.wrappers import Request

    from vati_tax.evidence import api as evidence_api
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import TenantScopeDenied, clear_principal_cache
    from vati_tax.identity.provision import ensure_tenant, grant_membership
    from vati_tax.portal import api as portal_api
    from vati_tax.portal import claims
    from vati_tax.submissions.guard import InvalidDomainInput
    from vati_tax.vati_tax.doctype.vati_refund_claim import vati_refund_claim
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

TAXPAYER = "Vati Taxpayer"
SUBMISSION = "Vati Submission"
REFUND = "Vati Refund Claim"
EVIDENCE = "Vati Evidence"
RUN = uuid.uuid4().hex[:8]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def uid() -> str:
    return str(uuid.uuid4())


def key() -> str:
    return "tax043-" + uuid.uuid4().hex


def pdf(tag: str) -> bytes:
    """A minimal valid one-page PDF (Frappe checks uploaded PDFs with pypdf)."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
        b"<< /Producer (synthetic claim evidence " + tag.encode() + b") >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 4 0 R >>\n".encode()
    out += f"startxref\n{xref}\n%%EOF\n".encode()
    return out


def make_user(name: str) -> str:
    user = f"tax043-{name}-{RUN}@example.test"
    frappe.get_doc(
        {
            "doctype": "User",
            "email": user,
            "first_name": f"Synthetic {name}",
            "send_welcome_email": 0,
        }  # fmt: skip
    ).insert(ignore_permissions=True)
    return user


class RefundClaimPortalTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.t1 = ensure_tenant(uid(), "Synthetic claim tenant one")
        cls.t2 = ensure_tenant(uid(), "Synthetic claim tenant two")
        cls.tp_a = cls.taxpayer(cls.t1, "Synthetic Mountain Wholesale", "430001001")
        cls.tp_b = cls.taxpayer(cls.t1, "Synthetic Border Retail", "430001002")
        cls.tp_c = cls.taxpayer(cls.t2, "Synthetic Highland Imports", "430001001")
        cls.users = {}
        grants = [
            ("taxpayer-a", cls.t1, policy.TAXPAYER, cls.tp_a),
            ("taxpayer-b", cls.t1, policy.TAXPAYER, cls.tp_b),
            ("taxpayer-c", cls.t2, policy.TAXPAYER, cls.tp_c),
            ("auditor-t1", cls.t1, policy.AUDITOR, None),
        ]
        for name, tenant, role, taxpayer in grants:
            cls.users[name] = make_user(name)
            grant_membership(cls.users[name], tenant, role, taxpayer)
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.local.request = None
        frappe.db.rollback()
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.local.response = frappe._dict()
        clear_principal_cache()

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.request = None
        frappe.db.rollback()

    # --- helpers ---------------------------------------------------------------------

    @staticmethod
    def taxpayer(tenant: str, name: str, tin: str) -> str:
        return (
            frappe.get_doc(
                {
                    "doctype": TAXPAYER,
                    "tenant_id": tenant,
                    "display_name": name,
                    "taxpayer_type": "company",
                    "tin": tin,
                }  # fmt: skip
            )
            .insert()
            .name
        )

    def as_user(self, name: str) -> None:
        frappe.set_user(self.users[name])
        clear_principal_cache()

    def upload(self, who: str, tenant: str, content: bytes | None = None, filename="invoice.pdf"):
        """The portal's upload path: the whitelisted multipart method with the tenant header."""
        self.as_user(who)
        builder = EnvironBuilder(
            method="POST",
            headers={"X-Vati-Tenant-ID": tenant},
            data={
                "file": FileStorage(
                    stream=io.BytesIO(content if content is not None else pdf(uid())),
                    filename=filename,
                )
            },
        )
        frappe.local.request = Request(builder.get_environ())
        return evidence_api.upload_evidence(document_type="invoice", idempotency_key=key())

    def claim(self, who: str, evidence_ids, **overrides):
        self.as_user(who)
        frappe.local.request = Request(EnvironBuilder(method="POST").get_environ())
        args = {
            "idempotency_key": key(),
            "period": "2026-09",
            "claimed_amount": "15000.00",
            "claim_reason": "excess_input_vat",
            "explanation": None,
            "contact_email": "accounts@example.test",
            "evidence_ids": evidence_ids,
            "declaration": 1,
            **overrides,
        }
        return portal_api.submit_refund_claim(**args)

    def error(self) -> dict:
        return frappe.local.response["error"]

    def count(self, doctype: str, **filters) -> int:
        return frappe.db.count(doctype, filters)

    # --- valid submission ------------------------------------------------------------

    def test_valid_claim_is_stored_submitted_and_linked_to_own_evidence(self):
        evidence = self.upload("taxpayer-a", self.t1)
        result = self.claim("taxpayer-a", [evidence["evidence_id"]])

        self.assertEqual((result["mode"], result["replayed"]), ("live", False))
        data = result["data"]
        self.assertEqual(data["status"], "submitted")
        self.assertTrue(data["reference"].startswith("REF-"))
        self.assertEqual((data["claimed_amount"], data["currency"]), ("15000.00", "LSL"))
        self.assertEqual((data["period"], data["evidence_count"]), ("2026-09", 1))

        frappe.set_user("Administrator")
        sub = frappe.get_doc(SUBMISSION, data["submission_id"])
        self.assertEqual(
            (sub.tenant_id, sub.taxpayer_id, sub.submission_type, sub.source, sub.declaration),
            (self.t1, self.tp_a, "refund_claim", "portal", 1),
        )
        self.assertEqual(sub.submitted_by, self.users["taxpayer-a"])
        claim = frappe.get_doc(REFUND, {"submission_id": sub.name})
        self.assertEqual(frappe.parse_json(claim.evidence_ids), [evidence["evidence_id"]])
        # Stored evidence stays private: the view never exposes a path or URL.
        self.assertNotIn("file_url", evidence)

    def test_session_reports_the_callers_own_taxpayer_scope(self):
        self.as_user("taxpayer-a")
        session = portal_api.get_session()
        self.assertEqual(session["taxpayers"], [{"tenant_id": self.t1, "taxpayer_id": self.tp_a}])

    def test_other_reason_needs_an_explanation(self):
        evidence = self.upload("taxpayer-a", self.t1)
        with self.assertRaises(portal_api.PortalRequestError):
            self.claim("taxpayer-a", [evidence["evidence_id"]], claim_reason="other")
        self.assertEqual(
            [e["field"] for e in self.error()["field_errors"]], ["explanation"]
        )  # fmt: skip
        result = self.claim(
            "taxpayer-a",
            [evidence["evidence_id"]],
            claim_reason="other",
            explanation="Synthetic: capital equipment purchased before trading started.",
        )
        self.assertEqual(result["data"]["claim_reason"], "other")

    def test_invalid_answers_are_all_reported_and_nothing_is_stored(self):
        before = self.count(SUBMISSION, taxpayer_id=self.tp_a)
        with self.assertRaises(portal_api.PortalRequestError):
            self.claim(
                "taxpayer-a",
                [],
                period="2099-01",
                claimed_amount="0",
                claim_reason="refund_please",
                contact_email="not-an-email",
                declaration=0,
            )
        error = self.error()
        self.assertEqual(error["code"], "invalid_domain_input")
        self.assertTrue(error["correlation_id"])
        self.assertEqual(
            {(e["field"], e["code"]) for e in error["field_errors"]},
            {
                ("period", "future_period"),
                ("claimed_amount", "zero_amount"),
                ("claim_reason", "invalid_choice"),
                ("contact_email", "invalid_email"),
                ("evidence_ids", "evidence_required"),
                ("declaration", "declaration_required"),
            },
        )
        for field_error in error["field_errors"]:
            self.assertNotIn("not-an-email", field_error["message"])
        self.assertEqual(self.count(SUBMISSION, taxpayer_id=self.tp_a), before)

    def test_claim_reasons_match_the_doctype(self):
        self.assertEqual(claims.REASONS, vati_refund_claim.REASONS)

    def test_staff_cannot_send_a_taxpayer_claim(self):
        with self.assertRaises(frappe.PermissionError):
            self.claim("auditor-t1", [uid()])

    # --- duplicate retry -------------------------------------------------------------

    def test_duplicate_retry_replays_and_a_changed_request_conflicts(self):
        evidence = self.upload("taxpayer-a", self.t1)
        retry_key = key()
        first = self.claim("taxpayer-a", [evidence["evidence_id"]], idempotency_key=retry_key)
        frappe.db.commit()  # as after the first HTTP response
        try:
            again = self.claim("taxpayer-a", [evidence["evidence_id"]], idempotency_key=retry_key)
            self.assertTrue(again["replayed"])
            self.assertEqual(again["data"], first["data"])
            self.assertEqual(
                self.count(SUBMISSION, idempotency_key=retry_key, taxpayer_id=self.tp_a), 1
            )

            with self.assertRaises(portal_api.PortalRequestError):
                self.claim(
                    "taxpayer-a",
                    [evidence["evidence_id"]],
                    idempotency_key=retry_key,
                    claimed_amount="15000.01",
                )
            self.assertEqual(self.error()["code"], "idempotency_conflict")
            self.assertEqual(self.count(REFUND, submission_id=first["data"]["submission_id"]), 1)

            # The key is scoped to the taxpayer: B's request never reaches A's claim.
            other = self.upload("taxpayer-b", self.t1)
            own = self.claim("taxpayer-b", [other["evidence_id"]], idempotency_key=retry_key)
            self.assertFalse(own["replayed"])
            self.assertNotEqual(own["data"]["submission_id"], first["data"]["submission_id"])
        finally:
            frappe.set_user("Administrator")
            for name in frappe.get_all(SUBMISSION, {"idempotency_key": retry_key}, pluck="name"):
                frappe.db.delete(REFUND, {"submission_id": name})
                frappe.db.delete(SUBMISSION, {"name": name})
            frappe.db.commit()

    # --- invalid documents -----------------------------------------------------------

    def test_invalid_documents_are_rejected_and_not_stored(self):
        before = self.count(EVIDENCE, taxpayer_id=self.tp_a)
        cases = [
            (b"MZsynthetic", "tool.exe"),  # disallowed type
            (PNG, "invoice.pdf"),  # renamed: content does not match the extension
            (b"", "empty.pdf"),  # empty
            (b"%PDF-1.4\n% synthetic broken pdf\n", "broken.pdf"),  # malformed PDF
        ]
        for content, filename in cases:
            with self.subTest(filename=filename):
                with self.assertRaises(frappe.ValidationError):
                    self.upload("taxpayer-a", self.t1, content, filename)
        self.assertEqual(self.count(EVIDENCE, taxpayer_id=self.tp_a), before)

    # --- cross-tenant and cross-taxpayer evidence --------------------------------------

    def test_claim_cannot_reference_another_taxpayers_or_tenants_evidence(self):
        foreign = {
            "same tenant, other taxpayer": self.upload("taxpayer-b", self.t1)["evidence_id"],
            "other tenant": self.upload("taxpayer-c", self.t2)["evidence_id"],
            "unknown": uid(),
        }
        own = self.upload("taxpayer-a", self.t1)["evidence_id"]
        before = self.count(SUBMISSION, taxpayer_id=self.tp_a)
        for label, evidence_id in foreign.items():
            with self.subTest(label):
                frappe.local.response = frappe._dict()
                with self.assertRaises(portal_api.PortalRequestError):
                    self.claim("taxpayer-a", [own, evidence_id])
                # Missing and someone else's evidence are indistinguishable.
                self.assertEqual(
                    [(e["field"], e["code"]) for e in self.error()["field_errors"]],
                    [("evidence_ids", "not_found")],
                )
        self.assertEqual(self.count(SUBMISSION, taxpayer_id=self.tp_a), before)

    def test_refund_claim_doctype_refuses_foreign_evidence_directly(self):
        # The DocType guard holds even when the portal API is bypassed (/api/resource).
        foreign = self.upload("taxpayer-b", self.t1)["evidence_id"]
        self.as_user("taxpayer-a")
        submission = frappe.get_doc(
            {
                "doctype": SUBMISSION,
                "tenant_id": self.t1,
                "taxpayer_id": self.tp_a,
                "submission_type": "refund_claim",
                "period": "2026-08",
            }
        ).insert()
        with self.assertRaises(InvalidDomainInput):
            frappe.get_doc(
                {
                    "doctype": REFUND,
                    "submission_id": submission.name,
                    "tenant_id": self.t1,
                    "taxpayer_id": self.tp_a,
                    "claimed_amount": "100.00",
                    "claim_reason": "excess_input_vat",
                    "evidence_ids": [foreign],
                }
            ).insert()

    def test_taxpayer_cannot_read_or_upload_into_another_scope(self):
        theirs = self.upload("taxpayer-b", self.t1)["evidence_id"]
        other_tenant = self.upload("taxpayer-c", self.t2)["evidence_id"]
        self.as_user("taxpayer-a")
        frappe.local.request = Request(
            EnvironBuilder(method="GET", headers={"X-Vati-Tenant-ID": self.t1}).get_environ()
        )
        for evidence_id in (theirs, other_tenant):
            with self.subTest(evidence_id=evidence_id):
                with self.assertRaises(frappe.DoesNotExistError):
                    evidence_api.get_evidence(evidence_id)
        with self.assertRaises(TenantScopeDenied):
            self.upload("taxpayer-a", self.t2)
