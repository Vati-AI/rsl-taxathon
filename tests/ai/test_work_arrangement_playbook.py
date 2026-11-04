"""TAX-051 specialist checkpoint playbook on a persistent SQLite checkpoint (model-free)."""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORDS = json.loads((ROOT / "data/synthetic/tax024_records.json").read_text())
TENANT = "tenant-synthetic"
HAS_SQLITE_SAVER = (
    importlib.util.find_spec("langgraph") is not None
    and importlib.util.find_spec("langgraph.checkpoint.sqlite") is not None
)
AGENT = "agent:tax051"


@unittest.skipUnless(HAS_SQLITE_SAVER, "LangGraph SQLite checkpointer not installed")
class WorkArrangementPlaybookTests(unittest.TestCase):
    def setUp(self) -> None:
        from ai.workflows import work_arrangement_playbook as wp
        from ai.workflows.fake_graph import create_investigation_state
        from ai.workflows.human_checkpoints import ResumeRejected, open_checkpointer, thread_config
        from ai.workflows.refund_lanes import records_source

        self.wp = wp
        self.ResumeRejected = ResumeRejected
        self.open_checkpointer = open_checkpointer
        self.thread_config = thread_config
        self.records = {(TENANT, r["case_id"]): copy.deepcopy(r) for r in RECORDS}
        self.source = records_source(self.records)
        self.create_state = create_investigation_state
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "checkpoints.sqlite"
        self.savers: list = []

    def tearDown(self) -> None:
        for saver in self.savers:
            saver.conn.close()
        self.tmp.cleanup()

    def worker(self):
        saver = self.open_checkpointer(self.db)
        self.savers.append(saver)
        return self.wp.build_work_arrangement_graph(
            self.wp.make_work_arrangement_tool(self.source), saver, agent_id=AGENT
        )

    def start(self, case_id: str = "SYN-003", tenant_id: str = TENANT):
        config = self.thread_config(tenant_id, case_id, f"run-{case_id}")
        state = {
            **self.create_state(tenant_id=tenant_id, case_id=case_id, run_id=f"run-{case_id}"),
            "case_version": 7,
        }
        return self.worker().invoke(state, config), config

    def opinion(self, config, **overrides):
        referral = self.wp.pending_referral(self.worker(), config)
        opinion = {
            "tenant_id": TENANT,
            "case_id": "SYN-003",
            "specialist_id": "officer-specialist-s",
            "case_version": referral["case_version"],
            "facts_digest": referral["facts_digest"],
            "classification": "undetermined",
            "rationale": "Synthetic: working arrangement facts are insufficient to classify.",
            "evidence_ids": ["CTR-SYN-003-01"],
        }
        return {**opinion, **overrides}

    def actor(self, **overrides):
        actor = {
            "actor_id": "officer-specialist-s",
            "tenant_id": TENANT,
            "roles": ["specialist"],
            "authenticated": True,
        }
        return {**actor, **overrides}

    def test_syn003_pauses_for_specialist_with_unresolved_classification(self) -> None:
        paused, config = self.start()
        self.assertTrue(paused["__interrupt__"])
        (finding,) = paused["findings"]
        self.assertEqual(finding["kind"], "specialist_referral")
        self.assertEqual(finding["code"], "service_classification_evidence_gap")
        self.assertIsNone(finding["classification"])
        self.assertIs(finding["auto_classification_permitted"], False)
        self.assertIsNone(finding["assessment"])
        self.assertIsNone(finding["decision"])
        self.assertNotIn("specialist_opinion", paused)
        referral = self.wp.pending_referral(self.worker(), config)
        self.assertEqual(referral["case_version"], 7)
        self.assertEqual(referral["proposed_by"], AGENT)
        self.assertTrue({"CTR-SYN-003-01", "INV-SYN-003-01"} <= set(referral["evidence_ids"]))

    def test_other_scenarios_finish_without_referral(self) -> None:
        for case_id in ("SYN-001", "SYN-002", "SYN-004", "SYN-005"):
            with self.subTest(case_id=case_id):
                final, config = self.start(case_id)
                self.assertNotIn("__interrupt__", final)
                self.assertEqual(final["findings"], [])
                self.assertIsNone(self.wp.pending_referral(self.worker(), config))

    def test_restarted_worker_records_specialist_opinion_with_attribution(self) -> None:
        _, config = self.start()
        final = self.wp.resume_with_specialist_opinion(
            self.worker(), config, self.opinion(config), self.actor()
        )
        opinion = final["specialist_opinion"]
        self.assertEqual(opinion["status"], "specialist_opinion_recorded")
        self.assertEqual(opinion["classification"], "undetermined")
        self.assertEqual(opinion["specialist_id"], "officer-specialist-s")
        self.assertIs(opinion["decided_by_agent"], False)
        self.assertIsNone(opinion["assessment"])
        self.assertEqual(opinion["next_step"], "officer_decision_required")
        # The agent's own referral is unchanged: it never classified.
        self.assertIsNone(final["findings"][0]["classification"])

    def test_unauthorized_or_stale_resumes_are_rejected_and_run_stays_paused(self) -> None:
        _, config = self.start()
        cases = {
            "not_authenticated": ({}, {"authenticated": False}),
            "missing_identity": ({}, {"actor_id": ""}),
            "tenant_mismatch": ({}, {"tenant_id": "tenant-other"}),
            "case_mismatch": ({"case_id": "SYN-002"}, {}),
            "role_not_permitted": ({}, {"roles": ["supervisor", "auditor"]}),
            "actor_not_specialist": ({"specialist_id": "officer-specialist-t"}, {}),
            "self_approval": ({"specialist_id": AGENT}, {"actor_id": AGENT}),
            "stale_case_version": ({"case_version": 6}, {}),
            "facts_mismatch": ({"facts_digest": "0" * 64}, {}),
            "invalid_classification": ({"classification": "tax_evasion"}, {}),
            "rationale_required": ({"rationale": "ok"}, {}),
            "citation_not_in_referral": ({"evidence_ids": ["RET-SYN-001"]}, {}),
        }
        for code, (opinion_changes, actor_changes) in cases.items():
            with self.subTest(code=code):
                with self.assertRaises(self.ResumeRejected) as raised:
                    self.wp.resume_with_specialist_opinion(
                        self.worker(),
                        config,
                        self.opinion(config, **opinion_changes),
                        self.actor(**actor_changes),
                    )
                self.assertEqual(raised.exception.code, code)
                self.assertIsNotNone(self.wp.pending_referral(self.worker(), config))
        final = self.wp.resume_with_specialist_opinion(
            self.worker(), config, self.opinion(config), self.actor()
        )
        self.assertEqual(final["specialist_opinion"]["status"], "specialist_opinion_recorded")

    def test_direct_command_resume_cannot_bypass_validation(self) -> None:
        from langgraph.types import Command

        _, config = self.start()
        out = self.worker().invoke(
            Command(
                resume={"opinion": self.opinion(config), "actor": self.actor(roles=["auditor"])}
            ),
            config,
        )
        self.assertTrue(out["__interrupt__"])
        self.assertEqual(out["__interrupt__"][0].value["last_rejection"], "role_not_permitted")
        self.assertNotIn("specialist_opinion", out)

    def test_second_resume_after_opinion_is_refused(self) -> None:
        _, config = self.start()
        opinion = self.opinion(config)
        self.wp.resume_with_specialist_opinion(self.worker(), config, opinion, self.actor())
        with self.assertRaises(self.ResumeRejected) as raised:
            self.wp.resume_with_specialist_opinion(self.worker(), config, opinion, self.actor())
        self.assertEqual(raised.exception.code, "no_pending_checkpoint")

    def test_other_tenant_cannot_see_or_resume_the_referral(self) -> None:
        self.start()
        other = self.thread_config("tenant-other", "SYN-003", "run-SYN-003")
        self.assertIsNone(self.wp.pending_referral(self.worker(), other))
        final, _ = self.start(tenant_id="tenant-other")
        self.assertEqual(final["tool_results"][0]["error"]["code"], "case_records_not_found")
        self.assertNotIn("__interrupt__", final)


if __name__ == "__main__":
    unittest.main()
