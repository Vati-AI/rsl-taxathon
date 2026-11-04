"""TAX-049 related-party playbook on the TAX-022 graph interface (model-free)."""

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
class RelatedPartyPlaybookTests(unittest.TestCase):
    def setUp(self) -> None:
        from ai.workflows.fake_graph import create_investigation_state
        from ai.workflows.refund_lanes import records_source
        from ai.workflows.related_party_playbook import (
            build_related_party_graph,
            make_related_party_tool,
        )

        self.records = {(TENANT, r["case_id"]): copy.deepcopy(r) for r in RECORDS}
        self.graph = build_related_party_graph(
            make_related_party_tool(records_source(self.records))
        )
        self.create_state = create_investigation_state

    def state(self, case_id: str, tenant_id: str = TENANT, max_steps: int = 1) -> dict:
        return self.create_state(
            tenant_id=tenant_id, case_id=case_id, run_id=f"run-{case_id}", max_steps=max_steps
        )

    def test_syn002_yields_one_advisory_finding_with_citations_and_counter_evidence(self) -> None:
        final = self.graph.invoke(self.state("SYN-002"))
        self.assertEqual(final["tool_results"][0]["status"], "ok")
        (finding,) = final["findings"]
        self.assertEqual(finding["kind"], "related_party_review_proposal")
        self.assertEqual(finding["code"], "related_party_transaction")
        self.assertEqual(finding["proposed_next_step"], "targeted_evidence_request")
        self.assertTrue(
            {"CTR-SYN-002-01", "INV-SYN-002-01", "RET-SYN-002", "PAY-SYN-002-01"}
            <= set(finding["evidence_ids"])
        )
        self.assertTrue(finding["counter_evidence"])
        self.assertEqual(
            {g["code"] for g in finding["evidence_gaps"]},
            {
                "service_delivery_evidence_missing",
                "service_benefit_evidence_missing",
                "loss_explanation_not_documented",
            },
        )
        self.assertIsNone(finding["legal_conclusion"])
        self.assertIsNone(finding["enforcement_conclusion"])
        self.assertIsNone(finding["decision"])
        self.assertIs(finding["requires_officer_approval"], True)
        self.assertNotIn("proposed_action", finding)
        self.assertIs(final["requires_human_review"], True)

    def test_other_scenarios_add_no_finding(self) -> None:
        for case_id in ("SYN-001", "SYN-003", "SYN-004", "SYN-005"):
            with self.subTest(case_id=case_id):
                final = self.graph.invoke(self.state(case_id))
                self.assertEqual(final["tool_results"][0]["status"], "ok")
                self.assertEqual(final["findings"], [])

    def test_policy_gap_gate_keeps_route_unverified_and_blocks_enforcement(self) -> None:
        from ai.workflows.legal_routes import load_registry
        from ai.workflows.policy_gap_gate import gate_finding

        (finding,) = self.graph.invoke(self.state("SYN-002"))["findings"]
        gated = gate_finding(
            {**finding, "proposed_action": "fraud_accusation"},
            registry=load_registry(),
            as_of=date(2025, 12, 31),
            case_evidence_ids=frozenset(finding["evidence_ids"]),
        )
        self.assertEqual(gated["legal_route"]["classification"], "unverified_route")
        self.assertIsNone(gated["enforcement_conclusion"])
        self.assertEqual(
            gated["blocked_actions"][0]["reason"], "enforcement_conclusion_not_permitted"
        )

    def test_other_tenant_cannot_resolve_the_case_records(self) -> None:
        final = self.graph.invoke(self.state("SYN-002", tenant_id="tenant-other"))
        self.assertEqual(final["tool_results"][0]["error"]["code"], "case_records_not_found")
        self.assertEqual(final["findings"], [])

    def test_malformed_record_fails_closed_without_leaking_content(self) -> None:
        record = self.records[(TENANT, "SYN-002")]
        record["payments"][0]["amount"] = 87200.0
        record["contracts"][0]["description"] = "Synthetic secret note"
        final = self.graph.invoke(self.state("SYN-002"))
        result = final["tool_results"][0]
        self.assertEqual(result["error"]["code"], "invalid_money")
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("87200", json.dumps(result))
        self.assertEqual(final["findings"], [])

    def test_replay_does_not_duplicate_the_proposal(self) -> None:
        first = self.graph.invoke(self.state("SYN-002", max_steps=2))
        replay = self.graph.invoke(first)
        self.assertEqual(len(replay["findings"]), 1)


if __name__ == "__main__":
    unittest.main()
