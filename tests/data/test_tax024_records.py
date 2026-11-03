"""Structural and decimal checks for the independent TAX-024 scenario fixtures."""

import json
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORDS_PATH = ROOT / "data/synthetic/tax024_records.json"
CASES_PATH = ROOT / "data/scenarios/cases.json"


def money(value: str) -> Decimal:
    if not isinstance(value, str):
        raise AssertionError(f"Money must be a decimal string, got {value!r}")
    amount = Decimal(value)
    if amount.as_tuple().exponent != -2:
        raise AssertionError(f"Money must have two decimal places, got {value!r}")
    return amount


class Tax024RecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.records = json.loads(RECORDS_PATH.read_text(encoding="utf-8"))
        cls.cases = {case["id"]: case for case in json.loads(CASES_PATH.read_text(encoding="utf-8"))}

    def test_all_five_scenarios_have_linked_records_and_expected_outcomes(self) -> None:
        self.assertEqual({record["case_id"] for record in self.records}, set(self.cases))
        all_ids = []
        for record in self.records:
            with self.subTest(case=record["case_id"]):
                case_id = record["case_id"]
                case = self.cases[case_id]
                self.assertIs(record["synthetic"], True)
                self.assertTrue(record["taxpayer_id"].startswith("SYN-TAXPAYER-"))
                self.assertEqual(case["synthetic"], True)
                self.assertEqual(record["expected_outcome"], case["expected"])

                documents = [record["return"], *record["contracts"], *record["invoices"], *record["payments"]]
                ids = [document["id"] for document in documents]
                all_ids.extend(ids)
                self.assertEqual(len(ids), len(set(ids)), "record IDs must be unique within the fixture")
                self.assertTrue(all(document_id.startswith(("RET-", "CTR-", "INV-", "PAY-")) for document_id in ids))

                contracts = {contract["id"]: contract for contract in record["contracts"]}
                invoices = {invoice["id"]: invoice for invoice in record["invoices"]}
                self.assertTrue(all(invoice["contract_id"] in contracts for invoice in record["invoices"]))
                self.assertTrue(all(payment["invoice_id"] in invoices for payment in record["payments"]))
                for invoice in record["invoices"]:
                    self.assertEqual(invoice["counterparty_id"], contracts[invoice["contract_id"]]["counterparty_id"])

                for invoice in record["invoices"]:
                    net = money(invoice["net_amount"])
                    vat = money(invoice["vat_amount"])
                    gross = money(invoice["gross_amount"])
                    self.assertEqual(net + vat, gross, "invoice gross must reconcile")
                for payment in record["payments"]:
                    self.assertLessEqual(money(payment["amount"]), money(invoices[payment["invoice_id"]]["gross_amount"]))
                for invoice_id, invoice in invoices.items():
                    paid = sum(
                        (money(payment["amount"]) for payment in record["payments"] if payment["invoice_id"] == invoice_id),
                        Decimal("0.00"),
                    )
                    self.assertEqual(paid, money(invoice["gross_amount"]), "payments must reconcile to invoice gross")

                input_vat = sum(
                    (money(invoice["vat_amount"]) for invoice in record["invoices"] if invoice["direction"] == "purchase"),
                    Decimal("0.00"),
                )
                output_vat = sum(
                    (money(invoice["vat_amount"]) for invoice in record["invoices"] if invoice["direction"] == "sale"),
                    Decimal("0.00"),
                )
                tax_return = record["return"]
                claimed_input = money(tax_return["input_vat_claimed"])
                returned_output = money(tax_return["output_vat"])
                refund = money(tax_return["refund_claim"])
                self.assertEqual(refund, max(claimed_input - returned_output, Decimal("0.00")))
                self.assertEqual(refund, money(case["refund_claim"]))
                self.assertEqual(input_vat, money(case["supported_refund"]))
                self.assertEqual(output_vat, returned_output)
                self.assertTrue(record["expected_findings"])
                self.assertTrue(record["expected_non_findings"])
                evidence_ids = {
                    evidence_id
                    for finding in record["expected_findings"]
                    for evidence_id in finding["evidence_ids"]
                }
                self.assertTrue(evidence_ids.issubset(set(ids)))
        self.assertEqual(len(all_ids), len(set(all_ids)), "record IDs must be globally unique")

    def test_unverified_policy_gap_does_not_claim_a_legal_violation(self) -> None:
        record = next(item for item in self.records if item["case_id"] == "SYN-005")
        self.assertEqual(record["economic_context"]["legal_route"], "unverified")
        self.assertIn("legal_violation_established", record["expected_non_findings"])
        self.assertIn("enforcement_recommended", record["expected_non_findings"])

    def test_ambiguous_service_case_preserves_both_unresolved_explanations(self) -> None:
        record = next(item for item in self.records if item["case_id"] == "SYN-003")
        self.assertEqual(record["contracts"][0]["classification"], "unresolved")
        self.assertIn("employment_relationship_established", record["expected_non_findings"])
        self.assertIn("consultancy_misclassification", record["expected_non_findings"])


if __name__ == "__main__":
    unittest.main()
