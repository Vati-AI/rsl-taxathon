"""TAX-140 losses created by intra-group financing (model-free; playbook tests need LangGraph)."""

import copy
import json
import unittest
from pathlib import Path

from ai.tools.refund_reconciliation import RefundDataError
from ai.tools.related_party import PROHIBITED_CONCLUSIONS, review_related_party

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "data/synthetic/tax140_financing.json").read_text())
RECORDS = {r["case_id"]: r for r in FIXTURE["records"]}
ACCUSATORY = ("fraud", "abuse", "evasion", "sham", "fake", "illegal", "penalt", "avoid")

try:
    from ai.workflows.related_party_playbook import propose_officer_review
except ModuleNotFoundError:  # LangGraph is optional for the deterministic tests
    propose_officer_review = None


def all_codes(review: dict) -> set[str]:
    return {
        item["code"]
        for key in ("observations", "counter_evidence", "evidence_gaps", "discrepancies")
        for item in review[key]
    }


class FinancingLossScenarioTests(unittest.TestCase):
    def test_each_scenario_matches_its_expected_findings_and_non_findings(self) -> None:
        for case_id, record in RECORDS.items():
            with self.subTest(case=case_id):
                review = review_related_party(record)
                codes = all_codes(review)
                for code in record["expected_findings"]:
                    self.assertIn(code, codes)
                for code in record["expected_non_findings"]:
                    self.assertNotIn(code, review["financing_review"]["findings"])
                    if code != "financing_driven_losses":
                        self.assertNotIn(code, codes)
                self.assertIsNone(review["legal_conclusion"])
                self.assertIsNone(review["financing_review"]["legal_conclusion"])

    def test_financing_driven_and_operating_losses_are_kept_apart(self) -> None:
        fin = review_related_party(RECORDS["FIN-001"])["financing_review"]
        self.assertEqual(fin["loss_periods"]["financing_driven"], ["2023", "2024", "2025"])
        self.assertEqual(fin["loss_periods"]["operating"], [])
        op = review_related_party(RECORDS["FIN-002"])["financing_review"]
        self.assertEqual(op["loss_periods"]["operating"], ["2023", "2024", "2025"])
        self.assertEqual(op["loss_periods"]["financing_driven"], [])
        self.assertEqual(op["findings"], [])

    def test_an_operating_loss_mostly_made_of_related_party_interest_is_mixed(self) -> None:
        record = copy.deepcopy(RECORDS["FIN-002"])
        for period in record["financial_periods"]:
            # Operating loss 50,000; related-party interest 240,000 of a 290,000 loss.
            period["operating_result"] = "-50000.00"
            period["related_party_interest"] = "240000.00"
            period["other_finance_cost"] = "0.00"
        fin = review_related_party(record)["financing_review"]
        self.assertEqual(fin["loss_periods"]["mixed"], ["2023", "2024", "2025"])
        self.assertIn("financing_driven_losses", fin["findings"])

    def test_alternative_explanations_are_tested_and_recorded(self) -> None:
        for case_id in RECORDS:
            fin = review_related_party(RECORDS[case_id])["financing_review"]
            tested = [t["test"] for t in fin["alternative_explanations_tested"]]
            self.assertEqual(
                tested,
                [
                    "interest_matches_documented_loan_terms",
                    "third_party_finance_share",
                    "loan_funded_capital_investment",
                    "documented_loss_explanation",
                ],
            )
        one = review_related_party(RECORDS["FIN-001"])["financing_review"]
        self.assertEqual(one["alternative_explanations_tested"][0]["result"], "consistent")
        three = review_related_party(RECORDS["FIN-003"])
        tests = {t["test"]: t for t in three["financing_review"]["alternative_explanations_tested"]}
        self.assertEqual(tests["interest_matches_documented_loan_terms"]["result"], "differs")
        self.assertEqual(tests["loan_funded_capital_investment"]["result"], "documented")
        # A documented business reason keeps the pattern as an observation, not a finding.
        obs = next(o for o in three["observations"] if o["code"] == "financing_driven_losses")
        self.assertTrue(obs["explained"])
        self.assertEqual(three["financing_review"]["findings"], [])

    def test_a_documented_loss_explanation_also_covers_the_pattern(self) -> None:
        record = copy.deepcopy(RECORDS["FIN-001"])
        record["documents"].append({"id": "BUD-FIN-001", "kind": "budget"})
        record["loss_periods"] = ["2025-Q1", "2025-Q2", "2025-Q3"]
        record["loss_explanations"] = [{"code": "start_up", "evidence_ids": ["BUD-FIN-001"]}]
        review = review_related_party(record)
        self.assertNotIn("financing_driven_losses", review["financing_review"]["findings"])

    def test_carry_forward_pattern_is_recorded_with_the_interest_it_contains(self) -> None:
        review = review_related_party(RECORDS["FIN-001"])
        obs = next(o for o in review["observations"] if o["code"] == "financing_driven_losses")
        carry = obs["carry_forward"]
        self.assertEqual(carry["pattern"], "growing_carry_forward")
        self.assertEqual(carry["last"]["balance"]["amount"], "360000.00")
        self.assertEqual(
            carry["related_party_interest_in_financing_loss_periods"]["amount"], "360000.00"
        )

    def test_thin_capitalisation_is_an_unverified_ratio_observation(self) -> None:
        review = review_related_party(RECORDS["FIN-001"])
        obs = next(
            o
            for o in review["observations"]
            if o["code"] == "related_party_debt_above_reported_ratio"
        )
        self.assertEqual(obs["highest_ratio"], "5.00")
        self.assertIn("unverified", obs["scope_note"])

    def test_every_citation_resolves_and_wording_stays_neutral(self) -> None:
        for record in RECORDS.values():
            review = review_related_party(record)
            text = json.dumps(review).lower()
            for word in ACCUSATORY:
                self.assertNotIn(word, text)
            self.assertFalse(all_codes(review) & PROHIBITED_CONCLUSIONS)
            for question in review["evidence_questions"]:
                self.assertTrue(question["evidence_ids"])

    def test_malformed_figures_are_refused(self) -> None:
        record = copy.deepcopy(RECORDS["FIN-001"])
        record["financial_periods"][0]["related_party_interest"] = "-5.00"
        with self.assertRaises(RefundDataError):
            review_related_party(record)
        record = copy.deepcopy(RECORDS["FIN-001"])
        record["financial_periods"][0]["evidence_ids"] = ["NOT-IN-CASE"]
        with self.assertRaises(RefundDataError):
            review_related_party(record)

    def test_without_figures_the_tax049_review_is_unchanged(self) -> None:
        record = copy.deepcopy(RECORDS["FIN-002"])
        del record["financial_periods"]
        self.assertIsNone(review_related_party(record)["financing_review"])


@unittest.skipIf(propose_officer_review is None, "LangGraph not installed")
class FinancingPlaybookTests(unittest.TestCase):
    def test_routes_stay_unverified_candidates_never_eligible(self) -> None:
        assert propose_officer_review is not None
        proposal = propose_officer_review(review_related_party(RECORDS["FIN-001"]))
        assert proposal is not None
        self.assertEqual(proposal["code"], "financing_driven_losses")
        routes = proposal["financing"]["routes"]
        self.assertEqual(
            sorted(c["rule_id"] for c in routes["financing_driven_losses"]["candidates"]),
            ["CAND-DEDUCTION-TEST", "CAND-RP-ALLOCATION"],
        )
        self.assertEqual(
            [c["rule_id"] for c in routes["related_party_debt_above_reported_ratio"]["candidates"]],
            ["CAND-THIN-CAP"],
        )
        for route in routes.values():
            self.assertEqual(route["route_status"], "candidate_unverified")
            self.assertFalse(route["eligible_for_assessment"])
        self.assertIsNone(proposal["decision"])
        self.assertTrue(proposal["requires_officer_approval"])

    def test_an_explained_pattern_gives_no_financing_route(self) -> None:
        assert propose_officer_review is not None
        proposal = propose_officer_review(review_related_party(RECORDS["FIN-003"]))
        assert proposal is not None
        self.assertIsNone(proposal["financing"])
        self.assertIn(
            "loan_funded_capital_investment",
            {a["code"] for a in proposal["alternative_explanations"]},
        )


if __name__ == "__main__":
    unittest.main()
