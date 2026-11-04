import contextlib
import importlib.util
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
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

    def test_review_operations_accept_json_and_reject_malformed_bodies(self):
        from urllib.request import Request

        case = "40000000-0000-4000-8000-000000000003"
        tenant = {"X-Vati-Tenant-ID": "10000000-0000-4000-8000-000000000001"}
        with urlopen(Request(f"{self.base}/api/v1/cases/{case}/review", headers=tenant)) as res:
            body = json.loads(res.read())
            self.assertEqual(res.headers["X-Vati-Mode"], "fixture")
        self.assertEqual(body["data"]["case_id"], case)
        headers = {**tenant, "Idempotency-Key": "tooling-test-malformed-1"}
        bad = Request(
            f"{self.base}/api/v1/cases/{case}/decisions", data=b"{not json", headers=headers
        )
        with self.assertRaises(HTTPError) as result:
            urlopen(bad)
        self.assertEqual(400, result.exception.code)
        self.assertEqual(json.loads(result.exception.read())["error"]["code"], "invalid_request")


class BuiltAuditorServingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        dist = Path(self.tmp.name) / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<div id=app>built</div>")
        (dist / "assets" / "index-abc123.js").write_text("console.log(1)")
        (dist / "assets" / "notes.txt").write_text("not an allowed type")
        (Path(self.tmp.name) / "secret.js").write_text("outside dist")
        patcher = mock.patch.object(dev, "AUDITOR_DIST", dist)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_client_routes_load_built_shell(self):
        for route in ["/auditor/", "/auditor/cases/SYN-001", "/auditor/showcase"]:
            self.assertEqual("index.html", dev.static_target(route).name)

    def test_only_flat_allowed_assets_are_served(self):
        self.assertEqual(
            "index-abc123.js", dev.static_target("/auditor/assets/index-abc123.js").name
        )
        for route in [
            "/auditor/assets/../../secret.js",
            "/auditor/assets/..",
            "/auditor/assets/notes.txt",
            "/auditor/assets/missing.js",
            "/auditor/assets/sub/index-abc123.js",
        ]:
            self.assertIsNone(dev.static_target(route), route)

    def test_starter_is_served_without_a_build(self):
        with mock.patch.object(dev, "AUDITOR_DIST", Path(self.tmp.name) / "absent"):
            self.assertEqual("starter.html", dev.static_target("/auditor/").name)
            self.assertIsNone(dev.static_target("/auditor/cases/SYN-001"))
        self.assertEqual("starter.css", dev.static_target("/starter.css").name)


class TaskFileFormatTests(unittest.TestCase):
    def test_saved_task_files_use_lf_line_endings_on_every_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "tasks").mkdir()
            with mock.patch.object(task, "ROOT", Path(tmp)):
                task.save({"id": "TAX-999", "notes": [], "status": "READY"})
            data = (Path(tmp) / "tasks" / "TAX-999.json").read_bytes()
        self.assertNotIn(b"\r\n", data)
        self.assertTrue(data.endswith(b"}\n"))


class TaskReadinessTests(unittest.TestCase):
    def test_ready_list_excludes_epics_and_unmet_dependencies(self):
        # Use a synthetic task set: real tasks change status when claimed, so
        # asserting on live task IDs breaks as soon as an agent starts work.
        from argparse import Namespace

        def leaf(status, depends_on=(), owner="Leseli", kind="task"):
            return {
                "kind": kind,
                "owner": owner,
                "status": status,
                "depends_on": list(depends_on),
                "title": "t",
            }

        fixtures = {
            "TAX-001": leaf("READY", kind="epic"),
            "TAX-002": leaf("READY"),
            "TAX-003": leaf("BACKLOG", ["TAX-004"]),
            "TAX-004": leaf("IN_REVIEW", owner="Jane"),
            "TAX-005": leaf("IN_PROGRESS"),
            "TAX-006": leaf("BACKLOG", ["TAX-007"]),
            "TAX-007": leaf("DONE", owner="Jane"),
            "TAX-008": leaf("READY", owner="Jane"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "tasks").mkdir()
            for key, value in fixtures.items():
                (Path(tmp) / "tasks" / f"{key}.json").write_text(json.dumps(value))
            output = io.StringIO()
            with mock.patch.object(task, "ROOT", Path(tmp)), contextlib.redirect_stdout(output):
                task.list_tasks(Namespace(owner="Leseli", ready=True))
        listed = {line.split(" | ")[0] for line in output.getvalue().splitlines()}
        self.assertEqual({"TAX-002", "TAX-006"}, listed)
