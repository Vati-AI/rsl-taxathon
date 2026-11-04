"""Deterministic decimal and lane checks for TAX-048 refund reconciliation (no models)."""

import copy
import json
import unittest
from decimal import Decimal
from pathlib import Path

from ai.tools.refund_reconciliation import (
    RefundDataError,
    parse_money,
    propose_refund_lane,
    reconcile_refund,
)

ROOT = Path(__file__).resolve().parents[2]
RECORDS = {
    record["case_id"]: record
    for record in json.loads((ROOT / "data/synthetic/tax024_records.json").read_text())
}
CASES = {case["id"]: case for case in json.loads((ROOT / "data/scenarios/cases.json").read_text())}
FORBIDDEN = ("abuse", "fraud", "evasion", "denial", "denied", "enforcement", "violation")


def record(case_id: str) -> dict:
    return copy.deepcopy(RECORDS[case_id])


def clean_record(*invoices: tuple[str, str, str], claim: str) -> dict:
    """A fully evidenced synthetic record built from (net, vat, gross) triples."""
    items, payments = [], []
    for index, (net, vat, gross) in enumerate(invoices, start=1):
        items.append(
            {
                "id": f"INV-T-{index}",
                "contract_id": "CTR-T",
                "counterparty_id": "SUP-T",
                "direction": "purchase",
                "net_amount": net,
                "vat_amount": vat,
                "gross_amount": gross,
            }
        )
        payments.append({"id": f"PAY-T-{index}", "invoice_id": f"INV-T-{index}", "amount": gross})
    return {
        "case_id": "SYN-TEST",
        "synthetic": True,
        "return": {
            "id": "RET-T",
            "output_vat": "0.00",
            "input_vat_claimed": claim,
            "refund_claim": claim,
        },
        "contracts": [{"id": "CTR-T", "counterparty_id": "SUP-T", "related_party": False}],
        "invoices": items,
        "payments": payments,
    }


def amounts(result: dict) -> tuple[str, str, str]:
    return (
        result["claimed_refund"]["amount"],
        result["explained_amount"]["amount"],
        result["unresolved_amount"]["amount"],
    )


def codes(result: dict, key: str) -> set[str]:
    return {item["code"] for item in result[key]}


class MoneyTests(unittest.TestCase):
    def test_parses_cents_exactly_and_normalises_to_two_places(self) -> None:
        self.assertEqual(parse_money("1.5", field="x"), Decimal("1.50"))
        self.assertEqual(str(parse_money("0", field="x")), "0.00")
        self.assertEqual(str(parse_money("66666.67", field="x")), "66666.67")

    def test_rejects_floats_sub_cent_negative_and_exotic_forms(self) -> None:
        for value in (
            0.1,
            15000.0,
            15000,
            True,
            None,
            "0.005",
            "-1.00",
            "1e3",
            " 1.00",
            "1,000.00",
            "01.00",
            "NaN",
            "Infinity",
            "",
        ):
            with self.subTest(value=value), self.assertRaises(RefundDataError) as caught:
                parse_money(value, field="x")
            self.assertEqual(caught.exception.code, "invalid_money")

    def test_decimal_sums_do_not_drift_like_binary_floats(self) -> None:
        self.assertNotEqual(0.1 + 0.1 + 0.1, 0.3)
        result = reconcile_refund(
            clean_record(
                ("1.00", "0.10", "1.10"),
                ("1.00", "0.10", "1.10"),
                ("1.00", "0.10", "1.10"),
                claim="0.30",
            )
        )
        self.assertEqual(amounts(result), ("0.30", "0.30", "0.00"))
        self.assertEqual(result["explained_share"], "1.0000")

    def test_one_cent_shortfall_is_unresolved_and_not_rounded_away(self) -> None:
        result = reconcile_refund(
            clean_record(("66666.67", "5999.99", "72666.66"), claim="6000.00")
        )
        self.assertEqual(amounts(result), ("6000.00", "5999.99", "0.01"))
        self.assertEqual(result["explained_share"], "1.0000")  # display only, 4 dp
        self.assertEqual(propose_refund_lane(result)["proposed_lane"], "standard")

    def test_money_is_rendered_as_tax018_lsl_money_objects(self) -> None:
        result = reconcile_refund(record("SYN-001"))
        for key in ("claimed_refund", "explained_amount", "unresolved_amount"):
            self.assertEqual(set(result[key]), {"amount", "currency"})
            self.assertEqual(result[key]["currency"], "LSL")
            self.assertRegex(result[key]["amount"], r"^(0|[1-9][0-9]*)\.[0-9]{2}$")

    def test_rejects_other_currency_records_and_invalid_codes(self) -> None:
        mixed = record("SYN-001") | {"currency": "ZAR"}
        with self.assertRaises(RefundDataError) as caught:
            reconcile_refund(mixed)
        self.assertEqual(caught.exception.code, "currency_mismatch")
        with self.assertRaises(RefundDataError):
            reconcile_refund(record("SYN-001"), currency="lsl")


class ReconciliationTests(unittest.TestCase):
    def test_legitimate_fully_evidenced_claim_is_fast_and_never_labelled_abuse(self) -> None:
        result = reconcile_refund(record("SYN-001"))
        self.assertEqual(amounts(result), ("15000.00", "15000.00", "0.00"))
        self.assertEqual(result["gaps"] + result["discrepancies"] + result["indicators"], [])
        self.assertEqual(
            result["evidence_ids"],
            [
                "CTR-SYN-001-01",
                "INV-SYN-001-01",
                "INV-SYN-001-02",
                "PAY-SYN-001-01",
                "PAY-SYN-001-02",
                "RET-SYN-001",
            ],
        )
        proposal = propose_refund_lane(result)
        self.assertEqual(proposal["proposed_lane"], "fast")
        # The fixed disclaimer names what the proposal is not; every other field is checked.
        text = json.dumps({k: v for k, v in proposal.items() if k != "notice"}).lower()
        for word in FORBIDDEN:
            self.assertNotIn(word, text)

    def test_missing_invoice_evidence_is_a_gap_not_a_discrepancy(self) -> None:
        result = reconcile_refund(record("SYN-004"))
        self.assertEqual(amounts(result), ("5000.00", "2000.00", "3000.00"))
        self.assertEqual(result["discrepancies"], [])
        gap = next(
            g for g in result["gaps"] if g["code"] == "refund_claim_exceeds_documented_input_vat"
        )
        self.assertEqual(gap["amount"], {"amount": "3000.00", "currency": "LSL"})
        self.assertIn("imported_service_treatment_unverified", codes(result, "indicators"))

    def test_missing_payment_or_contract_moves_invoice_out_of_explained(self) -> None:
        data = record("SYN-001")
        data["payments"] = data["payments"][:1]
        data["invoices"][0]["contract_id"] = "CTR-MISSING"
        result = reconcile_refund(data)
        self.assertEqual(amounts(result), ("15000.00", "0.00", "15000.00"))
        self.assertEqual(
            [(i["invoice_id"], i["status"], i["reasons"]) for i in result["line_items"]],
            [
                ("INV-SYN-001-01", "missing_evidence", ["contract_evidence_missing"]),
                ("INV-SYN-001-02", "missing_evidence", ["payment_evidence_missing"]),
            ],
        )
        self.assertEqual(result["discrepancies"], [])
        self.assertEqual(
            codes(result, "gaps"), {"contract_evidence_missing", "payment_evidence_missing"}
        )
        self.assertEqual(propose_refund_lane(result)["proposed_lane"], "targeted")

    def test_partial_missing_evidence_below_materiality_is_standard(self) -> None:
        data = record("SYN-005")
        data.pop("economic_context")
        data["payments"] = data["payments"][:1]  # INV-SYN-005-02 (LSL 3,000.00) unpaid
        result = reconcile_refund(data)
        self.assertEqual(amounts(result), ("8000.00", "5000.00", "3000.00"))
        self.assertEqual(propose_refund_lane(result)["proposed_lane"], "standard")

    def test_invoice_and_payment_mismatches_are_discrepancies_and_targeted(self) -> None:
        data = record("SYN-001")
        data["invoices"][0]["gross_amount"] = "109000.01"
        data["payments"][0]["amount"] = "109000.01"
        data["payments"][1]["amount"] = "72000.00"
        result = reconcile_refund(data)
        self.assertEqual(amounts(result), ("15000.00", "0.00", "15000.00"))
        self.assertEqual(
            codes(result, "discrepancies"),
            {"invoice_total_mismatch", "payment_total_differs_from_invoice"},
        )
        self.assertEqual(result["gaps"], [])
        self.assertEqual(propose_refund_lane(result)["proposed_lane"], "targeted")

    def test_return_arithmetic_mismatch_and_orphan_payment_are_discrepancies(self) -> None:
        data = record("SYN-001")
        data["return"]["refund_claim"] = "15000.01"
        data["payments"].append({"id": "PAY-ORPHAN", "invoice_id": "INV-NONE", "amount": "1.00"})
        result = reconcile_refund(data)
        self.assertEqual(
            codes(result, "discrepancies"),
            {"return_arithmetic_mismatch", "payment_without_invoice"},
        )
        self.assertEqual(result["unresolved_amount"]["amount"], "0.01")
        self.assertEqual(propose_refund_lane(result)["proposed_lane"], "targeted")

    def test_output_vat_reduces_supported_refund(self) -> None:
        data = record("SYN-001")
        data["return"].update(output_vat="1000.00", refund_claim="14000.00")
        result = reconcile_refund(data)
        self.assertEqual(amounts(result), ("14000.00", "14000.00", "0.00"))

    def test_documents_above_claim_do_not_explain_more_than_claimed(self) -> None:
        data = record("SYN-001")
        data["return"].update(input_vat_claimed="10000.00", refund_claim="10000.00")
        self.assertEqual(amounts(reconcile_refund(data)), ("10000.00", "10000.00", "0.00"))

    def test_malformed_records_fail_closed_with_stable_codes(self) -> None:
        duplicate = record("SYN-001")
        duplicate["invoices"].append(dict(duplicate["invoices"][0]))
        float_amount = record("SYN-001")
        float_amount["invoices"][0]["vat_amount"] = 9000.0
        no_return = record("SYN-001")
        no_return.pop("return")
        for data, code in (
            (duplicate, "duplicate_record_id"),
            (float_amount, "invalid_money"),
            (no_return, "invalid_record"),
        ):
            with self.subTest(code=code), self.assertRaises(RefundDataError) as caught:
                reconcile_refund(data)
            self.assertEqual(caught.exception.code, code)

    def test_reconciliation_is_deterministic_and_does_not_mutate_input(self) -> None:
        data = record("SYN-002")
        before = copy.deepcopy(data)
        self.assertEqual(reconcile_refund(data), reconcile_refund(data))
        self.assertEqual(data, before)


class Tax024ScenarioTests(unittest.TestCase):
    EXPECTED_LANES = {
        "SYN-001": "fast",
        "SYN-002": "targeted",
        "SYN-003": None,
        "SYN-004": "targeted",
        "SYN-005": "standard",
    }

    def test_every_scenario_matches_independent_supported_refund(self) -> None:
        for case_id, data in RECORDS.items():
            with self.subTest(case_id=case_id):
                result = reconcile_refund(data)
                case = CASES[case_id]
                claim, supported = Decimal(case["refund_claim"]), Decimal(case["supported_refund"])
                self.assertEqual(result["claimed_refund"]["amount"], case["refund_claim"])
                self.assertEqual(result["explained_amount"]["amount"], case["supported_refund"])
                self.assertEqual(Decimal(result["unresolved_amount"]["amount"]), claim - supported)

    def test_every_scenario_lane_is_an_advisory_proposal_without_a_decision(self) -> None:
        for case_id, data in RECORDS.items():
            with self.subTest(case_id=case_id):
                proposal = propose_refund_lane(reconcile_refund(data))
                self.assertEqual(proposal["proposed_lane"], self.EXPECTED_LANES[case_id])
                self.assertEqual(proposal["status"], "proposed")
                self.assertIs(proposal["advisory"], True)
                self.assertIs(proposal["requires_officer_approval"], True)
                self.assertIsNone(proposal["decision"])
                self.assertIsNone(proposal["approved_by"])
                reason_codes = {r["code"] for r in proposal["reasons"]}
                for forbidden in data["expected_non_findings"]:
                    self.assertNotIn(forbidden, reason_codes)

    def test_scenario_findings_surface_the_expected_codes(self) -> None:
        def all_codes(case_id: str) -> set[str]:
            result = reconcile_refund(RECORDS[case_id])
            return (
                codes(result, "gaps") | codes(result, "indicators") | codes(result, "discrepancies")
            )

        self.assertEqual(all_codes("SYN-001"), set())
        self.assertEqual(
            all_codes("SYN-002"),
            {
                "related_party_transaction",
                "refund_claim_exceeds_documented_input_vat",
                "consecutive_loss_periods",
            },
        )
        self.assertEqual(all_codes("SYN-003"), {"service_classification_evidence_gap"})
        self.assertEqual(
            all_codes("SYN-004"),
            {"refund_claim_exceeds_documented_input_vat", "imported_service_treatment_unverified"},
        )
        self.assertEqual(all_codes("SYN-005"), {"economic_context_requires_human_policy_review"})

    def test_related_party_evidence_is_cited_without_asserting_pricing_or_intent(self) -> None:
        result = reconcile_refund(RECORDS["SYN-002"])
        related = next(i for i in result["indicators"] if i["code"] == "related_party_transaction")
        self.assertIn("CTR-SYN-002-01", related["evidence_ids"])
        self.assertIn("INV-SYN-002-01", related["evidence_ids"])
        self.assertNotIn("INV-SYN-002-02", related["evidence_ids"])


class LanePolicyTests(unittest.TestCase):
    def test_materiality_threshold_boundary_is_exact(self) -> None:
        half = reconcile_refund(clean_record(("10.00", "1.00", "11.00"), claim="2.00"))
        self.assertEqual(half["unresolved_amount"]["amount"], "1.00")
        self.assertEqual(propose_refund_lane(half)["proposed_lane"], "standard")  # 50% is "most"
        over = reconcile_refund(clean_record(("9.99", "0.99", "10.98"), claim="2.00"))
        self.assertEqual(propose_refund_lane(over)["proposed_lane"], "targeted")
        strict = propose_refund_lane(half, max_unresolved_share=Decimal("0.25"))
        self.assertEqual(strict["proposed_lane"], "targeted")
        self.assertEqual(strict["thresholds"], {"max_unresolved_share_for_standard": "0.25"})

    def test_rejects_float_or_out_of_range_thresholds(self) -> None:
        result = reconcile_refund(RECORDS["SYN-001"])
        for bad in (0.5, Decimal("1.01"), Decimal("-0.01")):
            with self.subTest(bad=bad), self.assertRaises(RefundDataError):
                propose_refund_lane(result, max_unresolved_share=bad)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
