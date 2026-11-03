"""Behavioural tests for the TAX-018 Vati OpenAPI contract and its validator."""

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integration" / "api"))

import contract  # noqa: E402

SPEC = contract.load_spec()
EXAMPLES = {e["name"]: e for e in contract.load_examples(contract.HTTP_EXAMPLES)}
EVENTS = contract.load_examples(contract.EVENT_EXAMPLES)
CASE = "40000000-0000-4000-8000-000000000004"
TENANT = "10000000-0000-4000-8000-000000000001"


def codes(violations):
    return {v.code for v in violations}


class ContractTests(unittest.TestCase):
    def test_contract_and_all_examples_validate(self):
        self.assertEqual(contract.check_all(), [])

    def test_five_success_pairs_cover_all_five_scenarios(self):
        success = [e for e in EXAMPLES.values() if str(e["response"]["status"]).startswith("2")]
        self.assertGreaterEqual(len(success), 5)
        self.assertEqual({e["scenario"] for e in success}, {f"SYN-00{i}" for i in range(1, 6)})
        operation_ids = {op["operationId"] for _, _, op in contract.operations(SPEC)}
        self.assertEqual(operation_ids, {e["operation_id"] for e in success})

    def test_required_error_contracts_have_examples(self):
        error_codes = {
            e["response"]["body"]["error"]["code"]
            for e in EXAMPLES.values()
            if "error" in (e["response"]["body"] or {})
        }
        self.assertTrue(
            {
                "missing_tenant_context",
                "invalid_case_id",
                "tenant_scope_denied",
                "version_conflict",
                "idempotency_conflict",
                "upstream_timeout",
            }
            <= error_codes
        )

    def test_duplicate_request_replays_original_response(self):
        original = EXAMPLES["create_case_legitimate_refund"]
        replay = EXAMPLES["create_case_duplicate_replay"]
        conflict = EXAMPLES["error_idempotency_key_reused_with_changed_body"]
        key = original["request"]["headers"]["Idempotency-Key"]
        self.assertEqual(replay["request"]["headers"]["Idempotency-Key"], key)
        self.assertEqual(replay["request"]["body"], original["request"]["body"])
        self.assertEqual(replay["response"]["body"], original["response"]["body"])
        self.assertEqual(replay["response"]["headers"]["Idempotent-Replayed"], "true")
        self.assertEqual(conflict["request"]["headers"]["Idempotency-Key"], key)
        self.assertNotEqual(conflict["request"]["body"], original["request"]["body"])


class RequestRejectionTests(unittest.TestCase):
    def headers(self, **overrides):
        h = {"Authorization": "Bearer x", "X-Vati-Tenant-ID": TENANT}
        h.update(overrides)
        return {k: v for k, v in h.items() if v is not None}

    def test_missing_tenant_header_is_rejected_on_every_operation(self):
        for method, template, _ in contract.operations(SPEC):
            path = template.replace("{case_id}", CASE)
            with self.subTest(op=f"{method} {template}"):
                found = contract.check_request(
                    SPEC, method, path, self.headers(**{"X-Vati-Tenant-ID": None})
                )
                self.assertIn("missing_tenant_context", codes(found))

    def test_malformed_tenant_is_rejected(self):
        found = contract.check_request(
            SPEC, "get", f"/api/v1/cases/{CASE}", self.headers(**{"X-Vati-Tenant-ID": "acme"})
        )
        self.assertEqual(codes(found), {"missing_tenant_context"})

    def test_missing_or_malformed_case_id_is_rejected(self):
        for case_id in ["", "not-a-uuid", "SYN-004"]:
            with self.subTest(case_id=case_id):
                found = contract.check_request(
                    SPEC, "get", f"/api/v1/cases/{case_id}/findings", self.headers()
                )
                self.assertEqual(codes(found), {"invalid_case_id"})

    def test_header_names_are_case_insensitive(self):
        found = contract.check_request(
            SPEC, "get", f"/api/v1/cases/{CASE}", {"x-vati-tenant-id": TENANT}
        )
        self.assertEqual(found, [])

    def test_mutation_without_idempotency_key_is_rejected(self):
        body = EXAMPLES["start_investigation_related_party"]["request"]["body"]
        found = contract.check_request(
            SPEC, "post", f"/api/v1/cases/{CASE}/investigate", self.headers(), body
        )
        self.assertEqual(codes(found), {"invalid_request"})

    def test_mutation_without_expected_version_is_rejected(self):
        found = contract.check_request(
            SPEC,
            "post",
            f"/api/v1/cases/{CASE}/investigate",
            self.headers(**{"Idempotency-Key": "auditor:investigate:test:1"}),
            {"playbook_id": "related_party_review@1"},
        )
        self.assertEqual(codes(found), {"invalid_request"})

    def test_float_money_and_unknown_fields_are_rejected(self):
        body = copy.deepcopy(EXAMPLES["create_case_legitimate_refund"]["request"]["body"])
        body["refund_claim"]["amount"] = 15000.0
        body["tenant_id"] = TENANT
        found = contract.check_request(
            SPEC,
            "post",
            "/api/v1/cases",
            self.headers(**{"Idempotency-Key": "frappe:VT-SUB-test:submit:v1"}),
            body,
        )
        detail = " ".join(v.detail for v in found)
        self.assertIn("refund_claim.amount: expected string", detail)
        self.assertIn("unexpected property 'tenant_id'", detail)

    def test_unknown_route_is_not_found(self):
        found = contract.check_request(SPEC, "delete", f"/api/v1/cases/{CASE}", self.headers())
        self.assertEqual(codes(found), {"not_found"})


class ExampleConsistencyTests(unittest.TestCase):
    def test_cross_tenant_response_data_is_detected(self):
        example = copy.deepcopy(EXAMPLES["get_case_ambiguous_service"])
        example["response"]["body"]["data"]["tenant_id"] = "10000000-0000-4000-8000-000000000002"
        self.assertTrue(
            any("tenant_id differs" in e for e in contract.check_example(SPEC, example))
        )

    def test_response_for_another_case_is_detected(self):
        example = copy.deepcopy(EXAMPLES["list_findings_policy_gap"])
        example["response"]["body"]["data"][0]["case_id"] = CASE
        self.assertTrue(any("case_id differs" in e for e in contract.check_example(SPEC, example)))

    def test_non_synthetic_example_is_rejected(self):
        example = copy.deepcopy(EXAMPLES["get_case_ambiguous_service"])
        example["synthetic"] = False
        self.assertTrue(contract.check_example(SPEC, example))

    def test_finding_cannot_skip_human_approval(self):
        example = copy.deepcopy(EXAMPLES["list_findings_imported_services_gap"])
        example["response"]["body"]["data"][1]["proposed_action"]["requires_human_approval"] = False
        self.assertTrue(contract.check_example(SPEC, example))

    def test_rejected_request_must_map_to_its_error_code(self):
        example = copy.deepcopy(EXAMPLES["error_missing_tenant_context"])
        example["response"]["body"]["error"]["code"] = "forbidden"
        self.assertTrue(any("does not match" in e for e in contract.check_example(SPEC, example)))

    def test_error_correlation_must_match_header(self):
        example = copy.deepcopy(EXAMPLES["error_upstream_timeout"])
        example["response"]["body"]["error"]["correlation_id"] = TENANT
        self.assertTrue(contract.check_example(SPEC, example))

    def test_events_require_tenant_and_case(self):
        for field in ["tenant_id", "case_id", "idempotency_key"]:
            with self.subTest(field=field):
                event = copy.deepcopy(EVENTS[0])
                del event[field]
                self.assertTrue(contract.check_event(SPEC, event))


class SpecLintTests(unittest.TestCase):
    def test_lint_detects_mutation_without_idempotency_or_tenant(self):
        spec = copy.deepcopy(SPEC)
        params = spec["paths"]["/api/v1/cases"]["post"]["parameters"]
        spec["paths"]["/api/v1/cases"]["post"]["parameters"] = [
            p for p in params if "IdempotencyKey" not in p["$ref"] and "Tenant" not in p["$ref"]
        ]
        errors = " ".join(contract.lint_spec(spec))
        self.assertIn("must require the X-Vati-Tenant-ID header", errors)
        self.assertIn("mutations must require Idempotency-Key", errors)

    def test_lint_rejects_floats_and_unchecked_keywords(self):
        spec = copy.deepcopy(SPEC)
        spec["components"]["schemas"]["Money"]["properties"]["amount"] = {"type": "number"}
        spec["components"]["schemas"]["Case"]["oneOf"] = []
        errors = " ".join(contract.lint_spec(spec))
        self.assertIn("floating-point numbers are not allowed", errors)
        self.assertIn("unsupported schema keyword(s) ['oneOf']", errors)

    def test_lint_rejects_unresolved_refs_and_missing_error_responses(self):
        spec = copy.deepcopy(SPEC)
        op = spec["paths"]["/api/v1/cases/{case_id}"]["get"]
        del op["responses"]["503"]
        spec["components"]["schemas"]["CaseEnvelope"]["properties"]["data"]["$ref"] = (
            "#/components/schemas/Nope"
        )
        errors = " ".join(contract.lint_spec(spec))
        self.assertIn("missing error responses ['503']", errors)
        self.assertIn("unresolved $ref", errors)

    def test_validator_refuses_unknown_keywords_at_runtime(self):
        errors = contract.validate({}, {"oneOf": []}, SPEC)
        self.assertEqual(errors, ["$: unsupported schema keyword(s) ['oneOf']"])

    def test_boolean_is_not_an_integer(self):
        self.assertTrue(contract.validate(True, {"type": "integer"}, SPEC))

    def test_timestamps_must_be_utc(self):
        schema = {"$ref": "#/components/schemas/Timestamp"}
        self.assertEqual(contract.validate("2026-11-03T10:00:00Z", schema, SPEC), [])
        self.assertTrue(contract.validate("2026-11-03T12:00:00+02:00", schema, SPEC))


if __name__ == "__main__":
    unittest.main()
