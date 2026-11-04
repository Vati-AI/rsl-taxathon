"""Frappe-free tests for evidence, extraction and provenance rules (TAX-032)."""

from __future__ import annotations

import hashlib
import io
import unittest
import uuid
import zipfile
from typing import Any

from vati_tax.evidence import rules
from vati_tax.evidence.rules import UploadRejected
from vati_tax.ids.canonical import InvalidMapping

PDF = b"%PDF-1.7\n% synthetic evidence\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
CSV = "invoice,amount\nSYN-1,100.00\n".encode()


def xlsx(with_workbook: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        if with_workbook:
            archive.writestr("xl/workbook.xml", "<workbook/>")
    return buffer.getvalue()


def reason(ctx) -> str:
    return ctx.exception.field_errors[0]["code"]


class DocumentCheckTests(unittest.TestCase):
    def test_accepts_each_contract_media_type_and_hashes_server_side(self):
        cases = [
            (PDF, "invoice.pdf", rules.PDF),
            (PNG, "scan.PNG", rules.PNG),
            (JPEG, "scan.jpeg", rules.JPEG),
            (CSV, "ledger.csv", rules.CSV),
            (xlsx(), "ledger.xlsx", rules.XLSX),
        ]
        for content, name, media in cases:
            with self.subTest(name=name):
                checked = rules.check_document(content, name, media)
                self.assertEqual(checked.media_type, media)
                self.assertEqual(checked.size_bytes, len(content))
                self.assertEqual(checked.document_sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(set(rules.MEDIA_TYPES), {m for _, _, m in cases})

    def test_size_limit_matches_contract(self):
        self.assertEqual(rules.MAX_DOCUMENT_BYTES, 25 * 1024 * 1024)
        at_limit = PDF + b"0" * (rules.MAX_DOCUMENT_BYTES - len(PDF))
        self.assertEqual(
            rules.check_document(at_limit, "a.pdf", None).size_bytes, rules.MAX_DOCUMENT_BYTES
        )
        with self.assertRaises(UploadRejected) as ctx:
            rules.check_document(at_limit + b"0", "a.pdf", None)
        self.assertEqual(reason(ctx), "too_large")
        self.assertEqual(ctx.exception.code, "invalid_domain_input")
        with self.assertRaises(UploadRejected) as ctx:
            rules.check_document(b"", "a.pdf", None)
        self.assertEqual(reason(ctx), "empty_document")

    def test_disallowed_types_are_rejected(self):
        for content, name in [
            (b"MZ\x90\x00binary", "tool.exe"),
            (b"<html><script>alert(1)</script>", "page.html"),
            (b"GIF89a....", "image.gif"),
            (xlsx(with_workbook=False), "archive.xlsx"),
            (b"PK\x03\x04not a zip", "ledger.xlsx"),
            (b"a,b\x00c", "ledger.csv"),
        ]:
            with self.subTest(name=name), self.assertRaises(UploadRejected) as ctx:
                rules.check_document(content, name, None)
            self.assertIn(reason(ctx), {"disallowed_type", "extension_mismatch"})
        with self.assertRaises(UploadRejected) as ctx:
            rules.check_document(PDF, "a.pdf", "application/zip")
        self.assertEqual(reason(ctx), "disallowed_type")

    def test_declared_type_extension_and_bytes_must_agree(self):
        with self.assertRaises(UploadRejected) as ctx:
            rules.check_document(PDF, "invoice.png", None)
        self.assertEqual(reason(ctx), "extension_mismatch")
        with self.assertRaises(UploadRejected) as ctx:
            rules.check_document(PDF, "invoice.pdf", rules.PNG)
        self.assertEqual(reason(ctx), "media_type_mismatch")
        with self.assertRaises(UploadRejected) as ctx:
            rules.check_document(PDF, "invoice", None)
        self.assertEqual(reason(ctx), "disallowed_type")

    def test_filename_keeps_only_the_last_component(self):
        self.assertEqual(rules.safe_filename("../../etc/x.pdf"), ("x.pdf", "pdf"))
        self.assertEqual(rules.safe_filename("C:\\docs\\Y.PDF"), ("Y.PDF", "pdf"))
        with self.assertRaises(UploadRejected):
            rules.safe_filename("bad\nname.pdf")
        long_name, ext = rules.safe_filename("a" * 400 + ".pdf")
        self.assertEqual((len(long_name), ext), (rules.FILENAME_MAX, "pdf"))


class RegistrationTests(unittest.TestCase):
    def build(self, **overrides):
        args = dict(
            tenant_id=str(uuid.uuid4()).upper(),
            taxpayer_id=str(uuid.uuid4()),
            idempotency_key="synthetic-" + uuid.uuid4().hex,
            document_type="invoice",
            source="taxpayer_portal",
        )
        args.update(overrides)
        return rules.Registration.build(**args)

    def test_normalises_ids_and_validates_enums(self):
        reg = self.build()
        self.assertEqual(reg.tenant_id, reg.tenant_id.lower())
        for field, value in [
            ("document_type", "selfie"),
            ("source", "email"),
            ("taxpayer_id", "not-a-uuid"),
            ("idempotency_key", "short"),
            ("case_id", "nope"),
        ]:
            with self.subTest(field=field), self.assertRaises(InvalidMapping):
                self.build(**{field: value})
        with self.assertRaises(InvalidMapping):
            self.build(reference_doctype="Vati Submission")

    def test_digest_changes_with_any_identity_field(self):
        reg = self.build()
        base = reg.digest("a" * 64, rules.PDF)
        self.assertNotEqual(base, reg.digest("b" * 64, rules.PDF))
        self.assertNotEqual(
            base,
            self.build(
                tenant_id=reg.tenant_id,
                idempotency_key=reg.idempotency_key,
                taxpayer_id=reg.taxpayer_id,
                document_type="contract",
            ).digest("a" * 64, rules.PDF),
        )
        self.assertEqual(
            reg.registration_key, rules.key(reg.tenant_id, "evidence", reg.idempotency_key)
        )

    def test_sources_are_bound_to_roles(self):
        rules.check_source("taxpayer_portal", {"taxpayer"})
        rules.check_source("officer_upload", {"auditor"})
        rules.check_source("synthetic_fixture", {"service"})
        for source, roles in [
            ("taxpayer_portal", {"auditor"}),
            ("taxpayer_portal", {"service"}),
            ("officer_upload", {"taxpayer"}),
            ("officer_upload", {"service"}),
            ("synthetic_fixture", {"taxpayer"}),
        ]:
            with self.subTest(source=source, roles=roles), self.assertRaises(UploadRejected):
                rules.check_source(source, roles)
        self.assertEqual(rules.default_source({"taxpayer"}), "taxpayer_portal")
        self.assertEqual(rules.default_source({"supervisor"}), "officer_upload")


def item(**overrides):
    base = {
        "label": "text",
        "element_ref": "#/texts/0",
        "page": 1,
        "bbox": {"l": 72.5, "t": 700.25, "r": 300.0, "b": 680.0, "coord_origin": "BOTTOMLEFT"},
        "text": "Invoice SYNINV0042",
    }
    base.update(overrides)
    return base


def payload(**overrides):
    base = {
        "status": "success",
        "extractor": "docling",
        "extractor_version": "1.38.2",
        "page_count": 2,
        "items": [item(), item(label="table", element_ref="#/tables/0", page=2, text=None)],
    }
    base.update(overrides)
    return base


class ExtractionTests(unittest.TestCase):
    def test_success_with_page_and_bbox_provenance(self):
        parsed = rules.parse_extraction(payload())
        self.assertEqual(parsed.status, "success")
        first = parsed.items[0]
        self.assertEqual(first.locator(), {"page": 1, "record": None})
        self.assertEqual(first.bbox, rules.BBox(72.5, 700.25, 300.0, 680.0, "BOTTOMLEFT"))
        self.assertEqual(first.text_sha256, hashlib.sha256(b"Invoice SYNINV0042").hexdigest())
        ref = rules.evidence_ref("e" * 8, "a" * 64, first)
        self.assertEqual(
            ref,
            {
                "evidence_id": "e" * 8,
                "document_sha256": "a" * 64,
                "locator": {"page": 1, "record": None},
            },
        )

    def test_failure_is_never_an_empty_success(self):
        failed = rules.parse_extraction(
            payload(
                status="failure", error_code="docling.policy", error_category="policy", items=[]
            )
        )
        self.assertEqual(
            rules.contract_extraction(
                {
                    "status": failed.status,
                    "error_code": failed.error_code,
                    "extractor_version": "1.38.2",
                }
            ),
            {"status": "failed", "extractor_version": "1.38.2", "error_code": "docling.policy"},
        )
        with self.assertRaises(InvalidMapping) as ctx:
            rules.parse_extraction(payload(status="failure", items=[]))
        self.assertEqual(reason(ctx), "status_mismatch")
        with self.assertRaises(InvalidMapping) as ctx:
            rules.parse_extraction(payload(status="failure", error_code="docling.timeout"))
        self.assertEqual(reason(ctx), "status_mismatch")
        with self.assertRaises(InvalidMapping):
            rules.parse_extraction(payload(error_code="docling.timeout"))

    def test_partial_keeps_items_and_an_error_code(self):
        parsed = rules.parse_extraction(
            payload(status="partial", error_code="docling.partial_success")
        )
        self.assertEqual(len(parsed.items), 2)
        self.assertEqual(
            rules.contract_extraction(
                {"status": "partial", "error_code": None, "extractor_version": "x"}
            ),
            {"status": "completed", "extractor_version": "x", "error_code": "partial_extraction"},
        )
        self.assertEqual(rules.contract_extraction(None)["status"], "pending")

    def test_locator_and_bbox_validation(self):
        bad = [
            item(page=0),
            item(page=3),
            item(page=None, bbox=None, record=None),
            item(page=None),
            item(bbox={"l": 10, "t": 5, "r": 5, "b": 1}),
            item(bbox={"l": 1, "t": 1, "r": 5, "b": 9}),
            item(bbox={"l": 1, "t": 9, "r": 5, "b": 1, "coord_origin": "TOPLEFT"}),
            item(bbox={"l": float("nan"), "t": 9, "r": 5, "b": 1}),
            item(bbox={"l": -1, "t": 9, "r": 5, "b": 1}),
            item(page=True),
            item(label="Text!"),
            item(text="x" * (rules.MAX_ITEM_TEXT + 1)),
        ]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(InvalidMapping):
                rules.parse_extraction(payload(items=[raw]))
        record_only = rules.parse_extraction(
            payload(items=[item(page=None, bbox=None, record="Sheet1!A2:C9", label="table")])
        )
        self.assertEqual(record_only.items[0].locator(), {"page": None, "record": "Sheet1!A2:C9"})
        top_left = item(bbox={"l": 1, "t": 1, "r": 5, "b": 9, "coord_origin": "TOPLEFT"})
        box = rules.parse_extraction(payload(items=[top_left])).items[0].bbox
        self.assertEqual(box.coord_origin if box else None, "TOPLEFT")

    def test_digest_is_stable_and_sensitive(self):
        a = rules.parse_extraction(payload()).digest("e1", "a" * 64)
        self.assertEqual(a, rules.parse_extraction(payload()).digest("e1", "a" * 64))
        self.assertNotEqual(a, rules.parse_extraction(payload()).digest("e1", "b" * 64))
        moved = payload(items=[item(page=2)])
        self.assertNotEqual(a, rules.parse_extraction(moved).digest("e1", "a" * 64))

    def test_header_fields(self):
        cases: list[dict[str, Any]] = [
            {"status": "done"},
            {"extractor": "ocr"},
            {"extractor_version": ""},
            {"page_count": -1},
            {"items": "nope"},
            {"items": [item()] * (rules.MAX_ITEMS + 1)},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(InvalidMapping):
                rules.parse_extraction(payload(**overrides))


if __name__ == "__main__":
    unittest.main()
