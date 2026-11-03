"""Behaviour tests for the synthetic-only yente developer stack (TAX-027)."""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("yente_dev", ROOT / "scripts" / "yente_dev.py")
assert spec and spec.loader
yente_dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(yente_dev)
DATA = ROOT / "integration" / "yente" / "data" / "vati_synthetic.ftm.json"
COMPOSE = "\n".join(
    line
    for line in (ROOT / "integration" / "yente" / "docker-compose.yente.yml")
    .read_text()
    .splitlines()
    if not line.lstrip().startswith("#")
)


class ManifestGuardTests(unittest.TestCase):
    def test_committed_manifest_is_accepted(self):
        yente_dev.check_manifest_is_local_only(yente_dev.MANIFEST.read_text())

    def test_external_or_licensed_sources_are_refused(self):
        for line in [
            '  - url: "https://data.opensanctions.org/datasets/latest/index.json"',
            "    entities_url: http://example.invalid/data.json",
            '    auth_token: "$OPENSANCTIONS_DELIVERY_TOKEN"',
        ]:
            with self.subTest(line=line), self.assertRaises(SystemExit):
                yente_dev.check_manifest_is_local_only("catalogs: []\ndatasets:\n" + line)

    def test_missing_empty_catalog_declaration_is_refused(self):
        with self.assertRaises(SystemExit):
            yente_dev.check_manifest_is_local_only("datasets: []")

    def test_comments_do_not_trigger_or_satisfy_the_guard(self):
        yente_dev.check_manifest_is_local_only("# no entities_url here\ncatalogs: []\n")
        with self.assertRaises(SystemExit):
            yente_dev.check_manifest_is_local_only("# catalogs: []\ndatasets: []\n")


class SyntheticDatasetTests(unittest.TestCase):
    def test_entities_are_synthetic_and_cover_the_smoke_expectations(self):
        entities = [json.loads(line) for line in DATA.read_text().splitlines() if line.strip()]
        ids = {e["id"] for e in entities}
        self.assertEqual(len(ids), len(entities))
        for entity in entities:
            with self.subTest(id=entity["id"]):
                self.assertTrue(entity["id"].startswith("vati-syn-"))
                self.assertIn("SYNTHETIC", " ".join(entity["properties"].get("notes", [])))
        self.assertIn(yente_dev.ALIAS_EXPECTED, ids)
        self.assertTrue(yente_dev.AMBIGUOUS_EXPECTED <= ids)
        alias_names = {
            n
            for e in entities
            if e["id"] == yente_dev.ALIAS_EXPECTED
            for n in e["properties"]["alias"]
        }
        self.assertIn(yente_dev.ALIAS_QUERY["properties"]["name"][0], alias_names)


class ComposeIsolationTests(unittest.TestCase):
    def test_only_yente_is_published_on_loopback_and_data_is_read_only(self):
        published = [line.strip() for line in COMPOSE.splitlines() if "ports:" in line]
        self.assertEqual(published, ['ports: ["127.0.0.1:${YENTE_PORT:-55800}:8000"]'])
        self.assertIn("./data:/data:ro", COMPOSE)
        self.assertNotIn("OPENSANCTIONS_DELIVERY_TOKEN", COMPOSE)
        self.assertNotIn("container_name", COMPOSE)
        self.assertEqual(COMPOSE.count("@sha256:"), 2)


if __name__ == "__main__":
    unittest.main()
