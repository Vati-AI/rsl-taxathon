"""getCaseReferences in the fixture API (TAX-133): the case reference card."""

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
CORRELATION = "c0000000-0000-4000-8000-000000000133"
REFUND_CASE = "40000000-0000-4000-8000-000000000001"
REFERRED_CASE = "40000000-0000-4000-8000-000000000002"
EXAMPLE = (
    ROOT
    / "integration"
    / "api"
    / "examples"
    / "http"
    / "49_get_case_references_legitimate_refund.json"
)


def call(case_id, tenant=TENANT):
    headers = {"Authorization": "Bearer synthetic", "X-Correlation-ID": CORRELATION}
    if tenant is not None:
        headers["X-Vati-Tenant-ID"] = tenant
    path = f"/api/v1/cases/{case_id}/references"
    status, res_headers, body = fixture_api.handle("GET", path, headers, {}, DATA, SPEC)
    example = {
        "name": "fixture case references",
        "operation_id": "getCaseReferences",
        "synthetic": True,
        "request": {"method": "GET", "path": path, "headers": headers},
        "response": {"status": status, "headers": res_headers, "body": body},
    }
    return status, body, contract.check_example(SPEC, example)


class CaseReferencesTests(unittest.TestCase):
    def test_refund_case_card_shows_the_rsl_claim_tin_and_tool_references(self):
        status, body, problems = call(REFUND_CASE)
        self.assertEqual((status, problems), (200, []))
        card = body["data"]
        self.assertEqual(card["audience"], "officer")
        self.assertEqual(card["display_id"], "SYN-001")
        self.assertEqual(card["tin_masked"], "******4101")
        self.assertEqual(card["rsl_claim_id"], "SYN-ORA-2026-0001")
        self.assertEqual(card["jube_case_id"], "syn-jube-3001")
        self.assertIsNone(card["rsl_audit_case_ref"])
        self.assertIsNone(card["openaleph_collection_id"])
        self.assertEqual(
            card["rsl_records"],
            [{"system": "etax", "record_type": "vat_return", "reference": "SYN-VR-2026-09-4101"}],
        )

    def test_referred_case_card_shows_the_rsl_audit_case(self):
        status, body, problems = call(REFERRED_CASE)
        self.assertEqual((status, problems), (200, []))
        self.assertEqual(body["data"]["rsl_audit_case_ref"], "SYN-AUD-2026-0002")
        self.assertIsNone(body["data"]["rsl_claim_id"])

    def test_rsl_links_are_contract_service_links_on_the_case(self):
        path = f"/api/v1/cases/{REFUND_CASE}"
        headers = {"X-Vati-Tenant-ID": TENANT, "X-Correlation-ID": CORRELATION}
        status, _, body = fixture_api.handle("GET", path, headers, {}, DATA, SPEC)
        self.assertEqual(status, 200)
        services = {link["service"] for link in body["data"]["service_links"]}
        self.assertTrue({"online_refunds", "etax", "jube"} <= services)

    def test_unknown_case_other_tenant_and_missing_tenant_are_refused(self):
        status, body, problems = call("40000000-0000-4000-8000-000000000999")
        self.assertEqual((status, body["error"]["code"], problems), (404, "not_found", []))
        status, body, _ = call(REFUND_CASE, tenant=OTHER_TENANT)
        self.assertEqual((status, body["error"]["code"]), (403, "tenant_scope_denied"))
        status, body, _ = call(REFUND_CASE, tenant=None)
        self.assertEqual((status, body["error"]["code"]), (400, "missing_tenant_context"))

    def test_the_published_example_is_what_the_fixture_server_returns(self):
        example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        status, body, _ = call(REFUND_CASE)
        self.assertEqual(example["response"]["status"], status)
        self.assertEqual(example["response"]["body"], body)


if __name__ == "__main__":
    unittest.main()
