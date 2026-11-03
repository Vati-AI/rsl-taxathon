import contextlib
import importlib.util
import io
import json
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dev = load("dev")
task = load("task")


class FixtureServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = dev.ThreadingHTTPServer(("127.0.0.1", 0), dev.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_five_explicitly_synthetic_scenarios(self):
        with urlopen(self.base + "/api/scenarios") as response:
            payload = json.load(response)
        self.assertEqual("fixture", payload["mode"])
        self.assertTrue(payload["synthetic"])
        self.assertEqual(5, len(payload["items"]))
        self.assertIn("policy_gap_review", [x["expected"] for x in payload["items"]])

    def test_private_files_are_not_served(self):
        for route in ["/.env", "/../.env", "/scripts/task.py", "/external/jube/.git/config"]:
            with self.assertRaises(HTTPError) as result:
                urlopen(self.base + route)
            self.assertEqual(404, result.exception.code)

    def test_missing_case_is_404(self):
        with self.assertRaises(HTTPError) as result:
            urlopen(self.base + "/api/cases/missing")
        self.assertEqual(404, result.exception.code)

    def test_mutations_are_not_available(self):
        from urllib.request import Request

        with self.assertRaises(HTTPError) as result:
            urlopen(Request(self.base + "/api/cases", data=b"{}", method="POST"))
        self.assertEqual(501, result.exception.code)


class TaskReadinessTests(unittest.TestCase):
    def test_ready_list_excludes_epics_and_unmet_dependencies(self):
        from argparse import Namespace

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            task.list_tasks(Namespace(owner="Leseli", ready=True))
        self.assertIn("TAX-020", output.getvalue())
        self.assertNotIn("TAX-001", output.getvalue())
        self.assertNotIn("TAX-041", output.getvalue())
