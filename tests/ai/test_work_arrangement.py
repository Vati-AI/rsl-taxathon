"""TAX-051 deterministic work-arrangement fact structuring (model-free)."""

import copy
import json
import unittest
from pathlib import Path

from ai.tools.refund_reconciliation import RefundDataError
from ai.tools.work_arrangement import (
    CLASSIFICATION_STATUS,
    FACTORS,
    PROHIBITED_CONCLUSIONS,
    review_work_arrangement,
)

ROOT = Path(__file__).resolve().parents[2]
RECORDS = {
    record["case_id"]: record
    for record in json.loads((ROOT / "data/synthetic/tax024_records.json").read_text())
}
ACCUSATORY = ("evasion", "misclassif", "disguised", "sham", "fraud", "liab", "penalt", "employee")


def syn003() -> dict:
    return copy.deepcopy(RECORDS["SYN-003"])


def fact(factor: str, value: str, source: str, *evidence: str) -> dict:
    return {
        "contract_id": "CTR-SYN-003-01",
        "factor": factor,
        "value": value,
        "source": source,
        "evidence_ids": list(evidence),
    }


def with_facts(record: dict, docs: list[str], facts: list[dict]) -> dict:
    record["documents"] = [{"id": d, "kind": "synthetic_support"} for d in docs]
    record["work_facts"] = facts
    return record


def factor_status(review: dict) -> dict[str, str]:
    return {f["factor"]: f["status"] for f in review["referrals"][0]["factors"]}


class WorkArrangementScenarioTests(unittest.TestCase):
    def test_syn003_is_referred_and_remains_unresolved(self) -> None:
        record = RECORDS["SYN-003"]
        review = review_work_arrangement(record)
        (referral,) = review["referrals"]
        self.assertEqual(referral["contract_id"], "CTR-SYN-003-01")
        self.assertEqual(referral["classification"]["status"], CLASSIFICATION_STATUS)
        self.assertIsNone(referral["classification"]["value"])
        self.assertIs(referral["classification"]["auto_classification_permitted"], False)
        self.assertEqual(review["classification_status"], CLASSIFICATION_STATUS)
        self.assertIsNone(review["assessment"])
        self.assertIsNone(review["legal_conclusion"])
        (expected,) = record["expected_findings"]
        (gap,) = review["evidence_gaps"]
        self.assertEqual(gap["code"], expected["code"])
        self.assertEqual(gap["evidence_ids"], expected["evidence_ids"])
        emitted = {g["code"] for g in review["evidence_gaps"]}
        self.assertFalse(emitted & (set(record["expected_non_findings"]) | PROHIBITED_CONCLUSIONS))

    def test_syn003_unknown_factors_become_neutral_questions(self) -> None:
        review = review_work_arrangement(RECORDS["SYN-003"])
        status = factor_status(review)
        self.assertEqual(status["payment_basis"], "consistent")  # derived from INV-SYN-003-01
        unknown = {f for f, s in status.items() if s == "unknown"}
        self.assertEqual(unknown, set(FACTORS) - {"payment_basis"})
        self.assertEqual({q["topic"] for q in review["evidence_questions"]}, unknown)
        for question in review["evidence_questions"]:
            self.assertEqual(question["status"], "draft_requires_officer_approval")
            self.assertEqual(question["evidence_ids"], ["CTR-SYN-003-01", "INV-SYN-003-01"])
            for word in ACCUSATORY:
                self.assertNotIn(word, question["text"].lower(), question["question_id"])

    def test_other_scenarios_are_not_referred(self) -> None:
        for case_id in ("SYN-001", "SYN-002", "SYN-004", "SYN-005"):
            with self.subTest(case_id=case_id):
                review = review_work_arrangement(RECORDS[case_id])
                self.assertEqual(review["referrals"], [])
                self.assertEqual(review["classification_status"], "no_referral")


class WorkArrangementFactTests(unittest.TestCase):
    def test_conflicting_contract_and_work_facts_are_structured_with_citations(self) -> None:
        record = with_facts(
            syn003(),
            ["DOC-ROSTER-003", "DOC-EMAIL-003"],
            [
                fact(
                    "direction_and_control", "worker_decides_methods", "contract", "CTR-SYN-003-01"
                ),
                fact(
                    "direction_and_control",
                    "business_sets_daily_roster",
                    "work_evidence",
                    "DOC-ROSTER-003",
                ),
                fact("tools_and_equipment", "business_supplies", "work_evidence", "DOC-EMAIL-003"),
            ],
        )
        review = review_work_arrangement(record)
        (referral,) = review["referrals"]
        (conflict,) = referral["conflicts"]
        self.assertEqual(conflict["factor"], "direction_and_control")
        self.assertEqual(
            conflict["values"], ["business_sets_daily_roster", "worker_decides_methods"]
        )
        self.assertEqual(conflict["evidence_ids"], ["CTR-SYN-003-01", "DOC-ROSTER-003"])
        self.assertEqual(factor_status(review)["tools_and_equipment"], "consistent")
        # Facts that point one way still do not classify.
        self.assertIsNone(referral["classification"]["value"])
        self.assertEqual(referral["classification"]["status"], CLASSIFICATION_STATUS)

    def test_conflict_alone_triggers_referral_even_if_contract_declares_a_classification(
        self,
    ) -> None:
        record = with_facts(
            syn003(),
            ["DOC-ROSTER-003"],
            [
                fact("exclusivity", "other_clients_allowed", "contract", "CTR-SYN-003-01"),
                fact(
                    "exclusivity", "works_only_for_business", "worker_statement", "DOC-ROSTER-003"
                ),
            ],
        )
        record["contracts"][0]["classification"] = "consultancy"
        review = review_work_arrangement(record)
        (referral,) = review["referrals"]
        self.assertEqual(referral["declared_classification"], "consultancy")
        self.assertIsNone(referral["classification"]["value"])

    def test_declared_classification_without_conflict_is_not_referred(self) -> None:
        record = syn003()
        record["contracts"][0]["classification"] = "consultancy"
        self.assertEqual(review_work_arrangement(record)["referrals"], [])

    def test_uncited_or_unsupplied_statements_are_not_facts(self) -> None:
        record = with_facts(
            syn003(),
            [],
            [
                fact("financial_risk", "worker_bears_risk", "taxpayer_statement"),
                fact("financial_risk", "business_bears_risk", "work_evidence", "DOC-NOT-SUPPLIED"),
            ],
        )
        review = review_work_arrangement(record)
        self.assertEqual(factor_status(review)["financial_risk"], "unknown")
        missing = [g for g in review["evidence_gaps"] if g["code"] == "cited_evidence_not_supplied"]
        self.assertEqual(missing[0]["missing_ids"], ["DOC-NOT-SUPPLIED"])

    def test_repeated_equal_invoices_describe_payment_basis_without_interpretation(self) -> None:
        record = syn003()
        record["invoices"].append(dict(record["invoices"][0], id="INV-SYN-003-02"))
        review = review_work_arrangement(record)
        (payment,) = [
            f for f in review["referrals"][0]["factors"] if f["factor"] == "payment_basis"
        ]
        (statement,) = payment["statements"]
        self.assertEqual(statement["value"], "repeated_equal_amounts")
        self.assertEqual(statement["source"], "transaction_records")
        self.assertEqual(statement["evidence_ids"], ["INV-SYN-003-01", "INV-SYN-003-02"])

    def test_unsafe_inputs_fail_closed_with_stable_codes(self) -> None:
        cases = {
            "invalid_record": [fact("vibes", "x", "contract", "CTR-SYN-003-01")],
            "unresolved_citation": [
                dict(fact("exclusivity", "x", "contract"), contract_id="CTR-NONE")
            ],
        }
        for code, facts in cases.items():
            with self.subTest(code=code):
                record = syn003()
                record["work_facts"] = facts
                with self.assertRaises(RefundDataError) as raised:
                    review_work_arrangement(record)
                self.assertEqual(raised.exception.code, code)
        record = syn003()
        record["work_facts"] = [fact("exclusivity", "Free Text!", "contract", "CTR-SYN-003-01")]
        with self.assertRaises(RefundDataError):
            review_work_arrangement(record)


if __name__ == "__main__":
    unittest.main()
