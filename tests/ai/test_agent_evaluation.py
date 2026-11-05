"""TAX-056 agent evaluation and injection regression: harness gate and seeded faults."""

import copy
import importlib.util
import json
import os
import unittest
from pathlib import Path

os.environ.setdefault("LANGSMITH_TRACING", "false")
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = json.loads((ROOT / "data/synthetic/tax056_evaluation_fixtures.json").read_text())
RECORDS = {
    r["case_id"]: r for r in json.loads((ROOT / "data/synthetic/tax024_records.json").read_text())
}
HAS_SQLITE_SAVER = (
    importlib.util.find_spec("langgraph") is not None
    and importlib.util.find_spec("langgraph.checkpoint.sqlite") is not None
)


class FixtureTests(unittest.TestCase):
    """Fixture policy; pure Python."""

    def test_fixtures_are_synthetic_and_repeat_each_scenario_at_least_three_times(self) -> None:
        self.assertIs(FIXTURES["synthetic"], True)
        self.assertGreaterEqual(FIXTURES["runs_per_scenario"], 3)
        for metric in (
            "expected_output",
            "false_positive",
            "citation_coverage",
            "interrupt_compliance",
            "hostile_resistance",
            "determinism",
        ):
            self.assertIn(metric, FIXTURES["thresholds"])

    def test_every_playbook_has_expectations_for_all_five_scenarios(self) -> None:
        for name, cases in FIXTURES["record_playbooks"].items():
            with self.subTest(playbook=name):
                self.assertEqual(set(cases), set(RECORDS))

    def test_expected_evidence_comes_from_the_case_records(self) -> None:
        for name, cases in FIXTURES["record_playbooks"].items():
            for case_id, spec in cases.items():
                record = RECORDS[case_id]
                ids = {
                    d["id"]
                    for d in [
                        record["return"],
                        *record["contracts"],
                        *record["invoices"],
                        *record["payments"],
                    ]
                }
                for item in spec.get("expect", []):
                    with self.subTest(playbook=name, case=case_id, code=item["code"]):
                        self.assertTrue(set(item["evidence_ids_include"]) <= ids)
                        self.assertNotIn(item["code"], record["expected_non_findings"])

    def test_each_playbook_has_injection_and_cross_scope_hostile_fixtures(self) -> None:
        for name in FIXTURES["record_playbooks"]:
            hostile = [h for h in FIXTURES["record_hostile"] if h["subject"] == name]
            with self.subTest(playbook=name):
                self.assertTrue(
                    any("{injection}" in json.dumps(h.get("mutations", [])) for h in hostile)
                )
                self.assertTrue(any(h.get("source") for h in hostile))

    def test_known_failures_are_documented_with_owner_and_reason(self) -> None:
        ids = [k["id"] for k in FIXTURES["known_failures"]]
        self.assertEqual(len(ids), len(set(ids)))
        for item in FIXTURES["known_failures"]:
            with self.subTest(id=item["id"]):
                self.assertTrue(item["owner"].strip())
                self.assertGreaterEqual(len(item["reason"]), 20)
                self.assertRegex(item["finding"], r"^F\d+$")


@unittest.skipUnless(HAS_SQLITE_SAVER, "LangGraph SQLite checkpointer not installed")
class HarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from ai.evaluation import agent_eval

        cls.ae = agent_eval
        cls.checks, cls.report = agent_eval.run_evaluation()

    def test_gate_passes_with_no_regressions_and_no_stale_known_failures(self) -> None:
        self.assertEqual(self.report["regressions"], [])
        self.assertEqual(self.report["stale_known_failures"], [])
        self.assertEqual(self.report["gate"], "pass")

    def test_every_registered_playbook_and_agent_is_evaluated(self) -> None:
        from ai.workflows.playbook_registry import load_registry

        registered = {e.name for e in load_registry().entries() if e.kind == "playbook"}
        evaluated = {c.subject for c in self.checks}
        self.assertTrue(registered <= evaluated, registered - evaluated)
        self.assertTrue(
            {"policy_gap_gate", "case_briefing", "approved_action_checkpoint"} <= evaluated
        )

    def test_known_failures_are_reported_not_hidden(self) -> None:
        reported = {
            f["id"]
            for metrics in self.report["subjects"].values()
            for item in metrics.values()
            for f in item["failures"]
        }
        known = {k["id"] for k in FIXTURES["known_failures"]}
        self.assertEqual(reported, known)
        self.assertEqual(self.report["failed_checks"], len(known))
        text = self.ae.render_text(self.report)
        for failure_id in known:
            self.assertIn(failure_id, text)

    def test_no_false_positives_and_full_interrupt_compliance(self) -> None:
        overall = self.report["overall"]
        self.assertEqual(overall["false_positive"]["numerator"], 0)
        self.assertGreater(overall["false_positive"]["denominator"], 0)
        self.assertEqual(overall["interrupt_compliance"]["value"], 1.0)
        self.assertEqual(overall["determinism"]["value"], 1.0)

    def test_results_are_deterministic_across_harness_runs(self) -> None:
        _, again = self.ae.run_evaluation()
        self.assertEqual(again["results_digest"], self.report["results_digest"])


@unittest.skipUnless(HAS_SQLITE_SAVER, "LangGraph SQLite checkpointer not installed")
class SeededFaultTests(unittest.TestCase):
    """The metrics must catch deliberately broken outputs (no silent passes)."""

    def setUp(self) -> None:
        from ai.evaluation import agent_eval

        self.ae = agent_eval
        self.rec = agent_eval.Recorder()
        self.forbidden = frozenset(FIXTURES["forbidden_codes"])
        self.spec = FIXTURES["record_playbooks"]["related_party"]["SYN-002"]
        self.good = {
            "kind": "related_party_review_proposal",
            "code": "related_party_transaction",
            "evidence_ids": ["CTR-SYN-002-01", "INV-SYN-002-01"],
            "requires_officer_approval": True,
            "decision": None,
            "approved_by": None,
            "observations": [
                {
                    "code": "related_party_transaction",
                    "evidence_ids": ["CTR-SYN-002-01", "INV-SYN-002-01"],
                },
                {"code": "consecutive_loss_periods", "evidence_ids": ["RET-SYN-002"]},
            ],
            "evidence_questions": [
                {
                    "question_id": "q1",
                    "evidence_ids": ["CTR-SYN-002-01"],
                    "status": "draft_requires_officer_approval",
                }
            ],
        }

    def score(self, finding: dict, **final: object) -> dict[str, list]:
        state = {"findings": [finding], "requires_human_review": True, **final}
        self.ae.evaluate_record_playbook_output(
            self.rec,
            "related_party",
            "SYN-002",
            state,
            self.spec,
            RECORDS["SYN-002"],
            self.forbidden,
        )
        failed: dict[str, list] = {}
        for check in self.rec.checks:
            if not check.passed:
                failed.setdefault(check.metric, []).append(check.key)
        return failed

    def test_a_correct_output_passes_every_metric(self) -> None:
        self.assertEqual(self.score(self.good), {})

    def test_citation_outside_the_case_is_caught(self) -> None:
        bad = copy.deepcopy(self.good)
        bad["observations"][1]["evidence_ids"] = ["RET-SYN-001"]
        self.assertIn("citation_coverage", self.score(bad))

    def test_uncited_claim_is_caught(self) -> None:
        bad = copy.deepcopy(self.good)
        bad["observations"][1]["evidence_ids"] = []
        self.assertIn("citation_coverage", self.score(bad))

    def test_forbidden_and_unexpected_codes_are_false_positives(self) -> None:
        bad = copy.deepcopy(self.good)
        bad["observations"].append({"code": "proven_abuse", "evidence_ids": ["RET-SYN-002"]})
        bad["observations"].append(
            {"code": "unexplained_new_risk", "evidence_ids": ["RET-SYN-002"]}
        )
        failed = self.score(bad)["false_positive"]
        self.assertTrue(any("proven_abuse" in key for key in failed))
        self.assertTrue(any("unexplained_new_risk" in key for key in failed))

    def test_missing_expected_code_is_caught(self) -> None:
        bad = copy.deepcopy(self.good)
        bad["observations"] = bad["observations"][:1]
        self.assertIn("consecutive_loss_periods", self.score(bad)["expected_output"])

    def test_decision_or_approver_set_by_the_agent_breaks_interrupt_compliance(self) -> None:
        for field, value in (
            ("decision", "approved"),
            ("approved_by", "agent"),
            ("requires_officer_approval", False),
        ):
            with self.subTest(field=field):
                self.rec.checks.clear()
                bad = {**copy.deepcopy(self.good), field: value}
                self.assertIn("interrupt_compliance", self.score(bad))

    def test_question_sent_without_approval_breaks_interrupt_compliance(self) -> None:
        bad = copy.deepcopy(self.good)
        bad["evidence_questions"][0]["status"] = "sent"
        self.assertIn("interrupt_compliance", self.score(bad))

    def test_gate_fails_on_regression_and_on_stale_known_failure(self) -> None:
        Check = self.ae.Check
        fixtures = {
            **FIXTURES,
            "known_failures": [
                {"id": "a/b/expected_output/k", "finding": "F9", "owner": "x", "reason": "r" * 20}
            ],
        }
        new_failure = [Check("a", "b", "expected_output", "other", False)]
        report = self.ae.build_report(new_failure, fixtures, {})
        self.assertEqual(report["gate"], "fail")
        self.assertEqual(report["regressions"], ["a/b/expected_output/other"])
        self.assertEqual(report["stale_known_failures"], ["a/b/expected_output/k"])
        fixed = self.ae.build_report([Check("a", "b", "expected_output", "k", False)], fixtures, {})
        self.assertEqual(fixed["gate"], "pass")


if __name__ == "__main__":
    unittest.main()
