"""Behavioural tests for the TAX-096 briefing, provenance, feedback and taxpayer-assistance contract."""

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integration" / "api"))

import contract  # noqa: E402

SPEC = contract.load_spec()
EXAMPLES = {e["name"]: e for e in contract.load_examples(contract.HTTP_EXAMPLES)}
TENANT = "10000000-0000-4000-8000-000000000001"
OTHER_TAXPAYER = "20000000-0000-4000-8000-000000000009"
TAXPAYER_OPS = {
    "explainValidationResults",
    "reviewSubmissionDocuments",
    "draftEvidenceResponse",
    "getEvidenceResponseDraft",
    "confirmEvidenceResponseDraft",
}
TAXPAYER_SUCCESS = [
    "explain_validation_results_refund_draft",
    "review_submission_documents_refund_draft",
    "draft_evidence_response_imported_services",
    "get_evidence_response_draft_imported_services",
    "confirm_evidence_response_draft",
]
INTERNAL_FIELDS = [
    "risk_score",
    "risk_level",
    "refund_lane",
    "selection_reasons",
    "findings",
    "finding_ids",
    "internal_notes",
    "case_id",
    "assigned_officer",
    "analysis_provenance",
    "legal_route",
    "confidence",
]


def schema(spec, name):
    return spec["components"]["schemas"][name]


def errors_for(example, spec=SPEC):
    return contract.check_example(spec, example)


def codes(violations):
    return {v.code for v in violations}


class TaxpayerExclusionTests(unittest.TestCase):
    def test_assistance_operations_are_taxpayer_context_with_clean_schemas(self):
        audience = {
            op["operationId"]
            for _, _, op in contract.operations(SPEC)
            if op.get(contract.AUDIENCE_KEY) == contract.TAXPAYER_AUDIENCE
        }
        self.assertEqual(audience, TAXPAYER_OPS)
        self.assertEqual(contract.lint_taxpayer_operations(SPEC), [])

    def test_internal_fields_cannot_be_added_to_taxpayer_schemas(self):
        for field in INTERNAL_FIELDS:
            with self.subTest(field=field):
                spec = copy.deepcopy(SPEC)
                schema(spec, "TaxpayerFacingDraftItem")["properties"][field] = {"type": "string"}
                errors = " ".join(contract.lint_spec(spec))
                self.assertIn(f"internal-only field '{field}'", errors)

    def test_taxpayer_operations_cannot_reference_officer_schemas(self):
        for target, prop, officer in [
            ("TaxpayerFacingEvidenceResponseDraft", "items", "Finding"),
            ("TaxpayerFacingValidationExplanation", "disclosure", "AnalysisProvenance"),
            ("TaxpayerFacingDocumentAssistance", "documents", "Evidence"),
        ]:
            with self.subTest(officer=officer):
                spec = copy.deepcopy(SPEC)
                schema(spec, target)["properties"][prop] = {
                    "$ref": f"#/components/schemas/{officer}"
                }
                errors = " ".join(contract.lint_spec(spec))
                self.assertIn(f"taxpayer operations may not reference {officer}", errors)

    def test_taxpayer_objects_must_be_closed(self):
        spec = copy.deepcopy(SPEC)
        schema(spec, "TaxpayerFacingExplanation")["additionalProperties"] = True
        schema(spec, "TaxpayerFacingDraftItem")["properties"]["extra"] = {"type": "object"}
        errors = contract.lint_spec(spec)
        self.assertEqual(sum("must set additionalProperties false" in e for e in errors), 2, errors)

    def test_assistance_routes_and_audience_must_coincide(self):
        spec = copy.deepcopy(SPEC)
        path = "/api/v1/taxpayers/{taxpayer_id}/assistance/validation-explanations"
        del spec["paths"][path]["post"][contract.AUDIENCE_KEY]
        spec["paths"]["/api/v1/cases/{case_id}/briefing"]["get"][contract.AUDIENCE_KEY] = "taxpayer"
        errors = " ".join(contract.lint_spec(spec))
        self.assertIn(f"POST {path}: taxpayer-assistance routes", errors)
        self.assertIn("GET /api/v1/cases/{case_id}/briefing: taxpayer-assistance routes", errors)

    def test_taxpayer_responses_cannot_carry_internal_fields(self):
        for name in TAXPAYER_SUCCESS:
            for field in INTERNAL_FIELDS:
                with self.subTest(example=name, field=field):
                    example = copy.deepcopy(EXAMPLES[name])
                    example["response"]["body"]["data"][field] = "synthetic"
                    self.assertTrue(errors_for(example))

    def test_nested_internal_fields_are_found_even_if_a_schema_is_loosened(self):
        # Defence in depth: the example scan still refuses officer-only field names.
        spec = copy.deepcopy(SPEC)
        schema(spec, "TaxpayerFacingDraftItem")["additionalProperties"] = True
        example = copy.deepcopy(EXAMPLES["draft_evidence_response_imported_services"])
        example["response"]["body"]["data"]["items"][0]["risk_score"] = "0.91"
        errors = errors_for(example, spec)
        self.assertTrue(any("risk_score: internal-only field" in e for e in errors), errors)

    def test_response_for_another_taxpayer_or_tenant_is_detected(self):
        for name in TAXPAYER_SUCCESS:
            with self.subTest(example=name):
                example = copy.deepcopy(EXAMPLES[name])
                example["response"]["body"]["data"]["taxpayer_id"] = OTHER_TAXPAYER
                self.assertIn(
                    f"{name}: response taxpayer_id differs from path taxpayer_id",
                    errors_for(example),
                )
                example = copy.deepcopy(EXAMPLES[name])
                example["request"]["headers"]["X-Vati-Tenant-ID"] = (
                    "10000000-0000-4000-8000-000000000002"
                )
                self.assertIn(
                    f"{name}: response tenant_id differs from request tenant", errors_for(example)
                )

    def test_other_taxpayer_is_not_found_and_officers_are_forbidden(self):
        other = EXAMPLES["error_assistance_other_taxpayer_not_found"]["response"]
        officer = EXAMPLES["error_assistance_officer_forbidden"]["response"]
        self.assertEqual((other["status"], other["body"]["error"]["code"]), (404, "not_found"))
        self.assertEqual((officer["status"], officer["body"]["error"]["code"]), (403, "forbidden"))

    def test_assistance_never_changes_declared_figures(self):
        for name in TAXPAYER_SUCCESS[:2]:
            with self.subTest(example=name):
                example = copy.deepcopy(EXAMPLES[name])
                example["response"]["body"]["data"]["changes_declared_figures"] = True
                self.assertTrue(errors_for(example))

    def test_suggestions_always_need_taxpayer_confirmation_and_disclose_their_source(self):
        example = copy.deepcopy(EXAMPLES["explain_validation_results_refund_draft"])
        example["response"]["body"]["data"]["explanations"][0]["requires_taxpayer_confirmation"] = (
            False
        )
        self.assertTrue(errors_for(example))
        example = copy.deepcopy(EXAMPLES["review_submission_documents_refund_draft"])
        del example["response"]["body"]["data"]["disclosure"]
        self.assertTrue(errors_for(example))

    def test_taxpayer_supplied_validation_results_are_refused(self):
        body = {"submission_id": "30000000-0000-4000-8000-000000000011", "issues": []}
        found = contract.check_request(
            SPEC,
            "post",
            "/api/v1/taxpayers/20000000-0000-4000-8000-000000000001/assistance/validation-explanations",
            {"X-Vati-Tenant-ID": TENANT, "Idempotency-Key": "taxpayer:explain:test:0001"},
            body,
        )
        self.assertEqual(codes(found), {"invalid_request"})


class DraftConfirmationTests(unittest.TestCase):
    def test_draft_is_saved_unsent_until_confirmed(self):
        draft = EXAMPLES["draft_evidence_response_imported_services"]["response"]["body"]["data"]
        self.assertEqual(
            (draft["status"], draft["sent_to_rsl"], draft["confirmation"]), ("draft", False, None)
        )
        confirmed = EXAMPLES["confirm_evidence_response_draft"]["response"]["body"]["data"]
        self.assertEqual((confirmed["status"], confirmed["sent_to_rsl"]), ("confirmed", True))
        self.assertEqual(confirmed["draft_id"], draft["draft_id"])
        self.assertGreater(confirmed["version"], draft["version"])

    def test_confirmation_requires_the_declaration(self):
        example = EXAMPLES["error_confirm_draft_without_declaration"]
        self.assertIs(example.get("request_conformant"), False)
        body = copy.deepcopy(example["request"]["body"])
        del body["declaration"]
        found = contract.check_request(
            SPEC, "post", example["request"]["path"], example["request"]["headers"], body
        )
        self.assertEqual(codes(found), {"invalid_request"})

    def test_confirmation_replays_and_never_sends_twice(self):
        original = EXAMPLES["confirm_evidence_response_draft"]
        replay = EXAMPLES["confirm_evidence_response_draft_replay"]
        stale = EXAMPLES["error_confirm_draft_stale_version"]
        key = original["request"]["headers"]["Idempotency-Key"]
        self.assertEqual(replay["request"]["headers"]["Idempotency-Key"], key)
        self.assertEqual(replay["request"]["body"], original["request"]["body"])
        self.assertEqual(replay["response"]["body"], original["response"]["body"])
        self.assertEqual(replay["response"]["headers"]["Idempotent-Replayed"], "true")
        self.assertNotEqual(stale["request"]["headers"]["Idempotency-Key"], key)
        self.assertEqual(stale["response"]["status"], 409)
        self.assertEqual(stale["response"]["body"]["error"]["code"], "version_conflict")
        self.assertEqual(
            stale["response"]["body"]["error"]["current_version"],
            original["response"]["body"]["data"]["version"],
        )

    def test_draft_mutations_require_tenant_and_idempotency_key(self):
        for name in [
            "draft_evidence_response_imported_services",
            "confirm_evidence_response_draft",
        ]:
            req = EXAMPLES[name]["request"]
            for header, code in [
                ("Idempotency-Key", "invalid_request"),
                ("X-Vati-Tenant-ID", "missing_tenant_context"),
            ]:
                with self.subTest(example=name, header=header):
                    headers = {k: v for k, v in req["headers"].items() if k != header}
                    found = contract.check_request(SPEC, "post", req["path"], headers, req["body"])
                    self.assertIn(code, codes(found))


class FindingFeedbackTests(unittest.TestCase):
    def test_feedback_replays_and_rejects_a_changed_body(self):
        original = EXAMPLES["record_finding_feedback_modified"]
        replay = EXAMPLES["record_finding_feedback_replay"]
        conflict = EXAMPLES["error_finding_feedback_key_reused"]
        key = original["request"]["headers"]["Idempotency-Key"]
        self.assertEqual(replay["request"]["headers"]["Idempotency-Key"], key)
        self.assertEqual(replay["request"]["body"], original["request"]["body"])
        self.assertEqual(replay["response"]["body"], original["response"]["body"])
        self.assertEqual(replay["response"]["headers"]["Idempotent-Replayed"], "true")
        self.assertEqual(conflict["request"]["headers"]["Idempotency-Key"], key)
        self.assertNotEqual(conflict["request"]["body"], original["request"]["body"])
        self.assertEqual(conflict["response"]["status"], 422)
        self.assertEqual(conflict["response"]["body"]["error"]["code"], "idempotency_conflict")

    def test_actor_comes_from_the_session_not_the_body(self):
        req = EXAMPLES["record_finding_feedback_modified"]["request"]
        for field in ["recorded_by", "actor_id", "officer_id", "role"]:
            with self.subTest(field=field):
                body = {**req["body"], field: "80000000-0000-4000-8000-000000000003"}
                found = contract.check_request(SPEC, "post", req["path"], req["headers"], body)
                self.assertEqual(codes(found), {"invalid_request"})

    def test_feedback_is_recorded_with_actor_and_never_changes_the_finding(self):
        data = EXAMPLES["record_finding_feedback_modified"]["response"]["body"]["data"]
        self.assertEqual(set(data["recorded_by"]), {"officer_id", "role"})
        for field, value in [
            ("changes_finding", True),
            ("verdict", "approved"),
            ("recorded_by", None),
        ]:
            with self.subTest(field=field):
                example = copy.deepcopy(EXAMPLES["record_finding_feedback_modified"])
                example["response"]["body"]["data"][field] = value
                self.assertTrue(errors_for(example))

    def test_feedback_is_case_and_tenant_scoped(self):
        example = copy.deepcopy(EXAMPLES["record_finding_feedback_modified"])
        example["response"]["body"]["data"]["case_id"] = "40000000-0000-4000-8000-000000000001"
        self.assertTrue(any("case_id differs" in e for e in errors_for(example)))
        example = copy.deepcopy(EXAMPLES["record_finding_feedback_modified"])
        example["response"]["body"]["data"]["tenant_id"] = "10000000-0000-4000-8000-000000000002"
        self.assertTrue(any("tenant_id differs" in e for e in errors_for(example)))

    def test_feedback_by_a_taxpayer_is_forbidden(self):
        example = EXAMPLES["error_finding_feedback_taxpayer_forbidden"]["response"]
        self.assertEqual((example["status"], example["body"]["error"]["code"]), (403, "forbidden"))


class BriefingAndProvenanceTests(unittest.TestCase):
    def briefing(self):
        return copy.deepcopy(EXAMPLES["get_case_briefing_imported_services_gap"])

    def test_briefing_cannot_carry_a_decision_or_conclusion(self):
        for field, value in [
            ("decision", "close_no_further_action"),
            ("legal_conclusion", "synthetic"),
            ("enforcement_conclusion", "synthetic"),
            ("requires_officer_approval", False),
        ]:
            with self.subTest(field=field):
                example = self.briefing()
                example["response"]["body"]["data"][field] = value
                self.assertTrue(errors_for(example))

    def test_every_statement_needs_a_resolvable_reference(self):
        example = self.briefing()
        sections = example["response"]["body"]["data"]["sections"]
        sections["what_happened"][0]["refs"] = []
        self.assertTrue(errors_for(example))
        example = self.briefing()
        sections = example["response"]["body"]["data"]["sections"]
        del sections["what_happened"][0]["refs"][0]["evidence_ref"]["document_sha256"]
        self.assertTrue(errors_for(example))

    def test_next_actions_are_limited_to_officer_proposals(self):
        example = self.briefing()
        sections = example["response"]["body"]["data"]["sections"]
        sections["next_action"][0]["action"] = "deny_refund"
        self.assertTrue(errors_for(example))

    def test_disclosure_must_match_the_recorded_provenance(self):
        example = self.briefing()
        example["response"]["body"]["data"]["disclosure"]["source"] = "template"
        self.assertTrue(any("disclosure.source: does not match" in e for e in errors_for(example)))
        example = self.briefing()
        example["response"]["body"]["data"]["analysis_provenance"]["fallback_level_name"] = (
            "template"
        )
        self.assertTrue(any("disclosure.source: does not match" in e for e in errors_for(example)))

    def test_provenance_records_the_adr_031_fields(self):
        required = set(schema(SPEC, "AnalysisProvenance")["required"])
        self.assertTrue(
            {
                "provider",
                "model_id",
                "role",
                "prompt_version",
                "settings",
                "fallback_level",
                "latency_ms",
                "token_usage",
                "validation",
            }
            <= required
        )
        for host in ["Finding", "InvestigationRun", "CaseBriefing"]:
            with self.subTest(schema=host):
                self.assertIn("analysis_provenance", schema(SPEC, host)["properties"])

    def test_provenance_rejects_unknown_providers_roles_and_float_settings(self):
        for path, value in [
            (("provider",), "openai"),
            (("role",), "planner"),
            (("settings", "temperature"), 0.2),
            (("validation", "status"), "skipped"),
        ]:
            with self.subTest(field=path):
                example = self.briefing()
                node = example["response"]["body"]["data"]["analysis_provenance"]
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] = value
                self.assertTrue(errors_for(example))

    def test_findings_without_provenance_remain_valid(self):
        example = copy.deepcopy(EXAMPLES["list_findings_policy_gap"])
        for finding in example["response"]["body"]["data"]:
            self.assertNotIn("analysis_provenance", finding)
        self.assertEqual(errors_for(example), [])

    def test_fallback_is_never_hidden(self):
        findings = EXAMPLES["list_findings_imported_services_gap"]["response"]["body"]["data"]
        provenance = findings[0]["analysis_provenance"]
        self.assertTrue(provenance["fell_back"])
        self.assertEqual(provenance["attempts"][0]["outcome"], "rate_limited")
        self.assertEqual(provenance["fallback_level_name"], "template")


if __name__ == "__main__":
    unittest.main()
