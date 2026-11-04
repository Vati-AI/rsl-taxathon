"""TAX-050 imported-services playbook on the TAX-022 graph interface (model-free)."""

import copy
import importlib.util
import json
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORDS = json.loads((ROOT / "data/synthetic/tax024_records.json").read_text())
TENANT = "tenant-synthetic"
HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


@unittest.skipUnless(HAS_LANGGRAPH, "LangGraph not installed; runs in the CI ai-smoke job")
class ImportedServicesPlaybookTests(unittest.TestCase):
    def setUp(self) -> None:
        from ai.workflows.fake_graph import create_investigation_state
        from ai.workflows.imported_services_playbook import (
            build_imported_services_graph,
            make_imported_services_tool,
        )
        from ai.workflows.refund_lanes import records_source

        self.records = {(TENANT, r["case_id"]): copy.deepcopy(r) for r in RECORDS}
        self.graph = build_imported_services_graph(
            make_imported_services_tool(records_source(self.records))
        )
        self.create_state = create_investigation_state

    def state(self, case_id: str, tenant_id: str = TENANT, max_steps: int = 1) -> dict:
        return self.create_state(
            tenant_id=tenant_id, case_id=case_id, run_id=f"run-{case_id}", max_steps=max_steps
        )

    def test_syn004_gap_yields_advisory_targeted_evidence_request(self) -> None:
        final = self.graph.invoke(self.state("SYN-004"))
        self.assertEqual(final["tool_results"][0]["status"], "ok")
        (finding,) = final["findings"]
        self.assertEqual(finding["kind"], "imported_services_evidence_request_proposal")
        self.assertEqual(finding["code"], "imported_service_treatment_unverified")
        self.assertEqual(finding["proposed_next_step"], "targeted_evidence_request")
        self.assertEqual(
            {q["topic"] for q in finding["evidence_questions"]},
            {"vat_treatment", "withholding", "service_performance"},
        )
        self.assertEqual(
            finding["statutory_treatment"]["status"], "unverified_pending_expert_review"
        )
        self.assertIsNone(finding["statutory_treatment"]["legal_conclusion"])
        self.assertIsNone(finding["enforcement_conclusion"])
        self.assertIsNone(finding["decision"])
        self.assertIs(finding["requires_officer_approval"], True)
        self.assertIs(final["requires_human_review"], True)
        self.assertNotIn("proposed_action", finding)  # leaves the gate's action slot empty

    def test_scenarios_without_imported_services_add_no_finding(self) -> None:
        for case_id in ("SYN-001", "SYN-002", "SYN-003", "SYN-005"):
            with self.subTest(case_id=case_id):
                final = self.graph.invoke(self.state(case_id))
                self.assertEqual(final["tool_results"][0]["status"], "ok")
                self.assertEqual(final["findings"], [])
                self.assertIs(final["requires_human_review"], False)

    def test_policy_gap_gate_keeps_statutory_route_unverified_and_blocks_enforcement(self) -> None:
        from ai.workflows.legal_routes import load_registry
        from ai.workflows.policy_gap_gate import gate_finding

        final = self.graph.invoke(self.state("SYN-004"))
        finding = {**final["findings"][0], "proposed_action": "proven_abuse"}
        gated = gate_finding(
            finding,
            registry=load_registry(),
            as_of=date(2025, 12, 31),
            case_evidence_ids=frozenset(finding["evidence_ids"]),
        )
        self.assertEqual(gated["legal_route"]["classification"], "unverified_route")
        self.assertFalse(gated["human_legal_review_eligible"])
        self.assertIsNone(gated["enforcement_conclusion"])
        self.assertEqual(
            gated["blocked_actions"][0]["reason"], "enforcement_conclusion_not_permitted"
        )

    def test_other_tenant_cannot_resolve_the_case_records(self) -> None:
        final = self.graph.invoke(self.state("SYN-004", tenant_id="tenant-other"))
        result = final["tool_results"][0]
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "case_records_not_found")
        self.assertEqual(final["findings"], [])
        self.assertIs(final["requires_human_review"], True)

    def test_malformed_record_fails_closed_without_leaking_content(self) -> None:
        self.records[(TENANT, "SYN-004")]["payments"][1]["amount"] = 50000.0
        final = self.graph.invoke(self.state("SYN-004"))
        result = final["tool_results"][0]
        self.assertEqual(result["error"]["code"], "invalid_money")
        self.assertNotIn("50000", json.dumps(result))
        self.assertEqual(final["findings"], [])

    def test_replay_does_not_duplicate_the_proposal(self) -> None:
        first = self.graph.invoke(self.state("SYN-004", max_steps=2))
        replay = self.graph.invoke(first)
        self.assertEqual(replay["step_count"], 2)
        self.assertEqual(len(replay["findings"]), 1)

    def test_exhausted_step_budget_stops_without_running_the_tool(self) -> None:
        first = self.graph.invoke(self.state("SYN-004", max_steps=1))
        again = self.graph.invoke(first)
        self.assertEqual(again["tool_results"][-1]["error"]["code"], "step_budget_exhausted")
        self.assertEqual(len(again["findings"]), 1)


if __name__ == "__main__":
    unittest.main()
