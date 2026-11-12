"""Bench tests for TAX-045 evidence request responses.

Run on a disposable site (``scripts/frappe_dev.py verify`` does this)::

    bench --site SITE run-tests --module vati_tax.responses.test_responses_bench

Under plain ``unittest`` without a site this module is skipped, not passed.
Requests are inserted as the TAX-046 store would (Administrator, synthetic
values); "internal" marker values prove what the taxpayer must never see.
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
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_tenant, grant_membership
    from vati_tax.portal import api as portal_api
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

REQUEST = "Vati Evidence Request"
RESPONSE = "Vati Evidence Response"
AUDIT = "Vati Audit Event"
RUN = uuid.uuid4().hex[:8]
INTERNAL_OFFICER = f"internal-officer-{RUN}@example.test"
INTERNAL_FINDING = str(uuid.uuid4())
ITEMS = [
    {"document_type": "invoice", "description": "Tax invoices for September 2026 purchases"},
    {"document_type": "bank_statement", "description": "Bank statement showing the payments"},
]


def uid() -> str:
    return str(uuid.uuid4())


def key() -> str:
    return "tax045-" + uuid.uuid4().hex


def pdf(tag: str) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
        b"<< /Producer (synthetic response " + tag.encode() + b") >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
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
    user = f"tax045-{name}-{RUN}@example.test"
    frappe.get_doc(
        {
            "doctype": "User",
            "email": user,
            "first_name": f"Synthetic {name}",
            "send_welcome_email": 0,
        }  # fmt: skip
    ).insert(ignore_permissions=True)
    return user


class EvidenceResponseTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.t1 = ensure_tenant(uid(), "Synthetic response tenant one")
        cls.t2 = ensure_tenant(uid(), "Synthetic response tenant two")
        cls.tp_a = cls.taxpayer(cls.t1, "Synthetic Mountain Wholesale", "450001001")
        cls.tp_b = cls.taxpayer(cls.t1, "Synthetic Border Retail", "450001002")
        cls.tp_c = cls.taxpayer(cls.t2, "Synthetic Highland Imports", "450001001")
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

    def new_request(self, tenant: str, taxpayer: str, status: str = "recorded") -> str:
        """An authorised request as TAX-046 stores it, with internal marker values."""
        frappe.set_user("Administrator")
        doc = frappe.get_doc(
            {
                "doctype": REQUEST,
                "request_id": uid(),
                "tenant_id": tenant,
                "taxpayer_id": taxpayer,
                "case_id": uid(),
                "status": "recorded",
                "authorised_by": INTERNAL_OFFICER,
                "authoriser_role": "supervisor",
                "case_version": 3,
                "due_date": "2099-12-31",
                "created_at": frappe.utils.now_datetime(),
                "items": json.dumps(ITEMS),
                "finding_ids": json.dumps([INTERNAL_FINDING]),
                "message_to_taxpayer": "Please send the invoices behind your September claim.",
                "registration_key": "internal-" + uuid.uuid4().hex,
                "request_digest": "internal-digest",
            }
        ).insert(ignore_permissions=True)
        if status in ("sent", "withdrawn"):
            # Reload first: TAX-046 compares every stored column on save.
            stored = frappe.get_doc(REQUEST, doc.name)
            stored.status = status
            stored.save(ignore_permissions=True)
        return doc.name

    def as_user(self, name: str) -> None:
        frappe.set_user(self.users[name])
        clear_principal_cache()

    def upload(self, who: str, tenant: str) -> str:
        self.as_user(who)
        builder = EnvironBuilder(
            method="POST",
            headers={"X-Vati-Tenant-ID": tenant},
            data={"file": FileStorage(stream=io.BytesIO(pdf(uid())), filename="invoice.pdf")},
        )
        frappe.local.request = Request(builder.get_environ())
        return evidence_api.upload_evidence(document_type="invoice", idempotency_key=key())[
            "evidence_id"
        ]

    def respond(self, who: str, request_id: str, items, **overrides):
        self.as_user(who)
        frappe.local.request = Request(EnvironBuilder(method="POST").get_environ())
        args = {"request_id": request_id, "items": items, "message": "Synthetic reply.",
                "idempotency_key": key(), **overrides}  # fmt: skip
        return portal_api.respond_to_evidence_request(**args)

    def answers(self, evidence_ids):
        return [
            {"item_index": 0, "evidence_ids": evidence_ids},
            {"item_index": 1, "evidence_ids": [], "unavailable_reason": "Account closed."},
        ]

    def error(self) -> dict:
        return frappe.local.response["error"]

    # --- structured responses and approved statuses ------------------------------------

    def test_structured_response_moves_the_request_to_responded_and_is_audited(self):
        request_id = self.new_request(self.t1, self.tp_a)
        evidence = self.upload("taxpayer-a", self.t1)
        result = self.respond("taxpayer-a", request_id, self.answers([evidence]))

        self.assertEqual((result["mode"], result["replayed"]), ("live", False))
        data = result["data"]
        self.assertEqual(data["status"], "response_sent")
        self.assertEqual(data["response"]["items"][0]["documents"][0]["evidence_id"], evidence)
        self.assertEqual(data["response"]["items"][0]["documents"][0]["name"], "invoice.pdf")
        self.assertEqual(data["response"]["items"][1]["unavailable_reason"], "Account closed.")

        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.get_value(REQUEST, request_id, "status"), "responded")
        stored = frappe.get_doc(RESPONSE, {"request_id": request_id})
        self.assertEqual((stored.tenant_id, stored.taxpayer_id), (self.t1, self.tp_a))
        self.assertEqual(stored.responded_by, self.users["taxpayer-a"])
        self.assertEqual(json.loads(stored.items)[0]["evidence_ids"], [evidence])
        audit = frappe.get_all(
            AUDIT,
            filters={"subject_id": request_id, "action": "evidence_request.responded"},
            fields=["category", "actor", "actor_role", "taxpayer_id"],
        )
        self.assertEqual(len(audit), 1)
        self.assertEqual(
            (audit[0].category, audit[0].actor, audit[0].actor_role, audit[0].taxpayer_id),
            ("write", self.users["taxpayer-a"], "taxpayer", self.tp_a),
        )

    def test_only_taxpayer_facing_statuses_and_fields_are_shown(self):
        open_id = self.new_request(self.t1, self.tp_a)
        sent_id = self.new_request(self.t1, self.tp_a, "sent")
        gone_id = self.new_request(self.t1, self.tp_a, "withdrawn")
        self.as_user("taxpayer-a")
        listed = portal_api.get_evidence_requests()
        views = {v["request_id"]: v for v in listed["data"]}
        self.assertEqual(views[open_id]["status"], "awaiting_response")
        self.assertEqual(views[sent_id]["status"], "awaiting_response")
        self.assertEqual(views[gone_id]["status"], "no_longer_needed")
        detail = portal_api.get_evidence_request(open_id)
        for payload in (listed, detail):
            text = json.dumps(payload, default=str)
            for internal in (INTERNAL_OFFICER, INTERNAL_FINDING, "internal-digest", "supervisor",
                             "case_id", "finding_ids", "authorised_by", "case_version"):  # fmt: skip
                self.assertNotIn(internal, text)
        self.assertEqual(
            set(detail["data"]),
            {"request_id", "status", "due_date", "message_to_taxpayer", "items", "response"},
        )

    def test_taxpayer_has_no_direct_access_to_request_records(self):
        request_id = self.new_request(self.t1, self.tp_a)
        self.as_user("taxpayer-a")
        self.assertFalse(frappe.has_permission(REQUEST, "read", doc=request_id))
        with self.assertRaises(frappe.PermissionError):
            frappe.get_list(REQUEST, pluck="name")

    # --- duplicate retry -------------------------------------------------------------

    def test_retry_replays_and_a_changed_answer_conflicts(self):
        request_id = self.new_request(self.t1, self.tp_a)
        evidence = self.upload("taxpayer-a", self.t1)
        retry_key = key()
        first = self.respond("taxpayer-a", request_id, self.answers([evidence]),
                             idempotency_key=retry_key)  # fmt: skip
        again = self.respond("taxpayer-a", request_id, self.answers([evidence]),
                             idempotency_key=retry_key)  # fmt: skip
        self.assertTrue(again["replayed"])
        self.assertEqual(again["data"], first["data"])
        with self.assertRaises(portal_api.PortalRequestError):
            self.respond("taxpayer-a", request_id, self.answers([evidence]),
                         idempotency_key=retry_key, message="Changed.")  # fmt: skip
        self.assertEqual(self.error()["code"], "idempotency_conflict")
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.count(RESPONSE, {"request_id": request_id}), 1)

    def test_a_request_is_answered_once_and_withdrawn_requests_are_closed(self):
        request_id = self.new_request(self.t1, self.tp_a)
        self.respond("taxpayer-a", request_id, self.answers([self.upload("taxpayer-a", self.t1)]))
        frappe.local.response = frappe._dict()
        with self.assertRaises(portal_api.PortalRequestError):
            self.respond(
                "taxpayer-a", request_id, self.answers([self.upload("taxpayer-a", self.t1)])
            )
        self.assertEqual(self.error()["field_errors"][0]["code"], "already_responded")
        gone = self.new_request(self.t1, self.tp_a, "withdrawn")
        frappe.local.response = frappe._dict()
        with self.assertRaises(portal_api.PortalRequestError):
            self.respond("taxpayer-a", gone, self.answers([self.upload("taxpayer-a", self.t1)]))
        self.assertEqual(self.error()["field_errors"][0]["code"], "request_withdrawn")

    # --- attachments link to the right request ---------------------------------------

    def test_attachments_must_be_the_taxpayers_own_and_link_to_one_request(self):
        request_id = self.new_request(self.t1, self.tp_a)
        foreign = {
            "same tenant, other taxpayer": self.upload("taxpayer-b", self.t1),
            "other tenant": self.upload("taxpayer-c", self.t2),
            "unknown": uid(),
        }
        for label, evidence in foreign.items():
            with self.subTest(label):
                frappe.local.response = frappe._dict()
                with self.assertRaises(portal_api.PortalRequestError):
                    self.respond("taxpayer-a", request_id, self.answers([evidence]))
                self.assertEqual(
                    [(e["field"], e["code"]) for e in self.error()["field_errors"]],
                    [("evidence_ids", "not_found")],
                )
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.get_value(REQUEST, request_id, "status"), "recorded")

        # A document already used for one request cannot answer another.
        first = self.new_request(self.t1, self.tp_a)
        second = self.new_request(self.t1, self.tp_a)
        shared = self.upload("taxpayer-a", self.t1)
        self.respond("taxpayer-a", first, self.answers([shared]))
        frappe.local.response = frappe._dict()
        with self.assertRaises(portal_api.PortalRequestError):
            self.respond("taxpayer-a", second, self.answers([shared]))
        self.assertEqual(self.error()["field_errors"][0]["code"], "evidence_reused")

    def test_answers_must_match_the_requested_items(self):
        request_id = self.new_request(self.t1, self.tp_a)
        evidence = self.upload("taxpayer-a", self.t1)
        with self.assertRaises(portal_api.PortalRequestError):
            self.respond("taxpayer-a", request_id, [{"item_index": 0, "evidence_ids": [evidence]},
                                                    {"item_index": 5, "evidence_ids": []}])  # fmt: skip
        self.assertIn(
            ("items.1", "answer_required"),
            {(e["field"], e["code"]) for e in self.error()["field_errors"]},
        )

    def test_another_taxpayers_or_tenants_request_is_concealed(self):
        theirs = self.new_request(self.t1, self.tp_b)
        other_tenant = self.new_request(self.t2, self.tp_c)
        evidence = self.upload("taxpayer-a", self.t1)
        self.as_user("taxpayer-a")
        listed = {v["request_id"] for v in portal_api.get_evidence_requests()["data"]}
        for request_id in (theirs, other_tenant):
            with self.subTest(request_id=request_id):
                self.assertNotIn(request_id, listed)
                with self.assertRaises(frappe.DoesNotExistError):
                    portal_api.get_evidence_request(request_id)
                with self.assertRaises(frappe.DoesNotExistError):
                    self.respond("taxpayer-a", request_id, self.answers([evidence]))
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.get_value(REQUEST, theirs, "status"), "recorded")

    def test_staff_cannot_respond_and_responses_are_immutable(self):
        request_id = self.new_request(self.t1, self.tp_a)
        with self.assertRaises(frappe.PermissionError):
            self.respond("auditor-t1", request_id, self.answers([uid()]))
        self.respond("taxpayer-a", request_id, self.answers([self.upload("taxpayer-a", self.t1)]))
        frappe.set_user("Administrator")
        stored = frappe.get_doc(RESPONSE, {"request_id": request_id})
        stored.message = "Rewritten"
        with self.assertRaises(frappe.PermissionError):
            stored.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(RESPONSE, stored.name, ignore_permissions=True, force=True)

    # --- TAX-126: checks before sending --------------------------------------------------

    def read(self, evidence_id: str, texts: list[str], status: str = "success") -> None:
        """Record an extraction revision as the Docling adapter would (synthetic text)."""
        frappe.set_user("Administrator")
        evidence = frappe.db.get_value("Vati Evidence", evidence_id,
                                       ["tenant_id", "taxpayer_id", "document_sha256"],
                                       as_dict=True)  # fmt: skip
        frappe.get_doc(
            {
                "doctype": "Vati Evidence Extraction",
                "extraction_id": uid(),
                "tenant_id": evidence.tenant_id,
                "taxpayer_id": evidence.taxpayer_id,
                "evidence_id": evidence_id,
                "document_sha256": evidence.document_sha256,
                "revision": 1,
                "status": status,
                "error_code": None if status == "success" else "conversion_failed",
                "extractor": "docling",
                "extractor_version": "docling-serve-1.38.2",
                "page_count": 1,
                "item_count": len(texts),
                "recorded_at": frappe.utils.now_datetime(),
                "items": [{"item_index": i, "label": "text", "page": 1, "text": t}
                          for i, t in enumerate(texts)],
            }
        ).insert(ignore_permissions=True)  # fmt: skip

    def review(self, who: str, request_id: str, items):
        self.as_user(who)
        frappe.local.request = Request(EnvironBuilder(method="POST").get_environ())
        return portal_api.review_evidence_response(request_id=request_id, items=items)

    def test_review_gives_guidance_from_the_extraction_and_stores_nothing(self):
        request_id = self.new_request(self.t1, self.tp_a)
        read = self.upload("taxpayer-a", self.t1)
        unreadable = self.upload("taxpayer-a", self.t1)
        self.read(read, ["Synthetic statement", "Period 01/09/2026 to 30/09/2026"])
        self.read(unreadable, [], status="failure")
        responses = frappe.db.count(RESPONSE)
        result = self.review("taxpayer-a", request_id,
                             [{"item_index": 0, "evidence_ids": [read, unreadable]}])  # fmt: skip
        self.assertEqual(result["mode"], "live")
        checks = result["data"]["items"][0]["checks"]
        self.assertEqual([c["code"] for c in checks],
                         ["document_matches_request", "document_not_readable"])  # fmt: skip
        self.assertIs(result["data"]["blocks_sending"], False)
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.count(RESPONSE), responses)
        self.assertEqual(frappe.db.get_value(REQUEST, request_id, "status"), "recorded")
        # Guidance never stops the response being sent.
        sent = self.respond("taxpayer-a", request_id, self.answers([read, unreadable]))
        self.assertEqual(sent["data"]["status"], "response_sent")

    def test_an_unread_document_is_queued_for_one_read(self):
        from unittest import mock

        request_id = self.new_request(self.t1, self.tp_a)
        evidence = self.upload("taxpayer-a", self.t1)
        with mock.patch.object(frappe, "enqueue") as enqueue:
            result = self.review("taxpayer-a", request_id,
                                 [{"item_index": 0, "evidence_ids": [evidence]}])  # fmt: skip
        self.assertEqual(result["data"]["items"][0]["checks"][0]["code"],
                         "document_still_being_read")  # fmt: skip
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.kwargs["job_id"], f"vati-draft-extraction-{evidence}")
        self.assertTrue(enqueue.call_args.kwargs["deduplicate"])

    def test_review_is_scoped_like_sending(self):
        mine = self.new_request(self.t1, self.tp_a)
        theirs = self.new_request(self.t1, self.tp_b)
        their_doc = self.upload("taxpayer-b", self.t1)
        with self.assertRaises(frappe.DoesNotExistError):
            self.review("taxpayer-a", theirs, [{"item_index": 0, "evidence_ids": []}])
        with self.assertRaises(frappe.ValidationError):
            self.review("taxpayer-a", mine, [{"item_index": 0, "evidence_ids": [their_doc]}])
        self.assertEqual(self.error()["field_errors"][0]["code"], "not_found")
        closed = self.new_request(self.t1, self.tp_a, status="withdrawn")
        with self.assertRaises(frappe.ValidationError):
            self.review("taxpayer-a", closed, [])
