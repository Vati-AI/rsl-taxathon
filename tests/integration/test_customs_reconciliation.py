"""Behaviour tests for TAX-073 tax/customs multi-value reconciliation (no models, no network)."""

import copy
import json
import random
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any

from ai.tools.customs_reconciliation import (
    CustomsDataError,
    convert,
    parse_rate,
    reconcile_customs_values,
)
from ai.workflows.customs_values import (
    CUSTOMS_TOOL_NAME,
    customs_records_source,
    make_customs_reconciliation_tool,
)

ROOT = Path(__file__).resolve().parents[2]
RECORDS = json.loads((ROOT / "data/synthetic/tax073_customs_records.json").read_text())
SCENARIO_IDS = {case["id"] for case in json.loads((ROOT / "data/scenarios/cases.json").read_text())}
FORBIDDEN = ("fraud", "false_declaration", "misdeclar", "evasion", "abuse", "penalty", "violation")
TENANT_A = "tenant-synthetic-a"
TENANT_B = "tenant-synthetic-b"


def record(case_id: str) -> dict[str, Any]:
    return copy.deepcopy(next(r for r in RECORDS if r["case_id"] == case_id))


def single(*values: dict[str, Any], rates: list | None = None, adjustments: list | None = None):
    """A one-transaction synthetic record."""
    return {
        "case_id": "SYN-TEST",
        "synthetic": True,
        "exchange_rates": rates or [],
        "transactions": [{"id": "IMP-T", "values": list(values), "adjustments": adjustments or []}],
    }


def value(vid: str, basis: str, amount: str, currency: str = "LSL", **extra: Any) -> dict:
    return {
        "id": vid,
        "basis": basis,
        "amount": amount,
        "currency": currency,
        "date": extra.pop("date", "2025-10-01"),
        **extra,
    }


def rate(rid: str, currency: str, value_: str, date: str = "2025-10-01") -> dict:
    return {
        "id": rid,
        "from_currency": currency,
        "to_currency": "LSL",
        "rate": value_,
        "date": date,
    }


def statuses(result: dict) -> dict[str, dict[str, str]]:
    return {
        t["transaction_id"]: {c["basis"]: c["status"] for c in t["comparisons"]}
        for t in result["transactions"]
    }


def codes(result: dict, key: str) -> set[str]:
    return {item["code"] for item in result[key]}


def walk_strings(node: object):
    if isinstance(node, dict):
        for k, v in node.items():
            yield str(k)
            yield from walk_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk_strings(v)
    elif isinstance(node, str):
        yield node


class SyntheticScenarioTests(unittest.TestCase):
    def test_records_are_synthetic_and_tied_to_tax024_scenarios(self) -> None:
        for rec in RECORDS:
            self.assertIs(rec["synthetic"], True)
            self.assertIn("Synthetic", rec["synthetic_notice"])
            self.assertIn(rec["case_id"], SCENARIO_IDS)

    def test_each_scenario_matches_its_documented_expectation(self) -> None:
        for rec in RECORDS:
            with self.subTest(case=rec["case_id"]):
                result = reconcile_customs_values(rec)
                expected = rec["expected"]
                self.assertEqual(statuses(result), expected["statuses"])
                self.assertEqual(sorted(codes(result, "gaps")), expected["gap_codes"])
                self.assertEqual(
                    sorted(codes(result, "discrepancies")), expected["discrepancy_codes"]
                )
                self.assertIs(result["synthetic"], True)

    def test_legitimate_import_keeps_every_basis_distinct_and_fully_bridged(self) -> None:
        result = reconcile_customs_values(record("SYN-001"))
        txn = result["transactions"][0]
        totals = {b["basis"]: b for b in txn["basis_totals"]}
        # Four different bases, four different numbers: never merged into one.
        self.assertEqual(
            totals["supplier_invoice"]["reporting_amount"],
            {"amount": "182500.00", "currency": "LSL"},
        )
        self.assertEqual(
            totals["customs_value"]["reporting_amount"], {"amount": "198012.50", "currency": "LSL"}
        )
        self.assertEqual(
            totals["payment"]["reporting_amount"], {"amount": "186000.00", "currency": "LSL"}
        )
        self.assertEqual(totals["customs_value"]["valuation_bases"], ["CIF"])
        self.assertEqual(totals["supplier_invoice"]["valuation_bases"], ["FOB"])
        customs = next(c for c in txn["comparisons"] if c["basis"] == "customs_value")
        self.assertEqual(customs["compared_in"], "USD")
        self.assertEqual(customs["difference"], {"amount": "850.00", "currency": "USD"})
        self.assertEqual(customs["residual"], {"amount": "0.00", "currency": "USD"})
        self.assertEqual(customs["adjustment_ids"], ["ADJ-SYN-001-FRT", "ADJ-SYN-001-INS"])
        self.assertIn("ADJ-SYN-001-FRT", customs["evidence_ids"])
        self.assertEqual(result["discrepancies"], [])
        self.assertEqual(result["gaps"], [])

    def test_payment_date_exchange_movement_is_context_not_a_difference(self) -> None:
        result = reconcile_customs_values(record("SYN-001"))
        payment = next(
            c for c in result["transactions"][0]["comparisons"] if c["basis"] == "payment"
        )
        self.assertEqual(payment["status"], "matched")
        self.assertEqual(payment["reporting_difference"], {"amount": "3500.00", "currency": "LSL"})
        timing = [c for c in result["context"] if c["code"] == "exchange_rate_timing_difference"]
        self.assertEqual(len(timing), 1)
        self.assertIn("FX-SYN-001-USD-20251114", timing[0]["evidence_ids"])

    def test_every_cited_evidence_id_resolves_to_a_supplied_record(self) -> None:
        for rec in RECORDS:
            supplied = {r["id"] for r in rec["exchange_rates"]}
            for txn in rec["transactions"]:
                supplied.add(txn["id"])
                supplied.update(v["id"] for v in txn["values"])
                supplied.update(a["id"] for a in txn.get("adjustments", []))
            result = reconcile_customs_values(rec)
            for key in ("gaps", "discrepancies", "context"):
                for item in result[key]:
                    with self.subTest(case=rec["case_id"], code=item["code"]):
                        self.assertTrue(item["evidence_ids"])
                        self.assertLessEqual(set(item["evidence_ids"]), supplied)

    def test_output_is_advisory_and_never_labels_misdeclaration(self) -> None:
        for rec in RECORDS:
            result = reconcile_customs_values(rec)
            self.assertIs(result["advisory"], True)
            self.assertIsNone(result["decision"])
            self.assertIs(result["requires_officer_review"], True)
            flat = " ".join(walk_strings({k: v for k, v in result.items() if k != "notice"}))
            for word in FORBIDDEN:
                self.assertNotIn(word, flat.lower())


class MissingSourceTests(unittest.TestCase):
    def test_missing_customs_record_is_incomplete_data_not_a_discrepancy(self) -> None:
        result = reconcile_customs_values(record("SYN-004"))
        self.assertEqual(result["discrepancies"], [])
        (gap,) = result["gaps"]
        self.assertEqual(gap["code"], "customs_value_record_missing")
        self.assertEqual(gap["interpretation"], "incomplete_data")

    def test_missing_exchange_rate_is_never_guessed(self) -> None:
        result = reconcile_customs_values(
            single(
                value("I", "supplier_invoice", "100.00", "USD", valuation_basis="FOB"),
                value("C", "customs_value", "1800.00", "LSL", valuation_basis="FOB"),
            )
        )
        comparison = result["transactions"][0]["comparisons"][0]
        self.assertEqual(comparison["status"], "not_comparable")
        self.assertIsNone(comparison["difference"])
        self.assertIsNone(result["transactions"][0]["values"][0]["reporting_amount"])
        self.assertIn("exchange_rate_missing", codes(result, "gaps"))
        self.assertEqual(result["discrepancies"], [])

    def test_cited_rate_that_was_not_supplied_is_a_gap(self) -> None:
        result = reconcile_customs_values(
            single(value("I", "supplier_invoice", "100.00", "USD", rate_id="FX-ABSENT"))
        )
        gap = next(g for g in result["gaps"] if g["code"] == "exchange_rate_missing")
        self.assertEqual(gap["cited_rate_id"], "FX-ABSENT")

    def test_partial_payments_are_partially_evidenced_not_discrepant(self) -> None:
        result = reconcile_customs_values(
            single(
                value("I", "supplier_invoice", "1000.00"),
                value("P", "payment", "400.00"),
            )
        )
        self.assertEqual(statuses(result)["IMP-T"]["payment"], "partially_evidenced")
        self.assertIn("payment_evidence_incomplete", codes(result, "gaps"))
        self.assertEqual(result["discrepancies"], [])

    def test_customs_without_valuation_basis_needs_bridging_evidence(self) -> None:
        result = reconcile_customs_values(
            single(
                value("I", "supplier_invoice", "1000.00", valuation_basis="FOB"),
                value("C", "customs_value", "1100.00"),
            )
        )
        comparison = result["transactions"][0]["comparisons"][0]
        self.assertEqual(comparison["status"], "basis_difference_unreconciled")
        self.assertEqual(comparison["reason"], "valuation_basis_missing")
        self.assertEqual(result["discrepancies"], [])

    def test_no_transactions_is_a_gap(self) -> None:
        result = reconcile_customs_values({"case_id": "SYN-TEST", "transactions": []})
        self.assertEqual(codes(result, "gaps"), {"no_import_transactions_supplied"})


class ContradictionTests(unittest.TestCase):
    def test_same_basis_values_beyond_tolerance_are_a_discrepancy_for_review(self) -> None:
        result = reconcile_customs_values(record("SYN-002"))
        diff = next(
            d for d in result["discrepancies"] if d["code"] == "values_differ_beyond_tolerance"
        )
        self.assertIs(diff["requires_officer_review"], True)
        self.assertEqual(diff["residual"], {"amount": "-20000.00", "currency": "ZAR"})
        self.assertEqual(
            diff["evidence_ids"], ["CINV-SYN-002-01", "CUS-SYN-002-01", "FX-SYN-002-ZAR-20251110"]
        )

    def test_transfer_pricing_value_stays_a_distinct_legitimate_basis(self) -> None:
        result = reconcile_customs_values(record("SYN-002"))
        tp = next(
            c
            for c in result["transactions"][0]["comparisons"]
            if c["basis"] == "transfer_pricing_value"
        )
        self.assertEqual(tp["status"], "basis_difference_unreconciled")
        self.assertEqual(tp["reason"], "transfer_pricing_basis")
        flagged = {e for d in result["discrepancies"] for e in d["evidence_ids"]}
        self.assertNotIn("TP-SYN-002-01", flagged)

    def test_conflicting_copies_of_one_reference_are_not_summed(self) -> None:
        result = reconcile_customs_values(record("SYN-002"))
        totals = {b["basis"]: b for b in result["transactions"][0]["basis_totals"]}
        self.assertIs(totals["payment"]["contradicted"], True)
        self.assertIsNone(totals["payment"]["transaction_amount"])
        conflict = next(
            d
            for d in result["discrepancies"]
            if d["code"] == "conflicting_values_for_same_reference"
        )
        self.assertEqual(conflict["evidence_ids"], ["CPAY-SYN-002-01", "CPAY-SYN-002-01-ALT"])

    def test_identical_copies_of_one_reference_count_once(self) -> None:
        result = reconcile_customs_values(record("SYN-001"))
        totals = {b["basis"]: b for b in result["transactions"][1]["basis_totals"]}
        self.assertEqual(
            totals["customs_value"]["transaction_amount"], {"amount": "50000.00", "currency": "ZAR"}
        )
        self.assertIn("duplicate_reference_counted_once", codes(result, "context"))

    def test_booked_conversion_that_disagrees_with_its_cited_rate(self) -> None:
        result = reconcile_customs_values(
            single(
                value(
                    "A",
                    "accounting_value",
                    "1850.00",
                    original_amount="100.00",
                    original_currency="USD",
                    rate_id="FX-1",
                ),
                rates=[rate("FX-1", "USD", "18.000000")],
            )
        )
        (item,) = result["discrepancies"]
        self.assertEqual(item["code"], "booked_conversion_inconsistent")
        self.assertEqual(item["expected"], {"amount": "1800.00", "currency": "LSL"})
        self.assertEqual(item["evidence_ids"], ["A", "FX-1"])

    def test_conflicting_same_day_rates_are_not_resolved_by_picking_one(self) -> None:
        result = reconcile_customs_values(
            single(
                value("I", "supplier_invoice", "100.00", "USD"),
                rates=[rate("FX-1", "USD", "18.000000"), rate("FX-2", "USD", "18.500000")],
            )
        )
        self.assertEqual(codes(result, "discrepancies"), {"conflicting_exchange_rates"})
        self.assertIsNone(result["transactions"][0]["values"][0]["reporting_amount"])

    def test_cited_rate_for_another_currency_is_a_discrepancy(self) -> None:
        result = reconcile_customs_values(
            single(
                value("I", "supplier_invoice", "100.00", "USD", rate_id="FX-Z"),
                rates=[rate("FX-Z", "ZAR", "1.000000")],
            )
        )
        self.assertEqual(codes(result, "discrepancies"), {"exchange_rate_currency_mismatch"})


class DecimalAndToleranceTests(unittest.TestCase):
    def test_conversion_rounds_half_up_to_cents(self) -> None:
        self.assertEqual(convert(Decimal("0.01"), Decimal("0.5")), Decimal("0.01"))
        self.assertEqual(convert(Decimal("10.00"), Decimal("18.123456")), Decimal("181.23"))
        self.assertEqual(convert(Decimal("33.33"), Decimal("1.000015")), Decimal("33.33"))

    def test_rates_and_amounts_reject_floats_and_malformed_strings(self) -> None:
        for bad in (18.25, "1e2", "-1.0", "0", "0.000000", "1.1234567", " 1.0", True):
            with self.subTest(rate=bad), self.assertRaises(CustomsDataError):
                parse_rate(bad, field_name="rate")
        for bad_amount in (100.0, "1,000.00", "10.001", "-5.00"):
            with self.subTest(amount=bad_amount), self.assertRaises(CustomsDataError) as ctx:
                reconcile_customs_values(single(value("I", "supplier_invoice", bad_amount)))
            self.assertEqual(ctx.exception.code, "invalid_money")

    def test_tolerance_boundary_is_inclusive_and_configurable(self) -> None:
        def run(customs: str, tolerance: str = "1.00") -> str:
            result = reconcile_customs_values(
                single(
                    value("I", "supplier_invoice", "1000.00", valuation_basis="FOB"),
                    value("C", "customs_value", customs, valuation_basis="FOB"),
                ),
                tolerance=Decimal(tolerance),
            )
            return result["transactions"][0]["comparisons"][0]["status"]

        self.assertEqual(run("1001.00"), "matched")
        self.assertEqual(run("998.99"), "difference_unexplained")
        self.assertEqual(run("1000.00", "0.00"), "matched")
        self.assertEqual(run("1000.01", "0.00"), "difference_unexplained")
        self.assertEqual(run("1005.00", "5.00"), "matched")

    def test_invalid_tolerance_is_rejected(self) -> None:
        for bad in (1.0, Decimal("-0.01"), Decimal("0.001"), Decimal("NaN"), True):
            with self.subTest(tolerance=bad), self.assertRaises(CustomsDataError) as ctx:
                reconcile_customs_values(single(), tolerance=bad)  # type: ignore[arg-type]
            self.assertEqual(ctx.exception.code, "invalid_policy")

    def test_reverse_direction_adjustment_bridges_both_ways(self) -> None:
        result = reconcile_customs_values(
            single(
                value("I", "supplier_invoice", "1100.00", valuation_basis="CIF"),
                value("C", "customs_value", "1000.00", valuation_basis="FOB"),
                adjustments=[
                    {
                        "id": "ADJ-1",
                        "kind": "freight",
                        "from_basis": "customs_value",
                        "to_basis": "supplier_invoice",
                        "effect": "add",
                        "amount": "100.00",
                        "currency": "LSL",
                        "date": "2025-10-01",
                    }
                ],
            )
        )
        comparison = result["transactions"][0]["comparisons"][0]
        self.assertEqual(comparison["status"], "matched_with_adjustments")
        self.assertEqual(comparison["adjustments_total"], {"amount": "-100.00", "currency": "LSL"})

    def test_mixed_currency_comparison_uses_reporting_conversions(self) -> None:
        result = reconcile_customs_values(
            single(
                value("I", "supplier_invoice", "100.00", "USD", valuation_basis="FOB"),
                value("C", "customs_value", "1825.00", "LSL", valuation_basis="FOB"),
                rates=[rate("FX-1", "USD", "18.250000")],
            )
        )
        comparison = result["transactions"][0]["comparisons"][0]
        self.assertEqual(comparison["compared_in"], "LSL")
        self.assertEqual(comparison["status"], "matched")
        invoice = result["transactions"][0]["values"][0]
        self.assertEqual(invoice["exchange_rate"]["rate_id"], "FX-1")
        self.assertEqual(invoice["evidence_ids"], ["I", "FX-1"])


class ValidationTests(unittest.TestCase):
    def test_structural_errors_have_stable_codes(self) -> None:
        cases = {
            "duplicate_record_id": single(
                value("X", "supplier_invoice", "1.00"), value("X", "payment", "1.00")
            ),
            "invalid_basis": single(value("I", "guess_value", "1.00")),
            "invalid_date": single(value("I", "payment", "1.00", date="2025-02-30")),
            "invalid_currency": single(value("I", "payment", "1.00", "usd")),
            "unsupported_exchange_rate": single(
                rates=[{**rate("FX", "USD", "1.0"), "to_currency": "ZAR"}]
            ),
            "invalid_adjustment": single(
                value("I", "supplier_invoice", "1.00"),
                adjustments=[
                    {
                        "id": "ADJ",
                        "kind": "freight",
                        "from_basis": "supplier_invoice",
                        "to_basis": "customs_value",
                        "effect": "multiply",
                        "amount": "1.00",
                        "currency": "LSL",
                        "date": "2025-10-01",
                    }
                ],
            ),
        }
        for code, rec in cases.items():
            with self.subTest(code=code), self.assertRaises(CustomsDataError) as ctx:
                reconcile_customs_values(rec)
            self.assertEqual(ctx.exception.code, code)

    def test_reporting_currency_mismatch_is_rejected(self) -> None:
        with self.assertRaises(CustomsDataError) as ctx:
            reconcile_customs_values({**record("SYN-001"), "reporting_currency": "ZAR"})
        self.assertEqual(ctx.exception.code, "currency_mismatch")


class ReplayDeterminismTests(unittest.TestCase):
    def test_replay_is_byte_identical_and_does_not_mutate_input(self) -> None:
        for rec in RECORDS:
            before = json.dumps(rec, sort_keys=True)
            first = json.dumps(reconcile_customs_values(rec), sort_keys=True)
            second = json.dumps(reconcile_customs_values(copy.deepcopy(rec)), sort_keys=True)
            self.assertEqual(first, second)
            self.assertEqual(json.dumps(rec, sort_keys=True), before)

    def test_input_order_does_not_change_the_result(self) -> None:
        shuffler = random.Random(73)
        for rec in RECORDS:
            baseline = json.dumps(reconcile_customs_values(rec), sort_keys=True)
            for _ in range(5):
                shuffled = copy.deepcopy(rec)
                shuffler.shuffle(shuffled["exchange_rates"])
                shuffler.shuffle(shuffled["transactions"])
                for txn in shuffled["transactions"]:
                    shuffler.shuffle(txn["values"])
                    shuffler.shuffle(txn.get("adjustments", []))
                with self.subTest(case=rec["case_id"]):
                    self.assertEqual(
                        json.dumps(reconcile_customs_values(shuffled), sort_keys=True), baseline
                    )

    def test_amounts_are_strings_not_floats(self) -> None:
        result = reconcile_customs_values(record("SYN-001"))

        def no_floats(node: object) -> None:
            self.assertNotIsInstance(node, float)
            if isinstance(node, dict):
                for v in node.values():
                    no_floats(v)
            elif isinstance(node, list):
                for v in node:
                    no_floats(v)

        no_floats(result)


class CaseScopedToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records = {(TENANT_A, r["case_id"]): copy.deepcopy(r) for r in RECORDS}
        self.tool = make_customs_reconciliation_tool(customs_records_source(self.records))

    def context(self, case_id: str, tenant_id: str = TENANT_A) -> dict:
        return {
            "tenant_id": tenant_id,
            "case_id": case_id,
            "run_id": f"run-{case_id}",
            "correlation_id": f"corr-{case_id}",
            "evidence_ids": [],
        }

    def test_tool_returns_scoped_envelope(self) -> None:
        result = self.tool(self.context("SYN-001"))  # type: ignore[arg-type]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["tool_name"], CUSTOMS_TOOL_NAME)
        self.assertEqual((result["tenant_id"], result["case_id"]), (TENANT_A, "SYN-001"))
        self.assertEqual(result["data"]["case_ref"], "SYN-001")

    def test_other_tenant_cannot_resolve_same_case_reference(self) -> None:
        result = self.tool(self.context("SYN-001", TENANT_B))  # type: ignore[arg-type]
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "customs_record_not_found")
        self.assertNotIn("data", result)

    def test_record_for_another_case_is_refused(self) -> None:
        self.records[(TENANT_A, "SYN-005")] = record("SYN-002")
        result = self.tool(self.context("SYN-005"))  # type: ignore[arg-type]
        self.assertEqual(result["error"]["code"], "tool_scope_mismatch")

    def test_invalid_record_fails_with_code_and_no_record_content(self) -> None:
        bad = record("SYN-004")
        bad["transactions"][0]["values"][0]["amount"] = 24222.22
        self.records[(TENANT_A, "SYN-004")] = bad
        result = self.tool(self.context("SYN-004"))  # type: ignore[arg-type]
        self.assertEqual(result["error"]["code"], "invalid_money")
        self.assertNotIn("24222", json.dumps(result))

    def test_tenants_with_colliding_case_ids_get_their_own_records(self) -> None:
        other = record("SYN-004")
        other["transactions"][0]["values"][0]["amount"] = "1.00"
        self.records[(TENANT_B, "SYN-004")] = other
        a = self.tool(self.context("SYN-004"))  # type: ignore[arg-type]
        b = self.tool(self.context("SYN-004", TENANT_B))  # type: ignore[arg-type]
        self.assertEqual(a["data"]["discrepancies"], [])
        self.assertIn("values_differ_beyond_tolerance", codes(b["data"], "discrepancies"))


if __name__ == "__main__":
    unittest.main()
