"""Behaviour tests for the authenticated, bounded Docling configuration (TAX-028)."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("docling_dev", ROOT / "scripts" / "docling_dev.py")
assert spec and spec.loader
docling_dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docling_dev)
COMPOSE = "\n".join(
    line
    for line in (ROOT / "docker-compose.docling.yml").read_text().splitlines()
    if not line.lstrip().startswith("#")
)
CONTRACT_MAX_BYTES = 26214400  # integration/api/openapi.json RegisterDocumentRequest.size_bytes


class ComposeSafetyTests(unittest.TestCase):
    def test_service_is_authenticated_loopback_only_and_pinned(self):
        self.assertIn("DOCLING_SERVE_API_KEY: ${DOCLING_API_KEY:?", COMPOSE)
        published = [line.strip() for line in COMPOSE.splitlines() if "ports:" in line]
        self.assertEqual(published, ['ports: ["127.0.0.1:55001:5001"]'])
        self.assertIn("docling-serve-cpu:v1.38.2@sha256:", COMPOSE)

    def test_documents_cannot_leave_the_container(self):
        for setting in [
            "DOCLING_SERVE_ENABLE_REMOTE_SERVICES",
            "DOCLING_SERVE_ALLOW_EXTERNAL_PLUGINS",
            "DOCLING_SERVE_ALLOW_CUSTOM_VLM_CONFIG",
            "DOCLING_SERVE_ALLOW_CUSTOM_PICTURE_DESCRIPTION_CONFIG",
            "DOCLING_SERVE_ALLOW_CUSTOM_CODE_FORMULA_CONFIG",
        ]:
            with self.subTest(setting=setting):
                self.assertIn(f'{setting}: "false"', COMPOSE)
        self.assertIn('HF_HUB_OFFLINE: "1"', COMPOSE)

    def test_bounds_match_the_vati_contract(self):
        self.assertIn(f'DOCLING_SERVE_MAX_FILE_SIZE: "{CONTRACT_MAX_BYTES}"', COMPOSE)
        self.assertEqual(docling_dev.MAX_FILE_SIZE, CONTRACT_MAX_BYTES)
        self.assertIn('DOCLING_SERVE_MAX_NUM_PAGES: "50"', COMPOSE)
        self.assertIn('DOCLING_SERVE_MAX_DOCUMENT_TIMEOUT: "300"', COMPOSE)


class ProvenanceParsingTests(unittest.TestCase):
    def test_text_is_grouped_by_page_number(self):
        doc = {
            "texts": [
                {"text": "Invoice SYNINV0042", "prov": [{"page_no": 1, "bbox": {}}]},
                {"text": "Invoice lines", "prov": [{"page_no": 2, "bbox": {}}]},
                {"text": "no provenance"},
            ]
        }
        pages = docling_dev.texts_by_page(doc)
        self.assertEqual(sorted(pages), [1, 2])
        self.assertIn("SYNINV0042", pages[1])
        self.assertNotIn("SYNINV0042", pages[2])


if __name__ == "__main__":
    unittest.main()
