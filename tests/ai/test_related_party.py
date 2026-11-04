"""TAX-049 deterministic related-party service and persistent-loss review (model-free)."""

import copy
import json
import unittest
from pathlib import Path

from ai.tools.refund_reconciliation import RefundDataError
from ai.tools.related_party import PROHIBITED_CONCLUSIONS, review_related_party

ROOT = Path(__file__).resolve().parents[2]
RECORDS = {
    record["case_id"]: record
    for record in json.loads((ROOT / "data/synthetic/tax024_records.json").read_text())
}
ACCUSATORY = ("fraud", "abuse", "evasion", "sham", "fake", "illegal", "liab", "penalt", "inflat")


def syn002() -> dict:
    return copy.deepcopy(RECORDS["SYN-002"])


def with_documents(record: dict, *doc_ids: str) -> dict:
    record["documents"] = [{"id": doc_id, "kind": "synthetic_support"} for doc_id in doc_ids]
    return record


def codes(review: dict, key: str) -> set[str]:
    return {item["code"] for item in review[key]}


def case_ids(record: dict) -> set[str]:
    docs = [
        record["return"],
        *record["contracts"],
        *record["invoices"],
        *record["payments"],
        *record.get("documents", []),
    ]
    return {doc["id"] for doc in docs}


def all_citations(review: dict) -> list[list[str]]:
    cited = [
        item["evidence_ids"]
        for key in ("observations", "counter_evidence", "evidence_gaps", "discrepancies")
        for item in review[key]
    ]
    cited += [q["evidence_ids"] for q in review["evidence_questions"]]
    cited += [t["evidence_ids"] for rel in review["relationships"] for t in rel["tests"]]
    return cited


class RelatedPartyScenarioTests(unittest.TestCase):
    def test_syn002_reproduces_expected_related_party_and_loss_findings(self) -> None:
        record = RECORDS["SYN-002"]
        review = review_related_party(record)
        observed = {item["code"]: item["evidence_ids"] for item in review["observations"]}
        for expected in record["expected_findings"]:
            if expected["code"] in ("related_party_transaction", "consecutive_loss_periods"):
                self.assertEqual(observed[expected["code"]], expected["evidence_ids"])
        self.assertFalse(
            (codes(review, "observations") | codes(review, "evidence_gaps"))
            & set(record["expected_non_findings"])
        )
        self.assertIsNone(review["legal_conclusion"])
        self.assertIsNone(review["enforcement_conclusion"])

    def test_syn002_unsupported_service_is_an_evidence_gap_with_questions(self) -> None:
        review = review_related_party(RECORDS["SYN-002"])
        self.assertEqual(
            codes(review, "evidence_gaps"),
            {
                "service_delivery_evidence_missing",
                "service_benefit_evidence_missing",
                "loss_explanation_not_documented",
            },
        )
        self.assertEqual(
            {q["topic"] for q in review["evidence_questions"]},
            {"service_delivery", "service_benefit", "loss_explanation"},
        )
        tests = {t["test"]: t["result"] for t in review["relationships"][0]["tests"]}
        self.assertEqual(tests["service_delivery"], "evidence_gap")
        self.assertEqual(tests["benefit"], "evidence_gap")
        for question in review["evidence_questions"]:
            self.assertEqual(question["status"], "draft_requires_officer_approval")
            for word in ACCUSATORY:
                self.assertNotIn(word, question["text"].lower(), question["question_id"])

    def test_syn002_records_counter_evidence(self) -> None:
        review = review_related_party(RECORDS["SYN-002"])
        counter = {item["code"]: item["evidence_ids"] for item in review["counter_evidence"]}
        self.assertEqual(counter["payment_trail_consistent"], ["INV-SYN-002-01", "PAY-SYN-002-01"])
        self.assertEqual(counter["no_duplication_observed"], ["CTR-SYN-002-01", "CTR-SYN-002-02"])

    def test_every_citation_resolves_to_a_case_record(self) -> None:
        for record in RECORDS.values():
            with self.subTest(case_id=record["case_id"]):
                review = review_related_party(record)
                known = case_ids(record)
                for evidence_ids in all_citations(review):
                    self.assertTrue(evidence_ids)
                    self.assertTrue(set(evidence_ids) <= known, evidence_ids)

    def test_scenarios_without_relationship_or_loss_pattern_have_no_observations(self) -> None:
        for case_id in ("SYN-001", "SYN-003", "SYN-004", "SYN-005"):
            with self.subTest(case_id=case_id):
                review = review_related_party(RECORDS[case_id])
                self.assertEqual(review["observations"], [])
                self.assertEqual(review["evidence_questions"], [])


class RelatedPartyTestBehaviour(unittest.TestCase):
    def test_documented_delivery_benefit_and_explanation_become_counter_evidence(self) -> None:
        record = with_documents(syn002(), "DOC-SLA-002", "DOC-USE-002", "DOC-MGMT-002")
        record["contracts"][0]["delivery_evidence_ids"] = ["DOC-SLA-002"]
        record["invoices"][0]["benefit_evidence_ids"] = ["DOC-USE-002"]
        record["loss_explanations"] = [
            {
                "code": "expansion_costs",
                "description": "New depot",
                "evidence_ids": ["DOC-MGMT-002"],
            }
        ]
        review = review_related_party(record)
        self.assertEqual(review["evidence_gaps"], [])
        self.assertEqual(review["evidence_questions"], [])
        counter = codes(review, "counter_evidence")
        self.assertTrue(
            {
                "service_delivery_documented",
                "benefit_documented",
                "documented_alternative_explanation",
            }
            <= counter
        )
        (explanation,) = [
            c
            for c in review["counter_evidence"]
            if c["code"] == "documented_alternative_explanation"
        ]
        self.assertEqual(explanation["explanation"], "expansion_costs")
        # Observations remain: documentation explains, it does not erase the relationship.
        self.assertIn("related_party_transaction", codes(review, "observations"))

    def test_undocumented_explanation_is_not_counter_evidence(self) -> None:
        for evidence_ids in ([], ["DOC-NOT-SUPPLIED"]):
            with self.subTest(evidence_ids=evidence_ids):
                record = syn002()
                record["loss_explanations"] = [
                    {"code": "market", "description": "x", "evidence_ids": evidence_ids}
                ]
                review = review_related_party(record)
                self.assertIn("loss_explanation_not_documented", codes(review, "evidence_gaps"))
                self.assertNotIn(
                    "documented_alternative_explanation", codes(review, "counter_evidence")
                )
        self.assertIn("cited_evidence_not_supplied", codes(review, "evidence_gaps"))

    def test_cited_document_not_in_case_is_a_gap_not_counter_evidence(self) -> None:
        record = syn002()
        record["contracts"][0]["delivery_evidence_ids"] = ["DOC-NOT-SUPPLIED"]
        record["contracts"][0]["relationship"] = {
            "type": "common_ownership",
            "evidence_ids": ["DOC-ALSO-MISSING"],
        }
        review = review_related_party(record)
        missing = [g for g in review["evidence_gaps"] if g["code"] == "cited_evidence_not_supplied"]
        self.assertEqual(
            sorted(i for g in missing for i in g["missing_ids"]),
            ["DOC-ALSO-MISSING", "DOC-NOT-SUPPLIED"],
        )
        self.assertNotIn("service_delivery_documented", codes(review, "counter_evidence"))

    def test_same_category_and_repeated_billing_are_flagged_for_review(self) -> None:
        record = syn002()
        record["contracts"][0]["service_category"] = "logistics"
        record["contracts"][1]["service_category"] = "Logistics"
        duplicate = dict(record["invoices"][0], id="INV-SYN-002-03")
        record["invoices"].append(duplicate)
        record["payments"].append(
            {"id": "PAY-SYN-002-03", "invoice_id": "INV-SYN-002-03", "amount": "87200.00"}
        )
        review = review_related_party(record)
        (dup,) = [o for o in review["observations"] if o["code"] == "possible_service_duplication"]
        self.assertTrue(
            {"CTR-SYN-002-01", "CTR-SYN-002-02", "INV-SYN-002-01", "INV-SYN-002-03"}
            <= set(dup["evidence_ids"])
        )
        self.assertIn("duplication", {q["topic"] for q in review["evidence_questions"]})
        self.assertNotIn("no_duplication_observed", codes(review, "counter_evidence"))

    def test_payment_gap_and_one_cent_discrepancy(self) -> None:
        record = syn002()
        record["payments"][0]["amount"] = "87199.99"
        review = review_related_party(record)
        (disc,) = review["discrepancies"]
        self.assertEqual(disc["code"], "payment_total_differs_from_invoice")
        self.assertEqual(disc["paid"], {"amount": "87199.99", "currency": "LSL"})
        record["payments"] = record["payments"][1:]
        review = review_related_party(record)
        self.assertIn("payment_evidence_missing", codes(review, "evidence_gaps"))

    def test_short_loss_history_is_not_a_pattern(self) -> None:
        record = syn002()
        record["loss_periods"] = ["2025-Q2", "2025-Q4"]
        review = review_related_party(record)
        self.assertNotIn("consecutive_loss_periods", codes(review, "observations"))
        self.assertIs(review["loss_review"]["persistent_loss"], False)

    def test_no_prohibited_conclusion_is_ever_emitted(self) -> None:
        review = review_related_party(RECORDS["SYN-002"])
        emitted = codes(review, "observations") | codes(review, "evidence_gaps")
        self.assertFalse(emitted & PROHIBITED_CONCLUSIONS)

    def test_unsafe_inputs_fail_closed_with_stable_codes(self) -> None:
        cases = {
            "invalid_money": lambda r: r["payments"][0].__setitem__("amount", 87200.0),
            "currency_mismatch": lambda r: r.__setitem__("currency", "ZAR"),
            "duplicate_record_id": lambda r: r["contracts"].append(dict(r["contracts"][0])),
            "invalid_record": lambda r: r["contracts"][0].__setitem__(
                "delivery_evidence_ids", "DOC"
            ),
        }
        for code, mutate in cases.items():
            with self.subTest(code=code):
                record = syn002()
                mutate(record)
                with self.assertRaises(RefundDataError) as raised:
                    review_related_party(record)
                self.assertEqual(raised.exception.code, code)


if __name__ == "__main__":
    unittest.main()
