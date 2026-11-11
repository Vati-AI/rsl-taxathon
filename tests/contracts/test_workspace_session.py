"""getWorkspaceSession in the fixture API (TAX-146): the app shell's server-side scope."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integration" / "api"))
sys.path.insert(0, str(ROOT / "scripts"))

import contract  # noqa: E402
import fixture_api  # noqa: E402

SPEC = contract.load_spec()
DATA = fixture_api.load_fixture()
TENANT = DATA["principal"]["tenant_id"]
OTHER_TENANT = "10000000-0000-4000-8000-000000000002"
CORRELATION = "c0000000-0000-4000-8000-0000000001a6"
PATH = "/api/v1/session"
EXAMPLES = ROOT / "integration" / "api" / "examples" / "http"


def call(tenant=TENANT, role=None):
    headers = {"Authorization": "Bearer synthetic", "X-Correlation-ID": CORRELATION}
    if tenant is not None:
        headers["X-Vati-Tenant-ID"] = tenant
    if role is not None:
        headers["X-Vati-Fixture-Role"] = role
    return fixture_api.handle("GET", PATH, headers, {}, DATA, SPEC)


def conforms(status, headers, body):
    example = {
        "name": "fixture workspace session",
        "operation_id": "getWorkspaceSession",
        "synthetic": True,
        "request": {
            "method": "GET",
            "path": PATH,
            "headers": {
                "Authorization": "Bearer synthetic",
                "X-Vati-Tenant-ID": TENANT,
                "X-Correlation-ID": CORRELATION,
            },  # fmt: skip
        },
        "response": {"status": status, "headers": headers, "body": body},
    }
    return contract.check_example(SPEC, example)


class WorkspaceSessionTests(unittest.TestCase):
    def test_an_auditor_sees_cases_and_unconnected_native_tools_but_no_reports(self):
        status, headers, body = call()
        self.assertEqual(status, 200)
        self.assertEqual(conforms(status, headers, body), [])
        data = body["data"]
        self.assertEqual(data["roles"], ["auditor"])
        self.assertTrue(data["synthetic"])
        apps = {a["key"]: a for a in data["apps"]}
        self.assertEqual(apps["cases"]["availability"], "available")
        for key in ("investigation", "explore"):
            self.assertEqual((apps[key]["availability"], apps[key]["reason"]),
                             ("unavailable", "not_connected"))  # fmt: skip
        self.assertNotIn("reports", apps)
        self.assertNotIn("admin", apps)
        self.assertEqual(data["views"], ["overview", "case_queue"])

    def test_a_supervisor_also_gets_reports_and_the_supervision_views(self):
        status, headers, body = call(role="supervisor")
        self.assertEqual(conforms(status, headers, body), [])
        data = body["data"]
        self.assertIn("reports", [a["key"] for a in data["apps"]])
        self.assertEqual(data["views"], ["overview", "case_queue", "management", "integrity"])
        self.assertEqual(data["officer_id"], "80000000-0000-4000-8000-000000000003")

    def test_another_tenant_or_an_unknown_role_is_refused(self):
        status, _, body = call(tenant=OTHER_TENANT)
        self.assertEqual((status, body["error"]["code"]), (403, "tenant_scope_denied"))
        status, _, body = call(tenant=None)
        self.assertEqual(status, 400)
        status, _, body = call(role="administrator")
        self.assertEqual((status, body["error"]["code"]), (400, "invalid_request"))

    def test_the_example_matches_the_fixture(self):
        example = json.loads((EXAMPLES / "48_get_workspace_session_supervisor.json").read_text())
        _, _, body = call(role="supervisor")
        self.assertEqual(body, example["response"]["body"])


if __name__ == "__main__":
    unittest.main()
