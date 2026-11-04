"""TAX-048 refund tool and lane nodes on the TAX-022 graph interface (model-free)."""

import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORDS = json.loads((ROOT / "data/synthetic/tax024_records.json").read_text())
TENANT = "tenant-synthetic"
HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


@unittest.skipUnless(HAS_LANGGRAPH, "LangGraph not installed; runs in the CI ai-smoke job")
class RefundLaneGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        from ai.workflows.fake_graph import create_investigation_state
        from ai.workflows.refund_lanes import (
            build_refund_lane_graph,
            make_refund_reconciliation_tool,
            records_source,
        )

        self.records = {(TENANT, r["case_id"]): copy.deepcopy(r) for r in RECORDS}
        self.tool = make_refund_reconciliation_tool(records_source(self.records))
        self.graph = build_refund_lane_graph(self.tool)
        self.create_state = create_investigation_state

    def state(self, case_id: str, tenant_id: str = TENANT) -> dict:
        return self.create_state(
            tenant_id=tenant_id, case_id=case_id, run_id=f"run-{case_id}", max_steps=1
        )

    def test_each_scenario_produces_one_advisory_lane_finding_for_officer_review(self) -> None:
        expected = {
            "SYN-001": "fast",
            "SYN-002": "targeted",
            "SYN-003": None,
            "SYN-004": "targeted",
            "SYN-005": "standard",
        }
        for case_id, lane in expected.items():
            with self.subTest(case_id=case_id):
                final = self.graph.invoke(self.state(case_id))
                self.assertEqual(final["step_count"], 1)
                self.assertEqual(final["tool_results"][0]["status"], "ok")
                self.assertEqual(final["tool_results"][0]["case_id"], case_id)
                (finding,) = final["findings"]
                self.assertEqual(finding["kind"], "refund_lane_proposal")
                self.assertEqual(finding["proposed_lane"], lane)
                self.assertEqual(finding["case_id"], case_id)
                self.assertIs(finding["requires_officer_approval"], True)
                self.assertIsNone(finding["decision"])
                self.assertIs(final["requires_human_review"], True)

    def test_amounts_flow_through_the_tool_envelope_as_decimal_strings(self) -> None:
        final = self.graph.invoke(self.state("SYN-002"))
        data = final["tool_results"][0]["data"]
        self.assertEqual(data["claimed_refund"], {"amount": "25000.00", "currency": "LSL"})
        self.assertEqual(data["explained_amount"], {"amount": "10000.00", "currency": "LSL"})
        self.assertEqual(data["unresolved_amount"], {"amount": "15000.00", "currency": "LSL"})
        self.assertEqual(final["tool_results"][0]["tool_name"], "refund_reconciliation_tool")

    def test_other_tenant_cannot_resolve_records_and_stops_for_review(self) -> None:
        final = self.graph.invoke(self.state("SYN-001", tenant_id="tenant-other"))
        self.assertEqual(final["tool_results"][0]["error"]["code"], "refund_record_not_found")
        self.assertEqual(final["findings"], [])
        self.assertIs(final["requires_human_review"], True)

    def test_record_for_another_case_is_rejected_as_scope_mismatch(self) -> None:
        self.records[(TENANT, "SYN-009")] = copy.deepcopy(self.records[(TENANT, "SYN-001")])
        final = self.graph.invoke(self.state("SYN-009"))
        self.assertEqual(final["tool_results"][0]["error"]["code"], "tool_scope_mismatch")
        self.assertEqual(final["findings"], [])

    def test_invalid_money_fails_closed_without_leaking_record_content(self) -> None:
        self.records[(TENANT, "SYN-001")]["invoices"][0]["vat_amount"] = 9000.0
        final = self.graph.invoke(self.state("SYN-001"))
        result = final["tool_results"][0]
        self.assertEqual(result["error"]["code"], "invalid_money")
        self.assertNotIn("9000", json.dumps(result))
        self.assertEqual(final["findings"], [])
        self.assertIs(final["requires_human_review"], True)

    def test_replay_is_deterministic_and_states_stay_isolated(self) -> None:
        first = self.graph.invoke(self.state("SYN-004"))
        second = self.graph.invoke(self.state("SYN-004"))
        self.assertEqual(first["findings"], second["findings"])
        other = self.graph.invoke(self.state("SYN-001"))
        self.assertEqual(other["findings"][0]["case_id"], "SYN-001")

    def test_tool_is_usable_with_the_tax022_fake_graph_harness(self) -> None:
        from ai.workflows.fake_graph import build_fake_tool_graph

        final = build_fake_tool_graph(self.tool).invoke(self.state("SYN-001"))
        self.assertEqual(final["tool_results"][0]["status"], "ok")
        self.assertEqual(final["tool_results"][0]["data"]["unresolved_amount"]["amount"], "0.00")


if __name__ == "__main__":
    unittest.main()
