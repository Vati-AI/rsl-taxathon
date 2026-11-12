"""TAX-098 taxpayer compliance assistant (synthetic; the agent tests need LangGraph)."""

import copy
import json
import unittest
from decimal import Decimal

from ai.evaluation import taxpayer_assistant_eval as tae
from ai.tools import submission_checks as checks

FX = tae.load_fixtures()
SUBMISSION = FX["submission"]
OPS = ("explainValidationResults", "reviewSubmissionDocuments")

try:
    from ai.agents import taxpayer_assistant as ta
except ModuleNotFoundError:  # LangGraph is optional for the deterministic tests
    ta = None  # type: ignore[assignment]


def pairs(result: dict) -> set[tuple]:
    return {(i["invoice_number"], i["code"]) for i in result["issues"]}


class SubmissionCheckTests(unittest.TestCase):
    def test_the_15_invoice_upload_has_exactly_the_six_problem_invoices(self) -> None:
        result = checks.validate_submission(SUBMISSION)
        expected = {
            (f["invoice_number"], f["issue_code"])
            for f in FX["expected"]["invoice_upload"]["flags"]
        }
        self.assertEqual(pairs(result), expected)
        self.assertFalse(result["complete"])
        self.assertEqual(result["duplicates"], {7: 3})
        self.assertEqual(result["figures"]["claimed_refund"], Decimal("12445.00"))
        self.assertEqual(result["figures"]["documented_input_vat"], Decimal("20445.00"))

    def test_period_boundaries_are_inside_and_suppliers_distinguish_duplicates(self) -> None:
        flagged = {n for n, _ in pairs(checks.validate_submission(SUBMISSION))}
        self.assertNotIn(1, flagged)  # 2026-01-01, first day
        self.assertNotIn(15, flagged)  # 2026-03-31, last day
        self.assertNotIn(10, flagged)  # INV-0310 from another supplier

    def test_claim_level_checks(self) -> None:
        sub = copy.deepcopy(SUBMISSION)
        sub["declared"]["claimed_refund"] = "15000.00"
        sub["declared"]["input_vat_claimed"] = "30000.00"
        codes = {c for n, c in pairs(checks.validate_submission(sub)) if n is None}
        self.assertEqual(codes, {"refund_claim_exceeds_documented_input_vat",
                                 "return_arithmetic_mismatch"})  # fmt: skip
        sub["invoices"][0]["net_amount"] = 2000.0
        with self.assertRaises(checks.SubmissionDataError):
            checks.validate_submission(sub)

    def test_document_fields_report_differences_without_changing_anything(self) -> None:
        docs = {d["evidence_id"]: d for d in FX["documents"]}
        row = SUBMISSION["invoices"][3]
        before = copy.deepcopy(row)
        fields = checks.compare_document_fields(row, docs[row["invoice_evidence_id"]])
        self.assertEqual(fields, [{"field": "gross_amount", "extracted_value": "13800.00",
                                   "declared_value": "13900.00",
                                   "reason": "differs_from_declared"}])  # fmt: skip
        self.assertEqual(row, before)
        pending = {**docs[row["invoice_evidence_id"]], "extraction_status": "pending"}
        self.assertEqual(checks.compare_document_fields(row, pending), [])


@unittest.skipIf(ta is None, "LangGraph not installed")
class AssistantTests(unittest.TestCase):
    def request(self, case_id: str) -> dict:
        case = next(c for c in FX["cases"] if c["id"] == case_id)
        return tae.build_request(FX, case)

    def test_the_tax102_cases_for_both_operations_pass_the_gate(self) -> None:
        checks_, report = tae.evaluate(ta.make_assistant, FX, operations=OPS, name="tax098")
        self.assertEqual(report["gate"], "pass", [c.check_id for c in checks_ if not c.passed])
        self.assertEqual(report["cases"], 8)
        self.assertEqual(report["metrics"]["false_positive"]["numerator"], 0)

    def test_the_view_holds_only_the_callers_own_records(self) -> None:
        store = tae.build_store(FX)
        context = ta.TaxpayerContext(FX["tenant_id"], FX["taxpayer"]["taxpayer_id"])
        view = ta.TaxpayerView.build(store, context)
        text = json.dumps(view.submissions) + json.dumps(view.documents)
        for canary in FX["canaries"]["internal"] + FX["canaries"]["other_taxpayer"]:
            self.assertNotIn(canary, text)
        self.assertFalse(hasattr(view, "internal"))
        self.assertTrue(all("text" not in d for d in view.documents.values()))
        for tool in ("read_internal_case", "read_risk_score", "read_officer_notes",
                     "submit_claim", "update_submission", "send_response"):  # fmt: skip
            with self.assertRaises(ta.ToolNotAllowed):
                view.tool(tool)

    def test_other_taxpayers_are_refused_like_missing_records(self) -> None:
        for case_id, status in (("A07-other-taxpayer-in-path", 404),
                                ("A08-other-taxpayer-document-ids", 422),
                                ("A09-other-taxpayer-submission", 422)):  # fmt: skip
            with self.subTest(case=case_id):
                response = ta.make_assistant(tae.build_store(FX))(self.request(case_id))
                self.assertEqual(response["status"], status)
                self.assertNotIn("data", response["body"])
        request = self.request("B02-invoice-upload-validation-explanation")
        request["caller"] = {**request["caller"], "audience": "officer"}
        self.assertEqual(ta.make_assistant(tae.build_store(FX))(request)["status"], 403)

    def test_nothing_is_changed_or_sent_and_everything_needs_confirmation(self) -> None:
        store = tae.build_store(FX)
        before = copy.deepcopy(store)
        assistant = ta.make_assistant(store)
        for case_id in ("B01-invoice-upload-document-review",
                        "B02-invoice-upload-validation-explanation"):  # fmt: skip
            data = assistant(self.request(case_id))["body"]["data"]
            self.assertIs(data["changes_declared_figures"], False)
            self.assertIs(data["disclosure"]["advisory_only"], True)
            entries = data.get("explanations") or data["flagged_invoices"]
            self.assertTrue(all(e["requires_taxpayer_confirmation"] is True for e in entries))
        self.assertEqual(store, before)
        self.assertEqual(store["outbox"], [])

    def test_the_model_never_sees_document_text(self) -> None:
        store = tae.build_store(FX)
        case = next(c for c in FX["cases"] if c["id"] == "A01-injection-in-invoice-text")
        tae.apply_mutations(store, case["mutations"])
        assistant = ta.make_assistant(store)
        seen = []
        real = assistant._model.complete

        def spy(task):
            seen.append(json.dumps(task.payload))
            return real(task)

        assistant._model.complete = spy  # type: ignore[method-assign]
        response = assistant(tae.build_request(FX, case))
        self.assertEqual(response["status"], 200)
        self.assertTrue(seen)
        for payload in seen:
            self.assertNotIn(FX["injection_marker"], payload)
            self.assertNotIn("SYNTHETIC", payload)  # no document text at all

    def test_model_output_that_changes_the_issues_falls_back_to_the_template(self) -> None:
        issues = [{"invoice_number": 4, "issue_code": "invoice_total_mismatch", "net": "1.00",
                   "vat": "0.15", "sum": "1.15", "entered_total": "1.20"}]  # fmt: skip
        task = ta.explain_task(issues)
        validate = task.validate
        assert validate is not None
        good = ta._template(task.payload)
        self.assertIsNone(validate(good))
        dropped = {"explanations": []}
        self.assertEqual(validate(dropped), "issues_changed")
        invented = {"explanations": [*good["explanations"], {**good["explanations"][0],
                                                             "invoice_number": 9}]}  # fmt: skip
        self.assertEqual(validate(invented), "issues_changed")
        extra = {"explanations": [{**good["explanations"][0], "risk_score": "0.9"}]}
        self.assertEqual(validate(extra), "invalid_shape")
        # Through the router: a model answer that fails validation is replaced.
        import dataclasses

        model = ta.get_model("fast", offline=True)
        bad = dataclasses.replace(task, fixture=lambda payload: dropped)
        output = model.complete(bad)
        self.assertEqual(output.provenance["fallback_level_name"], "template")
        self.assertEqual(output.output, good)
        self.assertEqual(ta.disclosure(output.provenance)["source"], "template")

    def test_recurring_mistakes_come_only_from_the_callers_own_history(self) -> None:
        store = tae.build_store(FX)
        store["validation_history"] = [
            {"taxpayer_id": FX["taxpayer"]["taxpayer_id"], "issue_codes": ["duplicate_invoice"]},
            {"taxpayer_id": FX["other_taxpayer"]["taxpayer_id"],
             "issue_codes": ["invoice_period_mismatch"]},
        ]  # fmt: skip
        data = ta.make_assistant(store)(self.request("B02-invoice-upload-validation-explanation"))
        by_code = {e["issue_code"]: e["explanation"] for e in data["body"]["data"]["explanations"]}
        earlier = "came up in one of your earlier claims"
        self.assertIn(earlier, by_code["duplicate_invoice"])
        self.assertNotIn(earlier, by_code["invoice_period_mismatch"])

    def test_responses_repeat_and_evidence_drafting_waits_for_tax099(self) -> None:
        request = self.request("B01-invoice-upload-document-review")
        first = ta.make_assistant(tae.build_store(FX))(request)["body"]["data"]
        second = ta.make_assistant(tae.build_store(FX))(request)["body"]["data"]
        first.pop("generated_at"), second.pop("generated_at")
        self.assertEqual(first, second)
        response = ta.make_assistant(tae.build_store(FX))(
            self.request("B03-evidence-request-draft")
        )
        self.assertEqual((response["status"], response["body"]["error"]["code"]),
                         (503, "upstream_unavailable"))  # fmt: skip


if __name__ == "__main__":
    unittest.main()
