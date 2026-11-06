import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("validate", ROOT / "scripts/validate.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for folder in ["tasks", "config", "data"]:
            shutil.copytree(ROOT / folder, self.root / folder)

    def tearDown(self):
        self.tmp.cleanup()

    def change(self, **values):
        p = self.root / "tasks/TAX-001.json"
        t = json.loads(p.read_text())
        t.update(values)
        p.write_text(json.dumps(t))

    def test_valid(self):
        self.assertEqual([], module.validate(self.root))

    def test_cycle(self):
        self.change(depends_on=["TAX-002"])
        self.assertTrue(any("cyclic" in e for e in module.validate(self.root)))

    def test_false_completion(self):
        self.change(status="DONE", pr=None, evidence=[])
        self.assertTrue(any("DONE needs" in e for e in module.validate(self.root)))

    def test_unknown_owner(self):
        self.change(owner="unverified-person")
        self.assertTrue(any("unknown owner" in e for e in module.validate(self.root)))

    def test_unknown_dependency(self):
        self.change(depends_on=["TAX-999"])
        self.assertTrue(any("unknown dependency" in e for e in module.validate(self.root)))

    def test_unmarked_synthetic_fixture(self):
        path = self.root / "data/synthetic/tax102_taxpayer_assistant_scenarios.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["synthetic"] = False
        path.write_text(json.dumps(doc), encoding="utf-8")
        errors = module.validate(self.root)
        self.assertIn(
            "tax102_taxpayer_assistant_scenarios.json: Only synthetic fixtures allowed", errors
        )

    def test_unmarked_synthetic_record(self):
        path = self.root / "data/synthetic/tax024_records.json"
        records = json.loads(path.read_text(encoding="utf-8"))
        del records[0]["synthetic"]
        path.write_text(json.dumps(records), encoding="utf-8")
        errors = module.validate(self.root)
        self.assertIn("tax024_records.json: Only synthetic fixtures allowed", errors)
