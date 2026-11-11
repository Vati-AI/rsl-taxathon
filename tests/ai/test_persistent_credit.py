"""TAX-139 persistent VAT credit positions: the review, SYN-007 and the gated generator."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from ai.evaluation.assurance_benchmark import canonical_sha256
from ai.evaluation.assurance_pipeline import assess_taxpayer, build_sources
from ai.evaluation.assurance_population import PERSISTENT_CREDIT, generate
from ai.tools.persistent_credit import (
    FINDING_CODE,
    TESTS,
    PersistentCreditError,
    load_policy,
    review_persistent_credit,
)

ROOT = Path(__file__).resolve().parents[2]
SYN007 = ROOT / "data/synthetic/tax139_syn007.json"
RATE = Decimal("0.15")
PERIODS = [f"2026-{m:02d}" for m in range(1, 13)]


def returns(output: str, claimed: str, zero: str = "0.00", n: int = 12) -> list[dict]:
    return [
        {
            "return_id": f"VAT-1-{p}",
            "period": p,
            "output_vat": output,
            "input_vat_claimed": claimed,
            "zero_rated_sales": zero,
        }
        for p in PERIODS[:n]
    ]


class ReviewTests(unittest.TestCase):
    def test_no_credit_means_nothing_to_explain(self) -> None:
        out = review_persistent_credit(vat_returns=returns("150000.00", "100000.00"), vat_rate=RATE)
        self.assertEqual(out["status"], "no_persistent_credit")
        self.assertEqual(out["credit_periods"], [])
        self.assertTrue(all(p["tests"] == [] for p in out["periods"]))
        self.assertIsNone(out["decision"])

    def test_zero_rated_sales_explain_an_exporters_credit(self) -> None:
        # Credit 40,000 a month; zero-rated sales 400,000 forgo 60,000 of output VAT.
        out = review_persistent_credit(
            vat_returns=returns("60000.00", "100000.00", zero="400000.00"), vat_rate=RATE
        )
        self.assertEqual(out["status"], "explained")
        self.assertTrue(out["persistent"])
        self.assertEqual(out["findings"], [])
        self.assertEqual(out["totals"]["explained_by_zero_rated_sales"], "480000.00")
        first = out["periods"][0]["tests"]
        self.assertEqual([t["test"] for t in first], list(TESTS))
        self.assertEqual(first[0]["explained"], "40000.00")
        self.assertIn("Zero-rated sales", first[0]["basis"])

    def test_every_test_is_recorded_in_order_even_when_it_explains_nothing(self) -> None:
        out = review_persistent_credit(vat_returns=returns("60000.00", "100000.00"), vat_rate=RATE)
        for period in out["periods"]:
            self.assertEqual([t["test"] for t in period["tests"]], list(TESTS))
            self.assertEqual(period["tests"][-1]["remaining_after"], "40000.00")

    def test_unexplained_persistent_credit_is_a_question_not_a_decision(self) -> None:
        out = review_persistent_credit(vat_returns=returns("60000.00", "100000.00"), vat_rate=RATE)
        self.assertEqual(out["status"], "unexplained")
        (finding,) = out["findings"]
        self.assertEqual(finding["code"], FINDING_CODE)
        self.assertEqual(finding["periods"], PERIODS)
        self.assertEqual(finding["unexplained_total"], {"amount": "480000.00", "currency": "LSL"})
        self.assertTrue(finding["questions"])
        self.assertNotIn("fraud", json.dumps(out).lower())
        self.assertIsNone(out["decision"])

    def test_customs_import_vat_explains_and_is_cited(self) -> None:
        entries = [
            {"entry_id": f"ASY-{p}", "registered_on": f"{p}-05", "import_vat": "45000.00"}
            for p in PERIODS
        ]
        out = review_persistent_credit(
            vat_returns=returns("60000.00", "100000.00"), vat_rate=RATE, asycuda_entries=entries
        )
        self.assertEqual(out["status"], "explained")
        test = out["periods"][0]["tests"][1]
        self.assertEqual((test["test"], test["explained"]), ("import_vat_paid_at_border", "40000.00"))
        self.assertEqual(test["evidence"], [f"ASY-{PERIODS[0]}"])

    def test_customs_unavailable_is_recorded_as_not_tested(self) -> None:
        out = review_persistent_credit(
            vat_returns=returns("60000.00", "100000.00"), vat_rate=RATE, customs_available=False
        )
        test = out["periods"][0]["tests"][1]
        self.assertEqual(test["status"], "not_tested")
        self.assertIn("unavailable", test["basis"])
        self.assertEqual(out["status"], "unexplained")

    def test_capital_spending_explains_but_asks_the_officer_to_verify_assets(self) -> None:
        saft = {
            "supplier_invoices": [
                {"entry_id": f"SI-{p}", "period": p, "account": "1600", "vat": "50000.00"}
                for p in PERIODS
            ]
            + [{"entry_id": "SI-VAT", "period": PERIODS[0], "account": "1400", "vat": "9.00"}]
        }
        out = review_persistent_credit(
            vat_returns=returns("60000.00", "100000.00"), vat_rate=RATE, saft=saft
        )
        self.assertEqual(out["status"], "explained")
        self.assertEqual(out["totals"]["explained_by_capital_spending"], "480000.00")
        self.assertIn("books_only", out["periods"][0]["tests"][2]["basis"])
        self.assertTrue(out["officer_checks"])
        self.assertNotIn("SI-VAT", out["periods"][0]["tests"][2]["evidence"])

    def test_occasional_unexplained_months_do_not_make_a_finding(self) -> None:
        rows = returns("60000.00", "100000.00", zero="400000.00")
        for row in rows[:5]:  # five months the zero-rated sales do not cover
            row["zero_rated_sales"] = "0.00"
        out = review_persistent_credit(vat_returns=rows, vat_rate=RATE)
        self.assertEqual(len(out["unexplained_periods"]), 5)
        self.assertEqual(out["status"], "explained")

    def test_a_few_credit_months_are_not_persistent(self) -> None:
        rows = returns("150000.00", "100000.00")
        for row in rows[:4]:
            row["input_vat_claimed"] = "200000.00"
        out = review_persistent_credit(vat_returns=rows, vat_rate=RATE)
        self.assertEqual(out["status"], "no_persistent_credit")
        self.assertEqual(len(out["credit_periods"]), 4)

    def test_malformed_input_and_policy_are_refused(self) -> None:
        with self.assertRaises(PersistentCreditError):
            review_persistent_credit(vat_returns=returns(60000.0, "1.00"), vat_rate=RATE)  # type: ignore[arg-type]
        with self.assertRaises(PersistentCreditError):
            review_persistent_credit(vat_returns=returns("1.00", "2.00") * 2, vat_rate=RATE)
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "policy.json"
            bad.write_text(json.dumps({**load_policy(), "explanation_order": ["zero_rated_sales"]}))
            with self.assertRaises(PersistentCreditError):
                load_policy(bad)


class Syn007Tests(unittest.TestCase):
    """The seventh scenario end to end through the assurance pipeline."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = json.loads(SYN007.read_text())
        cls.world = cls.doc["world"]
        sources = build_sources(cls.world)
        cls.results = {t: assess_taxpayer(cls.world, sources, t) for t in cls.world["taxpayers"]}

    def test_fixture_is_synthetic_and_carries_no_answer_in_the_world(self) -> None:
        self.assertTrue(self.doc["synthetic"])
        self.assertTrue(self.world["synthetic"])
        self.assertNotIn("expected", json.dumps(self.world))
        self.assertEqual(self.doc["case_id"], "SYN-007")

    def test_each_taxpayer_matches_its_expected_outcome(self) -> None:
        for tin, expected in self.doc["expected"].items():
            with self.subTest(tin=tin, label=expected["label"]):
                r = self.results[tin]
                pc = r["persistent_credit"]
                self.assertEqual(pc["status"], expected["review_status"])
                used = [
                    t for t in TESTS if Decimal(pc["totals"][f"explained_by_{t}"]) > 0
                ]
                self.assertEqual(used, expected["explained_by"])
                codes = [f["code"] for f in r["findings"]]
                if expected["finding"]:
                    self.assertEqual(codes, [expected["finding"]])
                    (finding,) = r["findings"]
                    self.assertEqual(
                        sorted(c["rule_id"] for c in finding["route"]["candidates"]),
                        expected["expected_routes"],
                    )
                    self.assertFalse(finding["route"]["eligible_for_assessment"])
                else:
                    self.assertEqual(codes, [])
                self.assertEqual([c["lane"]["lane"] for c in r["claims"]], [expected["claim_lane"]])
                self.assertIsNone(r["decision"])

    def test_the_exporters_structural_credit_stays_in_the_fast_lane(self) -> None:
        (claim,) = self.results["510000071"]["claims"]
        self.assertEqual(claim["lane"]["lane"], "fast")
        self.assertEqual(claim["lane"]["disputed_amount"]["amount"], "0.00")

    def test_capital_look_alike_is_explained_with_an_asset_check(self) -> None:
        pc = self.results["510000072"]["persistent_credit"]
        self.assertTrue(pc["persistent"])
        self.assertTrue(pc["officer_checks"])

    def test_the_finding_keeps_the_claim_out_of_the_fast_lane_with_a_reason(self) -> None:
        (claim,) = self.results["510000073"]["claims"]
        self.assertIn(FINDING_CODE, " ".join(claim["lane"]["reasons"]))


class GeneratorTests(unittest.TestCase):
    PARAMS = {
        "n_taxpayers": 30,
        "n_dev": 10,
        "issue_counts": {"fake_invoices": 2, "mixed": 1},
        "persistent_credit": {"planted": 3, "capex_lookalikes": 3},
    }

    def test_without_the_parameter_the_tax118_seal_still_reproduces(self) -> None:
        manifest = json.loads((ROOT / "docs/evidence/TAX-118/manifest.json").read_text())
        world, key = generate(int(manifest["seed"]))
        self.assertEqual(canonical_sha256(world), manifest["dataset_sha256"])
        self.assertEqual(canonical_sha256(key), manifest["answer_key_sha256"])

    def test_planted_and_look_alikes_are_reproducible_and_hidden(self) -> None:
        world, key = generate(3, copy.deepcopy(self.PARAMS))
        again, again_key = generate(3, copy.deepcopy(self.PARAMS))
        self.assertEqual(canonical_sha256(world), canonical_sha256(again))
        self.assertEqual(canonical_sha256(key), canonical_sha256(again_key))
        tk = key["taxpayers"].values()
        planted = [v for v in tk if PERSISTENT_CREDIT in v["issues"]]
        capex = [v for v in tk if "capex_lookalike" in v["counterparts"]]
        self.assertEqual((len(planted), len(capex)), (3, 3))
        self.assertTrue(all(v["issues"] == [PERSISTENT_CREDIT] for v in planted))
        self.assertTrue(all(not v["issues"] for v in capex))
        text = json.dumps(world)
        for secret in ("capex_lookalike", "suppressed_output_vat", "capex_months"):
            self.assertNotIn(secret, text)

    def test_the_pipeline_finds_planted_credits_and_explains_look_alikes(self) -> None:
        world, key = generate(3, copy.deepcopy(self.PARAMS))
        sources = build_sources(world)
        for tin, truth in key["taxpayers"].items():
            planted = PERSISTENT_CREDIT in truth["issues"]
            if not planted and "capex_lookalike" not in truth["counterparts"]:
                continue
            r = assess_taxpayer(world, sources, tin)
            with self.subTest(tin=tin, planted=planted):
                self.assertEqual(PERSISTENT_CREDIT in r["flags"], planted)


if __name__ == "__main__":
    unittest.main()


class LivePathTests(unittest.TestCase):
    """The TAX-121 live path turns the SYN-007 finding into a valid finding body."""

    def test_finding_body_requests_evidence_and_passes_finding_validation(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT / "backend" / "vati_tax"))
        from vati_tax.assurance import rules
        from vati_tax.findings import rules as finding_rules

        world = json.loads(SYN007.read_text())["world"]
        result = assess_taxpayer(world, build_sources(world), "510000073")
        bodies = rules.finding_bodies(
            result,
            sources={},
            bundle_evidence_id="00000000-0000-4000-8000-000000000139",
            register_version="test",
            input_digest="0" * 64,
        )
        ((body, key),) = bodies
        self.assertEqual(body["code"], FINDING_CODE)
        self.assertEqual(body["category"], "refund_reconciliation")
        self.assertEqual(body["proposed_action"], {"type": "request_evidence",
                                                   "requires_human_approval": True})  # fmt: skip
        self.assertEqual(body["unresolved_amount"]["amount"], "374625.00")
        self.assertEqual(len(body["alternative_explanations"]), len(TESTS))
        self.assertEqual(len(body["evidence_refs"]), 9)  # the nine unexplained returns
        self.assertTrue(key.startswith("check:"))
        finding = finding_rules.FindingInput.build(body)
        self.assertEqual(finding.proposed_action, "request_evidence")
        self.assertIsNone(result["decision"])
