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
import json
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
        grants: list[tuple[str, str, policy.Role, str | None]] = [
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
        # TAX-125: the claim form is the fixture-only Online Refunds simulator.
        self._simulator = frappe.local.conf.get("vati_online_refunds_simulator")
        frappe.local.conf["vati_online_refunds_simulator"] = True

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.request = None
        frappe.local.conf["vati_online_refunds_simulator"] = self._simulator
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
            # TAX-044: period figures and one invoice backed by the first document.
            "period_output_vat": "0.00",
            "period_input_vat": "15000.00",
            "invoices": [self.invoice(evidence_ids[0])] if evidence_ids else [],
            **overrides,
        }
        return portal_api.submit_refund_claim(**args)

    @staticmethod
    def invoice(evidence_id, **overrides):
        row = {
            "invoice_number": "INV-0912",
            "invoice_date": "2026-09-12",
            "supplier_name": "Synthetic Supplies",
            "net_amount": "100000.00",
            "vat_amount": "15000.00",
            "gross_amount": "115000.00",
            "invoice_evidence_id": evidence_id,
        }
        row.update(overrides)
        return row

    def review(self, who: str, evidence_ids, **overrides):
        self.as_user(who)
        frappe.local.request = Request(EnvironBuilder(method="POST").get_environ())
        args = {
            "period": "2026-09",
            "claimed_amount": "15000.00",
            "claim_reason": "excess_input_vat",
            "evidence_ids": evidence_ids,
            "period_output_vat": "0.00",
            "period_input_vat": "15000.00",
            "invoices": [self.invoice(evidence_ids[0])] if evidence_ids else [],
            **overrides,
        }
        return portal_api.review_refund_claim(**args)

    def error(self) -> dict:
        return frappe.local.response["error"]

    def count(self, doctype: str, **filters) -> int:
        return frappe.db.count(doctype, filters)

    # --- simulator only (TAX-125) ------------------------------------------------------

    def test_without_the_simulator_claims_are_refused_and_the_session_says_so(self):
        frappe.local.conf["vati_online_refunds_simulator"] = None
        evidence = self.upload("taxpayer-a", self.t1)
        before = self.count(SUBMISSION, tenant_id=self.t1)
        with self.assertRaises(frappe.PermissionError):
            self.claim("taxpayer-a", [evidence["evidence_id"]])
        with self.assertRaises(frappe.PermissionError):
            self.review("taxpayer-a", [evidence["evidence_id"]])
        self.assertEqual(self.count(SUBMISSION, tenant_id=self.t1), before)
        self.as_user("taxpayer-a")
        self.assertFalse(portal_api.get_session()["simulator_enabled"])
        frappe.local.conf["vati_online_refunds_simulator"] = True
        self.assertTrue(portal_api.get_session()["simulator_enabled"])

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
                ("invoices", "required"),
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

    # --- TAX-044: invoice schedule and pre-submission reconciliation ------------------

    def test_schedule_and_period_figures_are_stored_with_server_totals(self):
        first = self.upload("taxpayer-a", self.t1)["evidence_id"]
        second = self.upload("taxpayer-a", self.t1)["evidence_id"]
        rows = [
            self.invoice(first, amount_paid="115000.00", contract_evidence_id=second),
            self.invoice(
                second,
                invoice_number="INV-0913",
                net_amount="200.00",
                vat_amount="30.00",
                gross_amount="230.00",
            ),
        ]
        result = self.claim(
            "taxpayer-a",
            [first, second],
            invoices=rows,
            claimed_amount="15030.00",
            period_input_vat="15030.00",
        )
        frappe.set_user("Administrator")
        claim = frappe.get_doc(REFUND, {"submission_id": result["data"]["submission_id"]})
        self.assertEqual(len(claim.invoices), 2)
        totals = (
            float(claim.invoice_net_total),
            float(claim.invoice_vat_total),
            float(claim.invoice_gross_total),
        )
        self.assertEqual(totals, (100200.0, 15030.0, 115230.0))
        figures = (float(claim.period_output_vat), float(claim.period_input_vat))
        self.assertEqual(figures, (0.0, 15030.0))
        self.assertEqual(claim.invoices[0].contract_evidence_id, second)

    def test_review_shows_figures_and_guidance_and_stores_nothing(self):
        evidence = self.upload("taxpayer-a", self.t1)["evidence_id"]
        before = self.count(SUBMISSION, taxpayer_id=self.tp_a)
        # Incomplete evidence: claim exceeds the invoices, no payment, no contract,
        # and the period arithmetic does not add up (acceptance criterion 2).
        result = self.review(
            "taxpayer-a", [evidence], claimed_amount="20000.00", period_output_vat="100.00"
        )
        data = result["data"]
        self.assertEqual(result["mode"], "live")
        self.assertEqual(data["figures"]["documented_input_vat"], "15000.00")
        expected = {
            "refund_claim_exceeds_documented_input_vat",
            "return_arithmetic_mismatch",
            "payment_evidence_missing",
            "contract_evidence_missing",
        }
        self.assertEqual({p["code"] for p in data["prompts"]}, expected)
        text = json.dumps(result).lower()
        for word in ("fraud", "abuse", "suspic", "penalt", "lane", "risk", "investigat"):
            self.assertNotIn(word, text)
        self.assertEqual(self.count(SUBMISSION, taxpayer_id=self.tp_a), before)

    def test_fully_evidenced_review_is_complete(self):
        invoice = self.upload("taxpayer-a", self.t1)["evidence_id"]
        contract = self.upload("taxpayer-a", self.t1)["evidence_id"]
        row = self.invoice(invoice, amount_paid="115000.00", contract_evidence_id=contract)
        result = self.review("taxpayer-a", [invoice, contract], invoices=[row])
        self.assertTrue(result["data"]["complete"])
        self.assertEqual(result["data"]["figures"]["explained_amount"], "15000.00")

    def test_review_refuses_another_taxpayers_documents(self):
        theirs = self.upload("taxpayer-b", self.t1)["evidence_id"]
        with self.assertRaises(portal_api.PortalRequestError):
            self.review("taxpayer-a", [theirs])
        self.assertEqual(self.error()["field_errors"][0]["code"], "not_found")

    def test_schedule_problems_are_reported_by_row(self):
        evidence = self.upload("taxpayer-a", self.t1)["evidence_id"]
        bad = self.invoice(evidence, gross_amount="115000.01")
        with self.assertRaises(portal_api.PortalRequestError):
            self.claim("taxpayer-a", [evidence], invoices=[bad])
        self.assertEqual(
            [(e["field"], e["code"]) for e in self.error()["field_errors"]],
            [("invoices.0.gross_amount", "invoice_total_mismatch")],
        )

    def test_changed_schedule_under_the_same_key_conflicts(self):
        evidence = self.upload("taxpayer-a", self.t1)["evidence_id"]
        retry_key = key()
        rows = [self.invoice(evidence)]
        self.claim("taxpayer-a", [evidence], idempotency_key=retry_key, invoices=rows)
        frappe.db.commit()
        try:
            again = self.claim("taxpayer-a", [evidence], idempotency_key=retry_key, invoices=rows)
            self.assertTrue(again["replayed"])
            changed = [{**rows[0], "supplier_name": "Synthetic Other Supplier"}]
            frappe.local.response = frappe._dict()
            with self.assertRaises(portal_api.PortalRequestError):
                self.claim("taxpayer-a", [evidence], idempotency_key=retry_key, invoices=changed)
            self.assertEqual(self.error()["code"], "idempotency_conflict")
        finally:
            frappe.set_user("Administrator")
            for name in frappe.get_all(SUBMISSION, {"idempotency_key": retry_key}, pluck="name"):
                for claim in frappe.get_all(REFUND, {"submission_id": name}, pluck="name"):
                    frappe.db.delete("Vati Refund Claim Invoice", {"parent": claim})
                frappe.db.delete(REFUND, {"submission_id": name})
                frappe.db.delete(SUBMISSION, {"name": name})
            frappe.db.commit()

    def test_doctype_refuses_bad_rows_and_foreign_row_documents(self):
        own = self.upload("taxpayer-a", self.t1)["evidence_id"]
        theirs = self.upload("taxpayer-b", self.t1)["evidence_id"]
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

        def new_claim(row):
            return frappe.get_doc(
                {
                    "doctype": REFUND,
                    "submission_id": submission.name,
                    "tenant_id": self.t1,
                    "taxpayer_id": self.tp_a,
                    "claimed_amount": "100.00",
                    "claim_reason": "excess_input_vat",
                    "invoices": [row],
                }
            ).insert()

        bad_rows = {
            "total mismatch": self.invoice(own, invoice_date="2026-08-01", gross_amount="1.00"),
            "after the period": self.invoice(own, invoice_date="2026-09-15"),
            "another taxpayer's document": self.invoice(theirs, invoice_date="2026-08-01"),
        }
        for label, bad in bad_rows.items():
            with self.subTest(label):
                with self.assertRaises(InvalidDomainInput):
                    new_claim(bad)

    # --- live submissions list and status (TAX-092) ------------------------------------

    def sent_claim(self, who: str = "taxpayer-a", tenant: str | None = None) -> str:
        evidence = self.upload(who, tenant or self.t1)
        return self.claim(who, [evidence["evidence_id"]])["data"]["submission_id"]

    def mirror(self, submission_id: str, review_status: str) -> None:
        """What the TAX-039 bridge writes when Jube reports a case state (a direct DB write)."""
        frappe.db.set_value(
            SUBMISSION,
            submission_id,
            {"review_status": review_status, "review_status_at": frappe.utils.now_datetime()},
            update_modified=False,
        )

    def test_session_and_lists_are_live(self):
        self.as_user("taxpayer-a")
        self.assertEqual(portal_api.get_session()["mode"], "live")
        self.assertNotIn("synthetic", portal_api.get_session())
        listed = portal_api.get_submissions()
        self.assertEqual((listed["mode"], listed["data"]), ("live", []))
        # Fixture records are no longer served.
        with self.assertRaises(frappe.DoesNotExistError):
            portal_api.get_submission("SUB-SYN-0001")

    def test_a_sent_claim_appears_in_the_list_with_its_status(self):
        submission_id = self.sent_claim()
        self.as_user("taxpayer-a")
        items = portal_api.get_submissions()["data"]
        self.assertEqual([i["id"] for i in items], [submission_id])
        item = items[0]
        reference = frappe.db.get_value(SUBMISSION, submission_id, "reference")
        self.assertEqual(
            {k: item[k] for k in ("reference", "tax_type", "period", "amount", "currency", "status",
                                  "taxpayer_name")},
            {"reference": reference, "tax_type": "VAT refund claim", "period": "2026-09",
             "amount": "15000.00", "currency": "LSL", "status": "under_review",
             "taxpayer_name": "Synthetic Mountain Wholesale"},
        )  # fmt: skip
        self.assertEqual(item["steps"][1], {"label": "Officer review", "state": "current",
                                            "at": None})  # fmt: skip
        detail = portal_api.get_submission(submission_id)
        self.assertEqual((detail["mode"], detail["data"]), ("live", item))

    def test_the_mirrored_review_status_is_what_the_taxpayer_sees(self):
        submission_id = self.sent_claim()
        for status in ("evidence_requested", "completed"):
            with self.subTest(status=status):
                frappe.set_user("Administrator")
                self.mirror(submission_id, status)
                self.as_user("taxpayer-a")
                item = portal_api.get_submission(submission_id)["data"]
                self.assertEqual(item["status"], status)
                self.assertTrue(item["steps"][-1]["at"] or item["steps"][1]["at"])

    def test_other_taxpayers_and_tenants_never_see_it(self):
        submission_id = self.sent_claim()
        for who in ("taxpayer-b", "taxpayer-c"):
            with self.subTest(who=who):
                self.as_user(who)
                self.assertNotIn(
                    submission_id, [i["id"] for i in portal_api.get_submissions()["data"]]
                )
                with self.assertRaises(frappe.DoesNotExistError):
                    portal_api.get_submission(submission_id)
        # Their own claim is listed for them, and only theirs.
        theirs = self.sent_claim("taxpayer-b")
        self.as_user("taxpayer-b")
        self.assertEqual([i["id"] for i in portal_api.get_submissions()["data"]], [theirs])

    def test_staff_cannot_use_the_taxpayer_list(self):
        self.as_user("auditor-t1")
        with self.assertRaises(frappe.PermissionError):
            portal_api.get_submissions()

    def test_internal_case_and_risk_detail_never_reach_the_view(self):
        submission_id = self.sent_claim()
        frappe.set_user("Administrator")
        case_id = frappe.db.get_value(SUBMISSION, submission_id, "case_id")
        self.as_user("taxpayer-a")
        payloads = [portal_api.get_submissions(), portal_api.get_submission(submission_id)]
        text = json.dumps(payloads, default=str)
        self.assertNotRegex(text, r"(?i)risk|lane|score|jube|finding|case_id|tenant_id")
        if case_id:
            self.assertNotIn(case_id, text)
        allowed = {"id", "reference", "taxpayer_name", "tax_type", "period", "amount", "currency",
                   "status", "submitted_at", "next_step", "steps",
                   # TAX-125: where it came from; RSL's block only for a claim filed with RSL.
                   "origin", "rsl"}  # fmt: skip
        self.assertEqual(set(payloads[1]["data"]), allowed)
        self.assertEqual((payloads[1]["data"]["origin"], payloads[1]["data"]["rsl"]),
                         ("portal", None))  # fmt: skip
