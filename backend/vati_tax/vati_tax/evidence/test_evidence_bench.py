"""Bench tests for evidence storage, extraction revisions and provenance (TAX-032).

Run on a disposable site (``scripts/frappe_dev.py verify`` does this)::

    bench --site SITE run-tests --module vati_tax.evidence.test_evidence_bench

Plain ``unittest`` without a site skips this module. Every user, tenant,
taxpayer and document is synthetic.
"""

import hashlib
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
    from vati_tax.evidence import rules, store
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import TenantScopeDenied, clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.ids import mapping
    from vati_tax.ids.canonical import CaseNotFound, IdempotencyConflict, InvalidMapping
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

EVIDENCE = store.EVIDENCE
EXTRACTION = store.EXTRACTION
PROBE = "Vati TAX032 Submission Probe"
RUN = uuid.uuid4().hex[:8]


def email(name: str) -> str:
    return f"tax032-{name}-{RUN}@example.test"


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


def pdf(tag: str) -> bytes:
    """A minimal valid one-page PDF; ``tag`` makes the bytes (and hash) unique.

    Frappe parses uploaded PDFs with pypdf, so the cross-reference table must be right.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
        b"<< /Producer (synthetic evidence " + tag.encode() + b") >>",
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


def key() -> str:
    return "synthetic-" + uuid.uuid4().hex


def reason(ctx) -> str:
    return ctx.exception.field_errors[0]["code"]


def create_probe_doctype() -> None:
    if frappe.db.exists("DocType", PROBE):
        return
    frappe.get_doc(
        {
            "doctype": "DocType",
            "name": PROBE,
            "module": "Vati Tax",
            "custom": 1,
            "autoname": "hash",
            "fields": [
                {"fieldname": "tenant_id", "fieldtype": "Data", "label": "Tenant"},
                {"fieldname": "taxpayer_id", "fieldtype": "Data", "label": "Taxpayer"},
            ],
            "permissions": [
                {"role": "System Manager", "read": 1, "create": 1, "write": 1, "delete": 1},
                {"role": "Vati Taxpayer", "read": 1, "create": 1},
                {"role": "Vati Auditor", "read": 1},
            ],
        }
    ).insert(ignore_permissions=True)
    frappe.db.commit()


def extraction(**overrides):
    body = {
        "status": "success",
        "extractor": "synthetic_fixture",
        "extractor_version": "fixture-1",
        "page_count": 2,
        "items": [
            {
                "label": "text",
                "element_ref": "#/texts/0",
                "page": 1,
                "bbox": {
                    "l": 72.0,
                    "t": 720.5,
                    "r": 310.25,
                    "b": 700.0,
                    "coord_origin": "BOTTOMLEFT",
                },
                "field": "invoice_number",
                "text": "SYNINV0042",
            },
            {
                "label": "table",
                "element_ref": "#/tables/0",
                "page": 2,
                "bbox": {
                    "l": 50.0,
                    "t": 600.0,
                    "r": 550.0,
                    "b": 400.0,
                    "coord_origin": "BOTTOMLEFT",
                },
                "text": "Total | 3450.00",
            },
        ],
    }
    body.update(overrides)
    return body


class EvidenceBenchTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        create_probe_doctype()
        cls.t1 = ensure_tenant(str(uuid.uuid4()), "Synthetic evidence tenant one")
        cls.t2 = ensure_tenant(str(uuid.uuid4()), "Synthetic evidence tenant two")
        cls.tp_a, cls.tp_b = str(uuid.uuid4()), str(uuid.uuid4())
        cls.users = {}
        grants: list[tuple[str, str, policy.Role, str | None]] = [
            ("taxpayer-a", cls.t1, policy.TAXPAYER, cls.tp_a),
            ("taxpayer-b", cls.t1, policy.TAXPAYER, cls.tp_b),
            ("taxpayer-a-t2", cls.t2, policy.TAXPAYER, cls.tp_a),
            ("auditor-t1", cls.t1, policy.AUDITOR, None),
            ("auditor-t2", cls.t2, policy.AUDITOR, None),
        ]
        for name, tenant, role, taxpayer in grants:
            cls.users[name] = make_user(name)
            grant_membership(cls.users[name], tenant, role, taxpayer)
        cls.users["service-t1"] = ensure_service_identity(email("service-t1"), [cls.t1])
        cls.users["service-t2"] = ensure_service_identity(email("service-t2"), [cls.t2])
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.db.rollback()
        if frappe.db.exists("DocType", PROBE):
            frappe.delete_doc("DocType", PROBE, force=True, ignore_permissions=True)
            frappe.db.commit()
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        clear_principal_cache()

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.request = None

    def as_user(self, name: str) -> str:
        frappe.set_user(self.users[name])
        return self.users[name]

    def upload(self, who="taxpayer-a", content=None, **overrides):
        self.as_user(who)
        args = dict(
            tenant_id=self.t1,
            content=content if content is not None else pdf(uuid.uuid4().hex),
            filename="synthetic-invoice.pdf",
            document_type="invoice",
            idempotency_key=key(),
        )
        args.update(overrides)
        return store.register_upload(**args)

    def count(self, doctype=EVIDENCE, **filters) -> int:
        return frappe.db.count(doctype, filters)

    # --- storage and hashing ---------------------------------------------------------

    def test_upload_is_private_hashed_server_side_and_contract_shaped(self):
        content = pdf("hash")
        view = self.upload(content=content)
        self.assertEqual(view["document_sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(
            (view["version"], view["source"], view["media_type"], view["size_bytes"]),
            (1, "taxpayer_portal", rules.PDF, len(content)),
        )
        self.assertEqual(view["taxpayer_id"], self.tp_a)
        self.assertEqual(
            view["extraction"], {"status": "pending", "extractor_version": None, "error_code": None}
        )
        self.assertNotIn("file_url", view)
        self.assertNotIn("/files/", repr(view))
        frappe.set_user("Administrator")
        doc = frappe.get_doc(EVIDENCE, view["evidence_id"])
        file_doc = frappe.get_doc("File", doc.file_ref)
        self.assertEqual(file_doc.is_private, 1)
        self.assertTrue(file_doc.file_url.startswith("/private/files/"))
        self.assertEqual(
            (file_doc.attached_to_doctype, file_doc.attached_to_name), (EVIDENCE, doc.name)
        )
        self.assertEqual(file_doc.file_name, f"{doc.name}.pdf")
        with open(file_doc.get_full_path(), "rb") as handle:
            self.assertEqual(handle.read(), content)
        self.assertEqual(doc.owner, self.users["taxpayer-a"])

    def test_oversized_and_disallowed_uploads_store_nothing(self):
        before = (self.count(), frappe.db.count("File"))
        too_big = pdf("big") + b"0" * rules.MAX_DOCUMENT_BYTES
        cases = [
            (too_big, "big.pdf", None, "too_large"),
            (b"", "empty.pdf", None, "empty_document"),
            (b"MZ\x90\x00synthetic", "tool.exe", None, "disallowed_type"),
            (b"<html>synthetic</html>", "page.html", None, "disallowed_type"),
            (pdf("x"), "renamed.png", None, "extension_mismatch"),
            (pdf("y"), "invoice.pdf", rules.PNG, "media_type_mismatch"),
            (b"%PDF-1.4\n% synthetic broken pdf\n", "broken.pdf", None, "malformed_document"),
        ]
        for content, name, media, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(rules.UploadRejected) as ctx:
                self.upload(content=content, filename=name, media_type=media)
            self.assertEqual(reason(ctx), expected)
        frappe.set_user("Administrator")
        self.assertEqual((self.count(), frappe.db.count("File")), before)

    def test_idempotent_retry_returns_the_same_evidence(self):
        k, content = key(), pdf("retry")
        first = self.upload(content=content, idempotency_key=k)
        again = self.upload(content=content, idempotency_key=k)
        self.assertEqual(again["evidence_id"], first["evidence_id"])
        self.assertTrue(again["replayed"])
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.count("File", {"attached_to_name": first["evidence_id"]}), 1)
        with self.assertRaises(IdempotencyConflict):
            self.upload(content=pdf("other"), idempotency_key=k)

    # --- tenant and taxpayer isolation ----------------------------------------------

    def test_taxpayer_cannot_upload_for_another_taxpayer_or_tenant_or_source(self):
        with self.assertRaises(frappe.PermissionError):
            self.upload(taxpayer_id=self.tp_b)
        with self.assertRaises(TenantScopeDenied):
            self.upload(tenant_id=self.t2)
        with self.assertRaises(rules.UploadRejected) as ctx:
            self.upload(source="officer_upload")
        self.assertEqual(reason(ctx), "source_not_allowed")
        with self.assertRaises(frappe.PermissionError):
            self.upload(who="auditor-t2", taxpayer_id=self.tp_a)

    def test_reads_are_isolated_by_tenant_and_taxpayer(self):
        own = self.upload()["evidence_id"]
        officer = self.upload(who="auditor-t1", taxpayer_id=self.tp_a)["evidence_id"]
        self.assertEqual(
            self.upload(who="auditor-t1", taxpayer_id=self.tp_a)["source"], "officer_upload"
        )

        self.as_user("taxpayer-a")
        self.assertTrue(frappe.has_permission(EVIDENCE, "read", doc=own))
        # Officer uploads about the taxpayer are not shown to the taxpayer.
        self.assertFalse(frappe.has_permission(EVIDENCE, "read", doc=officer))
        self.assertEqual(store.get_evidence(tenant_id=self.t1, evidence_id=own)["evidence_id"], own)
        with self.assertRaises(store.EvidenceNotFound):
            store.get_evidence(tenant_id=self.t1, evidence_id=officer)
        own_file = frappe.db.get_value(EVIDENCE, own, "file_ref")
        self.assertTrue(frappe.get_doc("File", own_file).has_permission("read"))

        self.as_user("taxpayer-b")
        self.assertFalse(frappe.has_permission(EVIDENCE, "read", doc=own))
        self.assertNotIn(own, frappe.get_list(EVIDENCE, pluck="name", limit_page_length=0))
        with self.assertRaises(store.EvidenceNotFound):
            store.get_evidence(tenant_id=self.t1, evidence_id=own)
        self.assertFalse(frappe.get_doc("File", own_file).has_permission("read"))

        # Same taxpayer UUID in another tenant reaches nothing in tenant one.
        self.as_user("taxpayer-a-t2")
        self.assertFalse(frappe.has_permission(EVIDENCE, "read", doc=own))
        with self.assertRaises(TenantScopeDenied):
            store.get_evidence(tenant_id=self.t1, evidence_id=own)
        with self.assertRaises(store.EvidenceNotFound):
            store.get_evidence(tenant_id=self.t2, evidence_id=own)

        self.as_user("auditor-t2")
        self.assertFalse(frappe.has_permission(EVIDENCE, "read", doc=own))
        self.assertNotIn(own, frappe.get_list(EVIDENCE, pluck="name", limit_page_length=0))
        with self.assertRaises(store.EvidenceNotFound):
            store.evidence_history(tenant_id=self.t2, evidence_id=own)

        self.as_user("auditor-t1")
        self.assertTrue(frappe.has_permission(EVIDENCE, "read", doc=own))
        self.assertTrue(frappe.has_permission(EVIDENCE, "read", doc=officer))
        self.assertEqual(store.verify_integrity(tenant_id=self.t1, evidence_id=own)["intact"], True)

        self.as_user("taxpayer-a")
        with self.assertRaises(frappe.PermissionError):
            store.evidence_history(tenant_id=self.t1, evidence_id=own)

    def test_case_and_reference_links_respect_tenant_and_taxpayer(self):
        frappe.set_user("Administrator")
        case_a = mapping.register_case(
            tenant_id=self.t1, idempotency_key=key(), taxpayer_id=self.tp_a
        )["case_id"]
        case_t2 = mapping.register_case(tenant_id=self.t2, idempotency_key=key())["case_id"]
        ref_b = (
            frappe.get_doc({"doctype": PROBE, "tenant_id": self.t1, "taxpayer_id": self.tp_b})
            .insert()
            .name
        )
        ref_a = (
            frappe.get_doc({"doctype": PROBE, "tenant_id": self.t1, "taxpayer_id": self.tp_a})
            .insert()
            .name
        )

        view = self.upload(who="auditor-t1", taxpayer_id=self.tp_a, case_id=case_a)
        self.assertEqual(view["case_id"], case_a)
        frappe.set_user("Administrator")
        links = mapping.case_service_links(tenant_id=self.t1, case_id=case_a)
        self.assertIn(
            {"service": "frappe", "external_type": EVIDENCE, "external_id": view["evidence_id"]},
            [{k: link[k] for k in ("service", "external_type", "external_id")} for link in links],
        )
        self.as_user("auditor-t1")
        self.assertEqual(
            [e["evidence_id"] for e in store.case_evidence(tenant_id=self.t1, case_id=case_a)],
            [view["evidence_id"]],
        )
        with self.assertRaises(CaseNotFound):
            self.upload(who="auditor-t1", taxpayer_id=self.tp_a, case_id=case_t2)
        with self.assertRaises(rules.EvidenceConflict) as ctx:
            self.upload(who="auditor-t1", taxpayer_id=self.tp_b, case_id=case_a)
        self.assertEqual(reason(ctx), "taxpayer_mismatch")

        with self.assertRaises(CaseNotFound):
            self.upload(reference_doctype=PROBE, reference_name=ref_b)
        with self.assertRaises(InvalidMapping):
            self.upload(reference_doctype="User", reference_name=self.users["taxpayer-a"])
        ok = self.upload(reference_doctype=PROBE, reference_name=ref_a)
        self.assertEqual(ok["reference"], {"doctype": PROBE, "name": ref_a})

    # --- append-only versions ---------------------------------------------------------

    def test_corrections_are_new_versions_and_originals_never_change(self):
        v1 = self.upload(content=pdf("v1"))
        original = frappe.db.get_value(EVIDENCE, v1["evidence_id"], ["document_sha256", "modified"])
        v2 = self.upload(content=pdf("v2"), supersedes_evidence_id=v1["evidence_id"])
        self.assertEqual((v2["version"], v2["supersedes_evidence_id"]), (2, v1["evidence_id"]))
        self.assertEqual(v2["lineage_id"], v1["evidence_id"])
        self.assertEqual(
            frappe.db.get_value(EVIDENCE, v1["evidence_id"], ["document_sha256", "modified"]),
            original,
        )
        with self.assertRaises(rules.EvidenceConflict) as ctx:
            self.upload(content=pdf("fork"), supersedes_evidence_id=v1["evidence_id"])
        self.assertEqual(reason(ctx), "already_superseded")
        with self.assertRaises(rules.EvidenceConflict) as ctx:
            self.upload(content=pdf("v2"), supersedes_evidence_id=v2["evidence_id"])
        self.assertEqual(reason(ctx), "unchanged_content")
        with self.assertRaises(store.EvidenceNotFound):
            self.upload(
                who="taxpayer-b", content=pdf("b"), supersedes_evidence_id=v2["evidence_id"]
            )
        self.as_user("auditor-t1")
        history = store.evidence_history(tenant_id=self.t1, evidence_id=v2["evidence_id"])
        self.assertEqual([h["version"] for h in history], [1, 2])

    def test_evidence_and_its_file_are_immutable(self):
        view = self.upload()
        frappe.set_user("Administrator")
        doc = frappe.get_doc(EVIDENCE, view["evidence_id"])
        doc.document_type = "contract"
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(EVIDENCE, doc.name, ignore_permissions=True)
        file_doc = frappe.get_doc("File", doc.file_ref)
        file_doc.is_private = 0
        with self.assertRaises(frappe.PermissionError):
            file_doc.save(ignore_permissions=True)
        self.as_user("taxpayer-a")
        file_doc = frappe.get_doc("File", doc.file_ref)
        self.assertFalse(file_doc.has_permission("write"))
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc("File", file_doc.name)
        self.as_user("auditor-t1")
        self.assertTrue(store.verify_integrity(tenant_id=self.t1, evidence_id=doc.name)["intact"])

    def test_register_existing_private_file_checks_claimed_hash(self):
        content = pdf("ref")
        self.as_user("taxpayer-a")
        uploaded = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": "synthetic-ref.pdf",
                "is_private": 1,
                "content": content,
            }
        ).insert()
        args = dict(
            tenant_id=self.t1,
            file_ref=uploaded.name,
            document_sha256=hashlib.sha256(content).hexdigest(),
            media_type=rules.PDF,
            size_bytes=len(content),
            document_type="invoice",
            idempotency_key=key(),
        )
        with self.assertRaises(rules.UploadRejected) as ctx:
            store.register_file(**{**args, "document_sha256": "0" * 64})
        self.assertEqual(reason(ctx), "hash_mismatch")
        self.as_user("taxpayer-b")
        with self.assertRaises(CaseNotFound):
            store.register_file(**args)
        self.as_user("taxpayer-a")
        view = store.register_file(**args)
        self.assertEqual(view["document_sha256"], args["document_sha256"])
        with self.assertRaises(rules.UploadRejected) as ctx:
            store.register_file(**{**args, "idempotency_key": key()})
        self.assertEqual(reason(ctx), "already_attached")
        frappe.set_user("Administrator")
        public = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": "synthetic-public.pdf",
                "is_private": 0,
                "content": pdf("pub"),
            }
        ).insert(ignore_permissions=True)
        self.as_user("auditor-t1")
        with self.assertRaises(rules.UploadRejected) as ctx:
            store.register_file(
                **{
                    **args,
                    "file_ref": public.name,
                    "taxpayer_id": self.tp_a,
                    "idempotency_key": key(),
                }
            )
        self.assertEqual(reason(ctx), "not_private")

    # --- extraction revisions and provenance -------------------------------------------

    def test_extraction_revisions_are_append_only_and_keep_the_original_hash(self):
        view = self.upload(content=pdf("extract"))
        evidence_id, sha = view["evidence_id"], view["document_sha256"]
        self.as_user("service-t1")
        first = store.record_extraction(
            tenant_id=self.t1,
            evidence_id=evidence_id,
            document_sha256=sha,
            idempotency_key=(k1 := key()),
            result=extraction(),
        )
        self.assertEqual((first["revision"], first["status"]), (1, "success"))
        cited = first["items"][0]
        self.assertEqual(
            cited["evidence_ref"],
            {
                "evidence_id": evidence_id,
                "document_sha256": sha,
                "locator": {"page": 1, "record": None},
            },
        )
        self.assertEqual(
            cited["bbox"],
            {"l": 72.0, "t": 720.5, "r": 310.25, "b": 700.0, "coord_origin": "BOTTOMLEFT"},
        )
        self.assertEqual(cited["field"], "invoice_number")
        self.assertEqual(first["items"][1]["evidence_ref"]["locator"]["page"], 2)

        partial = store.record_extraction(
            tenant_id=self.t1,
            evidence_id=evidence_id,
            document_sha256=sha,
            idempotency_key=key(),
            result=extraction(
                status="partial",
                error_code="docling.partial_success",
                extractor="docling",
                extractor_version="1.38.2",
            ),
        )
        failed = store.record_extraction(
            tenant_id=self.t1,
            evidence_id=evidence_id,
            document_sha256=sha,
            idempotency_key=key(),
            result=extraction(
                status="failure",
                error_code="docling.policy",
                error_category="policy",
                extractor="docling",
                extractor_version="1.38.2",
                items=[],
            ),
        )
        self.assertEqual([partial["revision"], failed["revision"]], [2, 3])
        self.assertEqual(failed["supersedes_extraction_id"], partial["extraction_id"])
        self.assertEqual((failed["items"], failed["error_code"]), ([], "docling.policy"))

        replay = store.record_extraction(
            tenant_id=self.t1,
            evidence_id=evidence_id,
            document_sha256=sha,
            idempotency_key=k1,
            result=extraction(),
        )
        self.assertEqual(
            (replay["extraction_id"], replay["replayed"]), (first["extraction_id"], True)
        )
        with self.assertRaises(IdempotencyConflict):
            store.record_extraction(
                tenant_id=self.t1,
                evidence_id=evidence_id,
                document_sha256=sha,
                idempotency_key=k1,
                result=extraction(page_count=3),
            )
        with self.assertRaises(rules.EvidenceConflict) as ctx:
            store.record_extraction(
                tenant_id=self.t1,
                evidence_id=evidence_id,
                document_sha256="f" * 64,
                idempotency_key=key(),
                result=extraction(),
            )
        self.assertEqual(reason(ctx), "hash_mismatch")

        self.as_user("auditor-t1")
        revisions = store.list_extractions(tenant_id=self.t1, evidence_id=evidence_id)
        self.assertEqual([r["revision"] for r in revisions], [1, 2, 3])
        self.assertEqual({r["document_sha256"] for r in revisions}, {sha})
        latest = store.get_evidence(tenant_id=self.t1, evidence_id=evidence_id)
        self.assertEqual(latest["document_sha256"], sha)
        self.assertEqual(
            latest["extraction"],
            {"status": "failed", "extractor_version": "1.38.2", "error_code": "docling.policy"},
        )
        self.assertTrue(
            store.verify_integrity(tenant_id=self.t1, evidence_id=evidence_id)["intact"]
        )
        self.assertEqual(revisions[0]["items"][0]["text"], "SYNINV0042")

        frappe.set_user("Administrator")
        doc = frappe.get_doc(EXTRACTION, first["extraction_id"])
        doc.status = "failure"
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(EXTRACTION, doc.name, ignore_permissions=True)

    def test_only_a_tenant_service_records_extractions_and_reads_are_scoped(self):
        view = self.upload()
        args = dict(
            evidence_id=view["evidence_id"],
            document_sha256=view["document_sha256"],
            result=extraction(),
        )
        for who in ("taxpayer-a", "auditor-t1"):
            self.as_user(who)
            with self.subTest(who=who), self.assertRaises(frappe.PermissionError):
                store.record_extraction(tenant_id=self.t1, idempotency_key=key(), **args)
        self.as_user("service-t2")
        with self.assertRaises(TenantScopeDenied):
            store.record_extraction(tenant_id=self.t1, idempotency_key=key(), **args)
        with self.assertRaises(store.EvidenceNotFound):
            store.record_extraction(tenant_id=self.t2, idempotency_key=key(), **args)
        self.as_user("service-t1")
        recorded = store.record_extraction(tenant_id=self.t1, idempotency_key=key(), **args)
        for who in ("taxpayer-a", "taxpayer-b", "auditor-t2"):
            self.as_user(who)
            with self.subTest(who=who):
                self.assertFalse(
                    frappe.has_permission(EXTRACTION, "read", doc=recorded["extraction_id"])
                )
        self.as_user("taxpayer-a")
        with self.assertRaises(frappe.PermissionError):
            store.list_extractions(tenant_id=self.t1, evidence_id=view["evidence_id"])
        self.as_user("auditor-t1")
        self.assertTrue(frappe.has_permission(EXTRACTION, "read", doc=recorded["extraction_id"]))

    # --- whitelisted upload ------------------------------------------------------------

    def http_upload(self, who: str, content: bytes, filename: str, tenant: str, **form):
        self.as_user(who)
        builder = EnvironBuilder(
            method="POST",
            headers={"X-Vati-Tenant-ID": tenant},
            data={"file": FileStorage(stream=io.BytesIO(content), filename=filename)},
        )
        frappe.local.request = Request(builder.get_environ())
        return evidence_api.upload_evidence(document_type="invoice", idempotency_key=key(), **form)

    def test_whitelisted_upload_uses_the_tenant_header_and_rejects_bad_files(self):
        ok = self.http_upload("taxpayer-a", pdf("http"), "synthetic.pdf", self.t1)
        self.assertEqual(ok["taxpayer_id"], self.tp_a)
        with self.assertRaises(evidence_api.EvidenceRequestError):
            self.http_upload(
                "taxpayer-a", pdf("big") + b"0" * rules.MAX_DOCUMENT_BYTES, "big.pdf", self.t1
            )
        with self.assertRaises(evidence_api.EvidenceRequestError):
            self.http_upload("taxpayer-a", b"MZsynthetic", "tool.exe", self.t1)
        with self.assertRaises(TenantScopeDenied):
            self.http_upload("taxpayer-a", pdf("t2"), "synthetic.pdf", self.t2)
        with self.assertRaises(frappe.PermissionError):
            self.http_upload(
                "taxpayer-b", pdf("b"), "synthetic.pdf", self.t1, taxpayer_id=self.tp_a
            )
