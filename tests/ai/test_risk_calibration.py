"""TAX-066 risk calibration and drift evaluation (pure Python, no LangGraph needed)."""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ai.evaluation import risk_calibration as rc  # noqa: E402

FIXTURES = rc.load_fixtures()


def fixtures() -> dict[str, Any]:
    return copy.deepcopy(FIXTURES)


def add_case(doc: dict[str, Any], n: int, period: str, outcome: str, **values: Any) -> None:
    approved_at = {
        "2024-Q3": "2024-10-28T09:00:00Z",
        "2024-Q4": "2025-01-27T09:00:00Z",
        "2025-Q1": "2025-04-28T09:00:00Z",
        "2025-Q2": "2025-07-28T09:00:00Z",
        "2025-Q3": "2025-10-27T09:00:00Z",
        "2025-Q4": "2026-01-26T09:00:00Z",
    }[period]
    spec = {
        "n": n,
        "period": period,
        "refund": "0.00",
        "claimed": "0.00",
        "documented": "0.00",
        "related_net": "0.00",
        "loss_run": 0,
        "imported_unverified": 0,
        "review": {
            "outcome": outcome,
            "proposed_by": "officer-a@synthetic.test",
            "approved_by": "supervisor-c@synthetic.test",
            "approved_at": approved_at,
        },
    }
    spec.update(values)
    doc["cases"].append(spec)


def approval(result: rc.CalibrationResult, **overrides: Any) -> dict[str, Any]:
    base = {
        "decision": "approve",
        "approver": "SYN-SUPERVISOR-02",
        "rationale": "Synthetic review: holdout false positives fall from 8 to 5 with no regressions.",
        "candidate_digest": rc.digest(result.candidate),
        "parent_digest": result.report["approved"]["digest"],
        "report_digest": result.report["report_digest"],
        "acknowledged_drift_alerts": [a["id"] for a in result.report["drift"]["alerts"]],
        "approved_at": "2026-02-02T09:00:00Z",
    }
    base.update(overrides)
    return base


class ShippedFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = rc.evaluate_fixtures()
        cls.report = cls.result.report

    def test_matches_reviewed_baseline(self) -> None:
        self.assertEqual(rc.check_expected(self.report, FIXTURES["expected"]), [])

    def test_temporal_windows_and_exclusions(self) -> None:
        windows = self.report["windows"]
        self.assertEqual(windows["calibration_cases"], 31)
        self.assertEqual(windows["holdout_cases"], 16)
        self.assertEqual(
            windows["excluded"], [{"case": "TAX066-32", "reason": "label_after_cutoff"}]
        )

    def test_candidate_is_proposed_and_valid(self) -> None:
        candidate = self.result.candidate
        self.assertEqual(candidate["status"], "proposed_calibration")
        self.assertIs(candidate["calibration"]["auto_activate"], False)
        self.assertEqual(candidate["ruleset_version"], "1.1.0")
        self.assertEqual(candidate["calibration"]["parent_digest"], rc.digest(self.result.approved))
        rc.tax_risk().validate_ruleset(candidate)
        for rule in candidate["rules"]:
            self.assertTrue(rc.tax_risk().jube_script(candidate, rule).startswith("If Payload."))

    def test_failures_listed_by_case(self) -> None:
        holdout = self.report["holdout"]
        self.assertEqual(
            holdout["candidate"]["false_positive_cases"],
            ["TAX066-37", "TAX066-39", "TAX066-44", "TAX066-45", "TAX066-47"],
        )
        self.assertEqual(holdout["candidate"]["false_positive_rate"]["fraction"], "5/9")
        self.assertEqual(
            self.report["false_positives_removed"], ["TAX066-34", "TAX066-36", "TAX066-41"]
        )

    def test_drift_is_reported(self) -> None:
        ids = [a["id"] for a in self.report["drift"]["alerts"]]
        self.assertEqual(
            ids,
            ["feature_psi:imported_service_unverified_count", "rule_rate:TR-IMPORTED-SERVICE-GAP"],
        )
        self.assertEqual([p["period"] for p in self.report["drift"]["per_period"]][0], "2024-Q3")

    def test_deterministic(self) -> None:
        again = rc.evaluate_fixtures()
        self.assertEqual(again.report["report_digest"], self.report["report_digest"])
        self.assertEqual(rc.digest(again.candidate), rc.digest(self.result.candidate))

    def test_report_digest_covers_report(self) -> None:
        body = {k: v for k, v in self.report.items() if k != "report_digest"}
        self.assertEqual(rc.digest(body), self.report["report_digest"])


class NoLeakageTests(unittest.TestCase):
    def test_holdout_labels_never_change_the_candidate(self) -> None:
        doc = fixtures()
        for spec in doc["cases"]:
            if spec["period"] in doc["calibration"]["holdout_periods"]:
                spec["review"]["outcome"] = (
                    "request_further_evidence"
                    if spec["review"]["outcome"] == "close_no_further_action"
                    else "close_no_further_action"
                )
        flipped = rc.evaluate_fixtures(doc)
        self.assertEqual(rc.digest(flipped.candidate), rc.digest(rc.evaluate_fixtures().candidate))

    def test_label_after_cutoff_is_excluded_and_matters(self) -> None:
        doc = fixtures()
        late = next(s for s in doc["cases"] if s["n"] == 32)
        late.update({"documented": "12940.00", "loss_run": 0})  # a 60.00 gap, review warranted
        excluded = rc.evaluate_fixtures(doc)
        self.assertEqual(
            {c["rule_id"]: c["to"] for c in excluded.report["candidate"]["changes"]}[
                "TR-VAT-REFUND-MISMATCH"
            ],
            "100.00",
        )
        late["review"]["approved_at"] = "2025-08-01T09:00:00Z"
        included = rc.evaluate_fixtures(doc)
        self.assertEqual(included.report["windows"]["calibration_cases"], 32)
        self.assertEqual(
            {c["rule_id"]: c["to"] for c in included.report["candidate"]["changes"]}[
                "TR-VAT-REFUND-MISMATCH"
            ],
            "50.00",
        )

    def test_windows_must_be_temporal_and_disjoint(self) -> None:
        doc = fixtures()
        doc["calibration"]["holdout_periods"] = ["2025-Q2", "2025-Q3"]
        with self.assertRaises(rc.CalibrationError) as caught:
            rc.evaluate_fixtures(doc)
        self.assertEqual(caught.exception.code, "periods_overlap")
        doc = fixtures()
        doc["calibration"]["calibration_periods"] = ["2025-Q3"]
        doc["calibration"]["holdout_periods"] = ["2025-Q1"]
        with self.assertRaises(rc.CalibrationError) as caught:
            rc.evaluate_fixtures(doc)
        self.assertEqual(caught.exception.code, "not_temporal")


class RegressionGateTests(unittest.TestCase):
    def test_legitimate_case_regression_blocks_promotion(self) -> None:
        doc = fixtures()
        # A review-warranted case with two loss quarters makes the fit lower the threshold.
        add_case(doc, 49, "2025-Q1", "escalate_to_supervisor", loss_run=2)
        add_case(doc, 50, "2025-Q4", "close_no_further_action", loss_run=2)
        result = rc.evaluate_fixtures(doc)
        changes = {c["rule_id"]: c["to"] for c in result.report["candidate"]["changes"]}
        self.assertEqual(changes["TR-PERSISTENT-LOSSES"], "2")
        self.assertEqual(result.report["legitimate_case_regressions"], ["TAX066-50"])
        self.assertEqual(result.report["metric_gate"], "blocked")
        self.assertIn("legitimate_case_regressions", result.report["blocked_by"])
        with self.assertRaises(rc.PromotionRefused) as caught:
            rc.promote(result, approval(result))
        self.assertEqual(caught.exception.code, "metric_gate_blocked")
        self.assertIn("legitimate_case_regressions", caught.exception.detail)

    def test_newly_missed_review_warranted_case_blocks(self) -> None:
        doc = fixtures()
        add_case(
            doc,
            49,
            "2025-Q4",
            "request_further_evidence",
            refund="5080.00",
            claimed="5080.00",
            documented="5000.00",
        )
        result = rc.evaluate_fixtures(doc)
        self.assertEqual(result.report["new_missed_review_warranted"], ["TAX066-49"])
        self.assertIn("new_missed_review_warranted", result.report["blocked_by"])

    def test_no_change_is_not_promotable(self) -> None:
        doc = fixtures()
        doc["calibration"]["grid"] = {"TR-VAT-REFUND-MISMATCH": ["0.00"]}
        result = rc.evaluate_fixtures(doc)
        self.assertEqual(result.report["candidate"]["changes"], [])
        self.assertIn("candidate_changes_something", result.report["blocked_by"])

    def test_too_little_holdout_blocks(self) -> None:
        doc = fixtures()
        doc["thresholds"]["min_holdout_cases"] = 17
        self.assertIn("min_holdout_cases", rc.evaluate_fixtures(doc).report["blocked_by"])


class PromotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = rc.evaluate_fixtures()

    def refused(self, approval_record: Any, **kwargs: Any) -> str:
        with self.assertRaises(rc.PromotionRefused) as caught:
            rc.promote(self.result, approval_record, **kwargs)
        return caught.exception.code

    def test_valid_approval_returns_record_and_never_writes_ruleset(self) -> None:
        before = rc.APPROVED_RULESET_PATH.read_bytes()
        record = rc.promote(self.result, approval(self.result))
        self.assertEqual(record["status"], "approved_for_activation")
        self.assertEqual(record["candidate_ruleset_version"], "1.1.0")
        self.assertIn("never writes the approved ruleset", record["activation"])
        self.assertEqual(rc.APPROVED_RULESET_PATH.read_bytes(), before)
        self.assertEqual(rc.load_approved_ruleset()["ruleset_version"], "1.0.0")

    def test_refusals(self) -> None:
        r = self.result
        self.assertEqual(self.refused(None), "approval_required")
        self.assertEqual(self.refused(approval(r, decision="reject")), "not_approved")
        self.assertEqual(
            self.refused(approval(r, approver="officer-a@synthetic.test")), "invalid_approver"
        )
        self.assertEqual(self.refused(approval(r, rationale=" ")), "rationale_required")
        self.assertEqual(self.refused(approval(r, candidate_digest="sha256:0")), "digest_mismatch")
        self.assertEqual(self.refused(approval(r, report_digest="sha256:0")), "report_mismatch")
        self.assertEqual(self.refused(approval(r, parent_digest="sha256:0")), "stale_parent")
        code = self.refused(
            approval(r, acknowledged_drift_alerts=["rule_rate:TR-IMPORTED-SERVICE-GAP"])
        )
        self.assertEqual(code, "drift_not_acknowledged")

    def test_self_approval(self) -> None:
        doc = fixtures()
        doc["calibration"]["proposer"] = "SYN-SUPERVISOR-02"
        result = rc.evaluate_fixtures(doc)
        with self.assertRaises(rc.PromotionRefused) as caught:
            rc.promote(result, approval(result))
        self.assertEqual(caught.exception.code, "self_approval")

    def test_approved_ruleset_changed_since_calibration(self) -> None:
        live = copy.deepcopy(self.result.approved)
        live["rules"][0]["explanation"] += " Edited."
        self.assertEqual(self.refused(approval(self.result), current_approved=live), "stale_parent")

    def test_tampered_report_is_refused(self) -> None:
        tampered = rc.CalibrationResult(
            {
                **self.result.report,
                "metric_gate": "pass",
                "blocked_by": [],
                "drift": {"alerts": []},
            },
            self.result.candidate,
            self.result.approved,
        )
        with self.assertRaises(rc.PromotionRefused) as caught:
            rc.promote(tampered, approval(self.result))
        self.assertEqual(caught.exception.code, "report_mismatch")

    def test_cannot_calibrate_from_a_candidate(self) -> None:
        with self.assertRaises(rc.CalibrationError) as caught:
            rc.evaluate_fixtures(approved=self.result.candidate)
        self.assertEqual(caught.exception.code, "parent_not_approved")

    def test_candidate_never_written_over_approved_rules(self) -> None:
        with self.assertRaises(rc.PromotionRefused) as caught:
            rc.write_candidate(self.result, rc.APPROVED_RULESET_PATH)
        self.assertEqual(caught.exception.code, "protected_path")
        with self.assertRaises(rc.PromotionRefused):
            rc.write_candidate(self.result, rc.PROTECTED_DIR / "tax_risk_rules.v2.json")
        with tempfile.TemporaryDirectory() as tmp:
            path = rc.write_candidate(self.result, Path(tmp) / "candidate.json")
            self.assertIn('"proposed_calibration"', path.read_text(encoding="utf-8"))


class LedgerInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = fixtures()
        self.entries, self.report = rc.synthetic_ledger(self.doc)
        self.tenant = self.doc["tenant_id"]

    def outcomes(self, entries: Any = None, report: Any = None) -> Any:
        return rc.reviewed_outcomes(
            self.entries if entries is None else entries,
            self.report if report is None else report,
            tenant_id=self.tenant,
        )

    def code(self, entries: Any = None, report: Any = None) -> str:
        with self.assertRaises(rc.CalibrationError) as caught:
            self.outcomes(entries, report)
        return caught.exception.code

    def test_ledger_must_be_verified(self) -> None:
        self.assertEqual(self.code(report={**self.report, "intact": False}), "ledger_not_intact")
        short = {**self.report, "head_seq": self.report["head_seq"] - 1}
        self.assertEqual(self.code(report=short), "ledger_not_verified")

    def test_tenant_and_chain_conflicts(self) -> None:
        other = [*self.entries, {**self.entries[0], "tenant_id": "tenant-other"}]
        self.assertEqual(self.code(other), "tenant_mismatch")
        clash = [*self.entries, {**self.entries[1], "event_id": "evt-066-forged"}]
        self.assertEqual(self.code(clash), "chain_conflict")
        labels, _ = self.outcomes([*self.entries, dict(self.entries[0])])  # duplicate delivery
        self.assertEqual(len(labels), 48)

    def test_unqualified_decisions_are_excluded_with_reasons(self) -> None:
        reasons = {
            1: {"approved_by": "officer-a@synthetic.test"},  # same person proposed: SoD
            2: {"approver_role": "auditor"},
            3: {"execution": "failed"},
            4: {"challenge": "overturned"},
            5: {"outcome": "deny_refund"},
        }
        for spec in self.doc["cases"]:
            spec["review"].update(reasons.get(spec["n"], {}))
        entries, report = rc.synthetic_ledger(self.doc)
        labels, excluded = rc.reviewed_outcomes(entries, report, tenant_id=self.tenant)
        got = {e["case_id"]: e["reason"] for e in excluded}
        self.assertEqual(
            got,
            {
                rc.case_uuid(1): "sod_violation",
                rc.case_uuid(2): "approver_not_supervisor",
                rc.case_uuid(3): "not_executed",
                rc.case_uuid(4): "overturned_on_challenge",
                rc.case_uuid(5): "unknown_outcome",
            },
        )
        self.assertEqual(len(labels), 43)
        result = rc.evaluate_fixtures(self.doc)
        shown = {e["case"]: e["reason"] for e in result.report["windows"]["excluded"]}
        self.assertEqual(shown["TAX066-01"], "sod_violation")

    def test_latest_approved_decision_is_the_label(self) -> None:
        later = [
            {
                **e,
                "event_id": e["event_id"] + "-2",
                "chain_seq": e["chain_seq"] + 1000,
                "subject_id": "dec-066-01-b",
                **({"detail": "request_further_evidence"} if "detail" in e else {}),
            }
            for e in self.entries
            if e["case_id"] == rc.case_uuid(1)
        ]
        report = {**self.report, "head_seq": 2000, "entries_checked": 2000}
        labels, _ = self.outcomes([*self.entries, *later], report)
        self.assertEqual(labels[rc.case_uuid(1)].outcome, "request_further_evidence")


class DriftTests(unittest.TestCase):
    def test_psi_identical_is_zero_and_shift_is_positive(self) -> None:
        self.assertAlmostEqual(rc.psi([10, 5, 1], [10, 5, 1]), 0.0)
        self.assertGreater(rc.psi([28, 2, 1], [10, 4, 2]), 0.25)

    def test_no_alerts_without_drift(self) -> None:
        doc = fixtures()
        # Remove the imported-services surge from the holdout window.
        for spec in doc["cases"]:
            if spec["period"] in doc["calibration"]["holdout_periods"] and spec["n"] in (
                39,
                45,
                46,
                47,
                48,
            ):
                spec["imported_unverified"] = 0
            if spec["n"] == 40:
                spec["imported_unverified"] = 1
        result = rc.evaluate_fixtures(doc)
        self.assertEqual(result.report["drift"]["alerts"], [])


if __name__ == "__main__":
    unittest.main()
