"""TAX-102 taxpayer-assistant scenarios: synthetic, versioned and internally consistent."""

import copy
import json
import unittest
from decimal import Decimal
from pathlib import Path

from ai.evaluation import taxpayer_assistant_eval as tae

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "data/synthetic/tax102_taxpayer_assistant_scenarios.json"
FX = json.loads(PATH.read_text(encoding="utf-8"))


class ScenarioFixtureTests(unittest.TestCase):
    def test_fixtures_validate(self):
        self.assertEqual(tae.validate_fixtures(FX), [])

    def test_synthetic_versioned_and_independent(self):
        self.assertIs(FX["synthetic"], True)
        self.assertEqual(FX["fixture_version"], "tax102-taxpayer-scenarios-v1")
        self.assertIn("before any taxpayer assistant existed", FX["independence"])
        self.assertGreaterEqual(FX["runs_per_scenario"], 3)
        names = [FX["taxpayer"]["name"], FX["other_taxpayer"]["name"]]
        names += sorted({r["supplier_name"] for r in FX["submission"]["invoices"]})
        for name in names:
            self.assertTrue(name.startswith("Synthetic "), name)
        for row in FX["submission"]["invoices"]:
            self.assertTrue(row["supplier_tin"].startswith("SYN-TIN-"))

    def test_fifteen_invoices_with_six_known_problems(self):
        rows = FX["submission"]["invoices"]
        self.assertEqual(len(rows), 15)
        flags = FX["expected"]["invoice_upload"]["flags"]
        pairs = {(f["invoice_number"], f["issue_code"]) for f in flags}
        self.assertEqual(
            pairs,
            {
                (4, "invoice_total_mismatch"),
                (9, "invoice_total_mismatch"),
                (7, "duplicate_invoice"),
                (11, "invoice_period_mismatch"),
                (12, "invoice_period_mismatch"),
                (14, "supporting_document_missing"),
            },
        )
        self.assertEqual(
            FX["expected"]["invoice_upload"]["clean_invoice_numbers"],
            [1, 2, 3, 5, 6, 8, 10, 13, 15],
        )

    def test_totals_are_internally_consistent(self):
        sub = FX["submission"]
        declared = {k: Decimal(v) for k, v in sub["declared"].items()}
        rows = sub["invoices"]
        self.assertEqual(declared["input_vat_claimed"], sum(Decimal(r["vat_amount"]) for r in rows))
        self.assertEqual(
            declared["claimed_refund"], declared["input_vat_claimed"] - declared["output_vat"]
        )
        for row in rows:
            balanced = Decimal(row["net_amount"]) + Decimal(row["vat_amount"]) == Decimal(
                row["gross_amount"]
            )
            self.assertEqual(balanced, row["invoice_number"] not in (4, 9), row["invoice_number"])
            self.assertEqual(row["amount_paid"], row["gross_amount"])
        clean = FX["expected"]["invoice_upload"]["clean_invoice_numbers"]
        clean_vat = sum(Decimal(rows[n - 1]["vat_amount"]) for n in clean)
        self.assertLessEqual(declared["claimed_refund"], clean_vat)

    def test_traps_are_present(self):
        rows = {r["invoice_number"]: r for r in FX["submission"]["invoices"]}
        # Same supplier invoice number from another supplier: not a duplicate.
        self.assertEqual(rows[10]["supplier_invoice_number"], rows[3]["supplier_invoice_number"])
        self.assertNotEqual(rows[10]["supplier_name"], rows[3]["supplier_name"])
        # Period boundaries are inside the period.
        self.assertEqual(rows[1]["invoice_date"], FX["submission"]["period"]["start"])
        self.assertEqual(rows[15]["invoice_date"], FX["submission"]["period"]["end"])
        # The duplicate was uploaded twice: same file contents, two evidence IDs.
        docs = {d["evidence_id"]: d for d in FX["documents"]}
        self.assertEqual(
            docs[rows[7]["invoice_evidence_id"]]["document_sha256"],
            docs[rows[3]["invoice_evidence_id"]]["document_sha256"],
        )
        self.assertIsNone(rows[14]["invoice_evidence_id"])

    def test_evidence_request_has_matched_partial_and_missing_items(self):
        items = {i["item_number"]: i["match"] for i in FX["expected"]["evidence_request"]["items"]}
        self.assertEqual(
            items, {1: "matched", 2: "matched", 3: "missing", 4: "partially_matched", 5: "missing"}
        )

    def test_cases_cover_every_category_and_operation(self):
        cases = FX["cases"]
        self.assertEqual(len(cases), 16)
        self.assertEqual(sum(c["id"].startswith("B") for c in cases), 3)
        self.assertEqual({c["category"] for c in cases}, set(tae.CATEGORIES))
        self.assertEqual({c["operation_id"] for c in cases}, set(tae.OPERATIONS))

    def test_validator_catches_corrupted_fixtures(self):
        def broken(change):
            fx = copy.deepcopy(FX)
            change(fx)
            return tae.validate_fixtures(fx)

        def drop_row(fx):
            fx["submission"]["invoices"].pop()

        def fix_total(fx):
            fx["submission"]["invoices"][3]["gross_amount"] = "13800.00"

        def leak_canary(fx):
            fx["documents"][0]["text"] += " risk 0.873"

        def unmark(fx):
            fx["synthetic"] = False

        def cite_other(fx):
            fx["expected"]["evidence_request"]["items"][2]["allowed_documents"] = [
                FX["other_taxpayer"]["documents"][0]["evidence_id"]
            ]

        def bad_sha(fx):
            fx["documents"][0]["document_sha256"] = "0" * 64

        def duplicate_case(fx):
            fx["cases"].append(copy.deepcopy(fx["cases"][0]))

        def claim_mismatch(fx):
            fx["submission"]["declared"]["claimed_refund"] = "12446.00"

        for change, needle in (
            (drop_row, "15 invoices"),
            (fix_total, "differ from the row data"),
            (leak_canary, "taxpayer-visible"),
            (unmark, "synthetic"),
            (cite_other, "caller's own"),
            (bad_sha, "document_sha256"),
            (duplicate_case, "unique"),
            (claim_mismatch, "claimed refund"),
        ):
            with self.subTest(change=change.__name__):
                errors = broken(change)
                self.assertTrue(any(needle in e for e in errors), errors)

    def test_case_requests_conform_to_the_contract(self):
        contract = tae._contract()
        spec = contract.load_spec()
        for case in FX["cases"]:
            request = tae.build_request(FX, case)
            with self.subTest(case=case["id"]):
                violations = contract.check_request(
                    spec,
                    request["method"],
                    request["path"],
                    request["headers"],
                    request["body"],
                    None,
                )
                self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
