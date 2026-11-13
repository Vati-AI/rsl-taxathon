"""TAX-131: TAX-066/067 output exported as feedback for RSL's Risk Unit (ADR-053)."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from ai.evaluation import risk_calibration
from ai.evaluation import risk_unit_feedback as ruf
from ai.workflows import peer_baselines as pb

ISSUED = "2026-11-13T00:00:00Z"
TENANT = "tenant-synthetic"
OFFICER = {"actor_id": "officer-a", "tenant_id": TENANT, "roles": ["auditor"],
           "authenticated": True}  # fmt: skip
GOOD = {"complete": True, "amended_unreconciled": False, "source_verified": True}


def row(taxpayer, period, refund, region="maseru"):
    return {
        "tenant_id": TENANT, "taxpayer_id": taxpayer, "period": period, "sector": "retail",
        "size_band": "small", "region": region, "data_source": "filed_return",
        "amounts": {"refund_claim": refund, "turnover": "100000.00",
                    "input_vat_claimed": refund},
        "data_quality": dict(GOOD),
    }  # fmt: skip


def comparisons():
    regions = ["maseru", "leribe", "mafeteng", "berea"]
    rows = [row("SYN-TP-SUBJECT", "2025-Q4", "15000.00")]
    rows += [row("SYN-TP-SUBJECT", p, f"{2000 + 100 * i}.00")
             for i, p in enumerate(("2024-Q4", "2025-Q1", "2025-Q2", "2025-Q3"))]  # fmt: skip
    rows += [row(f"SYN-TP-PEER-{i:02d}", "2025-Q4", f"{1500 + 150 * i}.00", regions[i % 4])
             for i in range(12)]  # fmt: skip
    one = pb.compare(rows, actor=OFFICER, tenant_id=TENANT, taxpayer_id="SYN-TP-SUBJECT",
                     period="2025-Q4")  # fmt: skip
    thin = pb.compare(rows[:3], actor=OFFICER, tenant_id=TENANT, taxpayer_id="SYN-TP-SUBJECT",
                      period="2025-Q4")  # fmt: skip
    return [one, thin]


class FeedbackPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = risk_calibration.evaluate_fixtures().report
        cls.comparisons = comparisons()
        cls.pack = ruf.build_pack(cls.report, cls.comparisons, issued_at=ISSUED)

    def test_the_pack_is_feedback_for_the_risk_unit_under_an_assumed_contract(self):
        pack = self.pack
        self.assertEqual(pack["schema"], "vati.risk-unit-feedback/1")
        self.assertTrue(pack["contract_id"].endswith("-assumed"))
        self.assertEqual((pack["authority"], pack["audience"]), ("feedback_only", "RSL Risk Unit"))
        self.assertTrue(pack["synthetic"])
        self.assertIn("not a risk score", pack["statement"])
        ruf.validate_pack(pack)

    def test_calibration_agreement_matches_the_tax066_report(self):
        holdout = self.report["holdout"]["approved"]
        agreement = self.pack["calibration"]["holdout_agreement"]
        self.assertEqual(agreement["signal_confirmed_by_review"], holdout["tp"])
        self.assertEqual(agreement["signal_not_confirmed_by_review"], holdout["fp"])
        self.assertEqual(agreement["review_found_issue_without_signal"], holdout["fn"])
        self.assertEqual(self.pack["calibration"]["report_digest"], self.report["report_digest"])
        self.assertEqual(len(self.pack["calibration"]["drift_alerts"]),
                         len(self.report["drift"]["alerts"]))  # fmt: skip
        self.assertFalse(self.pack["calibration"]["proposed_changes_active"])

    def test_peer_baselines_are_counted_not_copied(self):
        section = self.pack["peer_baselines"]
        self.assertTrue(section["included"])
        self.assertEqual(section["comparisons"], 2)
        for metric in section["by_metric"].values():
            for counts in metric.values():
                self.assertEqual(sum(counts.values()), 2)
        self.assertGreater(
            sum(m[k]["insufficient_evidence"] for m in section["by_metric"].values() for k in m), 0
        )
        absent = ruf.build_pack(self.report, None, issued_at=ISSUED)["peer_baselines"]
        self.assertFalse(absent["included"])

    def test_no_case_taxpayer_or_score_leaves_vati(self):
        text = json.dumps(self.pack)
        for value in ("SYN-TP-SUBJECT", "SYN-TP-PEER", TENANT, "15000.00"):
            self.assertNotIn(value, text)
        for case_id in self.report["holdout"]["approved"]["false_positive_cases"]:
            self.assertNotIn(case_id, text)
        self.assertNotIn('"risk_score"', text)
        self.assertNotIn('"score"', text)

    def test_tampered_or_scored_packs_are_refused(self):
        for mutate, code in (
            (lambda p: p["calibration"].__setitem__("risk_score", 0.9), "forbidden_field"),
            (lambda p: p["peer_baselines"].__setitem__("taxpayer_id", "X"), "identifying_field"),
            (lambda p: p.__setitem__("authority", "recommendation_only"), "wrong_authority"),
            (lambda p: p["calibration"]["window"].__setitem__("reviewed_cases", 1),
             "digest_mismatch"),
        ):  # fmt: skip
            pack = copy.deepcopy(self.pack)
            mutate(pack)
            with self.assertRaises(ruf.FeedbackError) as ctx:
                ruf.validate_pack(pack)
            self.assertEqual(ctx.exception.code, code)

    def test_a_comparison_carrying_a_score_is_refused(self):
        bad = dict(self.comparisons[0], risk_score=0.7)
        with self.assertRaises(ruf.FeedbackError) as ctx:
            ruf.build_pack(self.report, [bad], issued_at=ISSUED)
        self.assertEqual(ctx.exception.code, "comparison_carries_a_score")

    def test_export_writes_a_file_but_never_into_the_rules_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = ruf.write_pack(self.pack, Path(tmp) / "pack.json")
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), self.pack)
        rules = risk_calibration.APPROVED_RULESET_PATH.parent / "feedback.json"
        with self.assertRaises(ruf.FeedbackError):
            ruf.write_pack(self.pack, rules)
        self.assertFalse(rules.exists())

    def test_cli_check_and_out(self):
        self.assertEqual(ruf.main(["--check"]), 0)
        with tempfile.TemporaryDirectory() as tmp:
            baselines = Path(tmp) / "comparisons.json"
            baselines.write_text(json.dumps(self.comparisons), encoding="utf-8")
            out = Path(tmp) / "pack.json"
            self.assertEqual(ruf.main(["--baselines", str(baselines), "--out", str(out)]), 0)
            self.assertTrue(
                json.loads(out.read_text(encoding="utf-8"))["peer_baselines"]["included"]
            )


if __name__ == "__main__":
    unittest.main()
