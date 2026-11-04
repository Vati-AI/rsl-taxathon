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

    def test_case_without_fixture_records_is_not_invented(self):
        data = copy.deepcopy(DATA)
        del data["case_records"]["40000000-0000-4000-8000-000000000002"]
        headers = {"X-Vati-Tenant-ID": TENANT}
        status, out_headers, body = fixture_api.handle(
            "GET", f"/api/v1/cases/{CASE_2}/findings", headers, {}, data, SPEC
        )
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))
        self.assertNotIn("data", body)
        self.assertEqual(out_headers["X-Vati-Mode"], "fixture")

    def test_correlation_id_is_echoed_or_generated(self):
        _, headers, _ = call("/api/v1/cases")
        self.assertEqual(headers["X-Correlation-ID"], CORRELATION)
        _, headers, _ = call("/api/v1/cases", correlation=None)
        self.assertRegex(headers["X-Correlation-ID"], contract.UUID_RE)


CASE_2 = "40000000-0000-4000-8000-000000000002"
RECORD_OPERATIONS = [
    ("evidence", "listEvidence", "Evidence"),
    ("entities", "listEntities", "Entity"),
    ("findings", "listFindings", "Finding"),
]


class CaseRecordTests(unittest.TestCase):
    """listEvidence, listEntities and listFindings fixtures (TAX-041)."""

    def test_every_record_matches_its_schema_and_case(self):
        for case_id, records in DATA["case_records"].items():
            for key, _, schema_name in RECORD_OPERATIONS:
                schema = {"$ref": f"#/components/schemas/{schema_name}"}
                for item in records[key]:
                    with self.subTest(case=case_id, kind=key):
                        self.assertEqual(contract.validate(item, schema, SPEC), [])
                        self.assertEqual(item["case_id"], case_id)
                        self.assertEqual(item["tenant_id"], TENANT)

    def test_every_fixture_case_has_a_record_entry(self):
        self.assertEqual(sorted(DATA["case_records"]), sorted(c["case_id"] for c in DATA["cases"]))

    def test_finding_citations_resolve_to_stored_evidence_hashes(self):
        for case_id, records in DATA["case_records"].items():
            hashes = {e["evidence_id"]: e["document_sha256"] for e in records["evidence"]}
            for finding in records["findings"]:
                for ref in finding["evidence_refs"]:
                    with self.subTest(finding=finding["finding_id"]):
                        self.assertEqual(hashes.get(ref["evidence_id"]), ref["document_sha256"])
                        locator = ref["locator"]
                        self.assertTrue(locator["page"] or locator["record"])

    def test_ownership_and_payment_references_name_case_entities(self):
        for records in DATA["case_records"].values():
            ids = {e["entity_id"] for e in records["entities"]}
            for entity in records["entities"]:
                for prop in ("owner", "asset", "payer", "beneficiary"):
                    for value in entity["properties"].get(prop, []):
                        with self.subTest(entity=entity["entity_id"], prop=prop):
                            self.assertIn(value, ids)

    def test_disputed_ownership_case_has_the_records_the_view_needs(self):
        records = DATA["case_records"][CASE_2]
        kinds = {f["kind"] for f in records["findings"]}
        self.assertTrue({"contradictory_evidence", "missing_evidence"} <= kinds)
        statuses = {c["review_status"] for e in records["entities"] for c in e["match_candidates"]}
        self.assertEqual(statuses, {"unreviewed", "confirmed", "rejected"})
        on_file = {e["evidence_id"] for e in records["evidence"]}
        cited = {i for e in records["entities"] for i in e["evidence_ids"]}
        self.assertTrue(cited - on_file, "SYN-002 keeps one entity citing evidence not on file")
        superseded = [e for e in records["evidence"] if e["supersedes_evidence_id"]]
        self.assertEqual(len(superseded), 1)
        self.assertIn(superseded[0]["supersedes_evidence_id"], on_file)

    def test_list_operations_conform_and_page(self):
        for key, operation, _ in RECORD_OPERATIONS:
            path = f"/api/v1/cases/{CASE_2}/{key}"
            with self.subTest(operation=operation):
                status, headers, body = call(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["X-Vati-Mode"], "fixture")
                self.assertEqual(conforms(path, {}, status, headers, body, operation), [])
                self.assertEqual(body["data"], DATA["case_records"][CASE_2][key])
                seen, cursor = [], None
                for _ in range(20):
                    query = {"limit": "2", **({"cursor": cursor} if cursor else {})}
                    status, headers, body = call(path, query)
                    self.assertEqual(conforms(path, query, status, headers, body, operation), [])
                    seen += body["data"]
                    cursor = body["meta"]["next_cursor"]
                    if not body["meta"]["has_more"]:
                        break
                self.assertEqual(seen, DATA["case_records"][CASE_2][key])

    def test_case_without_records_is_an_empty_page(self):
        path = "/api/v1/cases/40000000-0000-4000-8000-000000000007/findings"
        status, _, body = call(path)
        self.assertEqual(
            (status, body), (200, {"data": [], "meta": {"next_cursor": None, "has_more": False}})
        )

    def test_unknown_case_other_tenant_and_bad_cursor_are_refused(self):
        status, _, body = call("/api/v1/cases/40000000-0000-4000-8000-000000000099/evidence")
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))
        status, _, body = call(f"/api/v1/cases/{CASE_2}/entities", tenant=OTHER_TENANT)
        self.assertEqual((status, body["error"]["code"]), (403, "tenant_scope_denied"))
        self.assertNotIn("data", body)
        status, _, body = call(f"/api/v1/cases/{CASE_2}/findings", {"cursor": "abc"})
        self.assertEqual((status, body["error"]["code"]), (400, "invalid_request"))
        status, _, body = call("/api/v1/cases/SYN-002/evidence")
        self.assertEqual((status, body["error"]["code"]), (400, "invalid_case_id"))

    def test_unsynthetic_records_file_is_refused(self):
        path = ROOT / "integration" / "api" / "fixtures" / "case_records.json"
        text = path.read_text(encoding="utf-8").replace(
            '"synthetic": true,\n  "cases"', '"synthetic": false,\n  "cases"', 1
        )
        with tempfile.TemporaryDirectory() as folder:
            tmp = Path(folder) / "records.json"
            tmp.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                fixture_api.load_fixture(records=tmp)


if __name__ == "__main__":
    unittest.main()
