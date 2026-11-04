"""Behaviour tests for the contract-shaped fixture API served by scripts/dev.py (TAX-040)."""

import copy
import sys
import tempfile
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
OFFICER = DATA["principal"]["officer_id"]
CORRELATION = "c0000000-0000-4000-8000-0000000000aa"


def call(path, query=None, tenant=TENANT, correlation=CORRELATION):
    headers = {"Authorization": "Bearer synthetic"}
    if tenant is not None:
        headers["X-Vati-Tenant-ID"] = tenant
    if correlation is not None:
        headers["X-Correlation-ID"] = correlation
    return fixture_api.handle("GET", path, headers, query or {}, DATA, SPEC)


def conforms(path, query, status, headers, body, operation):
    """Validate a fixture response as if it were a contract example."""
    example = {
        "name": f"fixture {path}",
        "operation_id": operation,
        "synthetic": True,
        "request": {
            "method": "GET",
            "path": path,
            "query": query,
            "headers": {"X-Vati-Tenant-ID": TENANT, "X-Correlation-ID": CORRELATION},
        },
        "response": {"status": status, "headers": headers, "body": body},
    }
    return contract.check_example(SPEC, example)


class FixtureDataTests(unittest.TestCase):
    def test_every_case_matches_the_case_schema(self):
        schema = {"$ref": "#/components/schemas/Case"}
        for case in DATA["cases"]:
            with self.subTest(case=case["display_id"]):
                self.assertEqual(contract.validate(case, schema, SPEC), [])
                self.assertTrue(case["synthetic"])
                self.assertEqual(case["taxpayer"]["taxpayer_id"], case["taxpayer_id"])

    def test_embedded_taxpayer_summary_matches_profile(self):
        profiles = {t["taxpayer_id"]: t for t in DATA["taxpayers"]}
        for case in DATA["cases"]:
            profile = profiles[case["taxpayer_id"]]
            self.assertEqual(case["taxpayer"]["display_name"], profile["display_name"])
            self.assertEqual(case["taxpayer"]["taxpayer_type"], profile["taxpayer_type"])

    def test_unsynthetic_fixture_file_is_refused(self):
        path = ROOT / "integration" / "api" / "fixtures" / "auditor_queue.json"
        text = path.read_text(encoding="utf-8").replace(
            '"synthetic": true,\n  "principal"', '"synthetic": false,\n  "principal"', 1
        )
        with tempfile.TemporaryDirectory() as folder:
            tmp = Path(folder) / "fixture.json"
            tmp.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                fixture_api.load_fixture(tmp)


class ListCasesTests(unittest.TestCase):
    def test_assigned_queue_contains_only_the_officers_cases(self):
        status, headers, body = call("/api/v1/cases", {"assigned_to": "me"})
        self.assertEqual(status, 200)
        ids = [c["display_id"] for c in body["data"]]
        self.assertEqual(ids, [f"SYN-00{i}" for i in range(1, 8)])
        self.assertTrue(all(c["assigned_officer_id"] == OFFICER for c in body["data"]))
        self.assertEqual(headers["X-Vati-Mode"], "fixture")
        self.assertEqual(
            conforms("/api/v1/cases", {"assigned_to": "me"}, status, headers, body, "listCases"), []
        )

    def test_unassigned_and_filters_narrow_the_queue(self):
        _, _, body = call("/api/v1/cases", {"assigned_to": "unassigned"})
        self.assertEqual([c["display_id"] for c in body["data"]], ["SYN-009"])
        _, _, body = call("/api/v1/cases", {"assigned_to": "me", "status": "AWAITING_HUMAN"})
        self.assertEqual({c["status"] for c in body["data"]}, {"AWAITING_HUMAN"})
        _, _, body = call("/api/v1/cases", {"refund_lane": "targeted"})
        self.assertEqual({c["refund_lane"] for c in body["data"]}, {"targeted"})

    def test_no_match_is_an_empty_page_not_an_error(self):
        query = {"assigned_to": "me", "status": "RESOLVED"}
        status, headers, body = call("/api/v1/cases", query)
        self.assertEqual(
            (status, body), (200, {"data": [], "meta": {"next_cursor": None, "has_more": False}})
        )
        self.assertEqual(conforms("/api/v1/cases", query, status, headers, body, "listCases"), [])

    def test_cursor_pages_cover_every_case_once(self):
        seen, cursor = [], None
        for _ in range(10):
            query = {"limit": "2", **({"cursor": cursor} if cursor else {})}
            status, headers, body = call("/api/v1/cases", query)
            self.assertEqual(
                conforms("/api/v1/cases", query, status, headers, body, "listCases"), []
            )
            seen += [c["case_id"] for c in body["data"]]
            cursor = body["meta"]["next_cursor"]
            if not body["meta"]["has_more"]:
                break
        self.assertEqual(sorted(seen), sorted(c["case_id"] for c in DATA["cases"]))

    def test_invalid_query_values_are_rejected(self):
        for query in [
            {"status": "BOGUS"},
            {"assigned_to": "everyone"},
            {"limit": "0"},
            {"cursor": "abc"},
            {"cursor": "999"},
        ]:
            with self.subTest(query=query):
                status, _, body = call("/api/v1/cases", query)
                self.assertEqual((status, body["error"]["code"]), (400, "invalid_request"))


class AccessTests(unittest.TestCase):
    def test_missing_or_malformed_tenant_is_400(self):
        for tenant in [None, "acme"]:
            with self.subTest(tenant=tenant):
                status, _, body = call("/api/v1/cases", tenant=tenant)
                self.assertEqual((status, body["error"]["code"]), (400, "missing_tenant_context"))

    def test_other_tenant_is_denied_without_data(self):
        for path in [
            "/api/v1/cases",
            "/api/v1/cases/40000000-0000-4000-8000-000000000001",
            "/api/v1/taxpayers/20000000-0000-4000-8000-000000000001",
        ]:
            with self.subTest(path=path):
                status, headers, body = call(path, tenant=OTHER_TENANT)
                self.assertEqual((status, body["error"]["code"]), (403, "tenant_scope_denied"))
                self.assertNotIn("data", body)
                self.assertEqual(body["error"]["correlation_id"], headers["X-Correlation-ID"])

    def test_error_bodies_follow_the_contract(self):
        status, headers, body = call("/api/v1/cases", tenant=OTHER_TENANT)
        schema = {"$ref": "#/components/schemas/ErrorResponse"}
        self.assertEqual(contract.validate(body, schema, SPEC), [])
        self.assertEqual(contract.ERROR_STATUS[body["error"]["code"]], str(status))


class ReadTests(unittest.TestCase):
    def test_get_case_and_unknown_or_malformed_ids(self):
        path = "/api/v1/cases/40000000-0000-4000-8000-000000000006"
        status, headers, body = call(path)
        self.assertEqual(body["data"]["status"], "FAILED_PROCESSING")
        self.assertEqual(conforms(path, {}, status, headers, body, "getCase"), [])
        status, _, body = call("/api/v1/cases/40000000-0000-4000-8000-000000000099")
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))
        status, _, body = call("/api/v1/cases/SYN-001")
        self.assertEqual((status, body["error"]["code"]), (400, "invalid_case_id"))

    def test_taxpayer_profile_lists_only_open_visible_cases(self):
        path = "/api/v1/taxpayers/20000000-0000-4000-8000-000000000002"
        status, headers, body = call(path)
        self.assertEqual(conforms(path, {}, status, headers, body, "getTaxpayer"), [])
        self.assertEqual(
            [c["display_id"] for c in body["data"]["open_cases"]], ["SYN-002", "SYN-008"]
        )
        self.assertTrue(body["data"]["tin_masked"].startswith("****"))

    def test_taxpayer_without_visible_cases_is_not_disclosed(self):
        data = copy.deepcopy(DATA)
        data["cases"] = [
            c for c in data["cases"] if c["taxpayer_id"] != "20000000-0000-4000-8000-000000000001"
        ]
        headers = {"X-Vati-Tenant-ID": TENANT}
        status, _, body = fixture_api.handle(
            "GET", "/api/v1/taxpayers/20000000-0000-4000-8000-000000000001", headers, {}, data, SPEC
        )
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))
        status, _, body = call("/api/v1/taxpayers/not-a-uuid")
        self.assertEqual((status, body["error"]["code"]), (400, "invalid_request"))

    def test_operations_without_fixture_data_are_not_invented(self):
        status, headers, body = call("/api/v1/cases/40000000-0000-4000-8000-000000000002/evidence")
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))
        self.assertEqual(body["error"]["message"], "This operation has no fixture data.")
        self.assertEqual(headers["X-Vati-Mode"], "fixture")

    def test_correlation_id_is_echoed_or_generated(self):
        _, headers, _ = call("/api/v1/cases")
        self.assertEqual(headers["X-Correlation-ID"], CORRELATION)
        _, headers, _ = call("/api/v1/cases", correlation=None)
        self.assertRegex(headers["X-Correlation-ID"], contract.UUID_RE)


if __name__ == "__main__":
    unittest.main()
