"""TAX-070 outcome and appeal feedback loop (model-free)."""

import copy
import importlib.util
import json
import unittest

from ai.workflows import outcome_feedback as of

TENANT = "tenant-synthetic"
HAS_SQLITE_SAVER = (
    importlib.util.find_spec("langgraph") is not None
    and importlib.util.find_spec("langgraph.checkpoint.sqlite") is not None
)


def playbook_stamp(name="related_party", version="1.0"):
    if HAS_SQLITE_SAVER:  # real TAX-061 registry stamp
        from ai.workflows.playbook_registry import load_registry

        registry = load_registry()
        return {
            **registry.require_approved("playbook", name, version).stamp(registry.registry_version)
        }
    return {
        "kind": "playbook",
        "name": name,
        "version": version,
        "content_digest": "c" * 64,
        "registry_version": "SYN-PBR-2026.11.0",
    }


RULE = {"kind": "rule", "rule_id": "TR-RELATED-PARTY-FEES", "ruleset_version": "1.0.0"}
AUTHORITY = {"kind": "authority", "authority_id": "SYN-PLACEHOLDER-AUTH-002", "version": "2025.1"}


def item(n, outcome, provenance, *, stage="appeal", unchanged=True):
    decision_id = f"dec-{n:03d}"
    final = (
        None
        if outcome is None
        else {"challenge_id": f"chl-{n:03d}", "stage": stage, "outcome": outcome}
    )
    return {
        "tenant_id": TENANT,
        "history": {
            "decision": {
                "decision_id": decision_id,
                "case_id": f"case-{n:03d}",
                "status": "approved",
                "outcome": "request_further_evidence",
                "decision_digest": f"{n:064x}",
            },
            "challenges": [],
            "original_decision_unchanged": unchanged,
            "final_outcome": final,
            "legal_route_verified": False,
        },
        "provenance": copy.deepcopy(provenance),
    }


def scenario():
    stamp = playbook_stamp()
    return [
        item(1, "overturned", [stamp, RULE, AUTHORITY]),
        item(2, "partially_overturned", [stamp, RULE]),
        item(3, "upheld", [stamp, RULE, AUTHORITY]),
        item(4, "overturned", [stamp, AUTHORITY]),
        item(5, "upheld", [RULE]),
        item(6, "withdrawn", [stamp]),
        item(7, None, [stamp]),
        item(8, "overturned", [stamp], unchanged=False),
        item(9, "overturned", [AUTHORITY]),
    ]


class OutcomeFeedbackTests(unittest.TestCase):
    def test_overturned_component_gets_a_review_proposal_with_counter_evidence(self) -> None:
        result = of.evaluate_feedback(scenario(), tenant_id=TENANT)
        (playbook,) = [p for p in result["proposals"] if p["component"]["kind"] == "playbook"]
        self.assertEqual(playbook["kind"], "component_review_proposal")
        self.assertEqual(
            (playbook["final_outcomes"], playbook["overturned"], playbook["upheld"]), (4, 3, 1)
        )
        self.assertEqual(playbook["overturn_share"], "3/4")
        self.assertEqual(
            sorted(c["decision_id"] for c in playbook["overturned_cases"]),
            ["dec-001", "dec-002", "dec-004"],
        )
        self.assertEqual(
            [c["decision_id"] for c in playbook["counter_evidence_upheld"]], ["dec-003"]
        )
        self.assertEqual(playbook["status"], "proposed")
        self.assertIs(playbook["auto_apply"], False)
        self.assertIsNone(playbook["approved_by"])

    def test_below_threshold_components_are_reported_not_proposed(self) -> None:
        result = of.evaluate_feedback(scenario(), tenant_id=TENANT)
        rule = next(c for c in result["components"] if c["kind"] == "rule")
        self.assertEqual(
            (rule["final_outcomes"], rule["overturned"]), (4, 2)
        )  # 2/4 meets 1/2 share
        self.assertIn("rule", {p["component"]["kind"] for p in result["proposals"]})
        few = of.evaluate_feedback(scenario()[:2], tenant_id=TENANT)
        self.assertEqual(few["proposals"], [])  # fewer than 3 final outcomes per component
        self.assertTrue(few["components"])

    def test_legal_authority_is_only_referred_never_reinterpreted(self) -> None:
        result = of.evaluate_feedback(scenario(), tenant_id=TENANT)
        (authority,) = [p for p in result["proposals"] if p["component"]["kind"] == "authority"]
        self.assertEqual(authority["kind"], "legal_review_referral")
        self.assertIsNone(authority["legal_interpretation_change"])
        self.assertIs(authority["automatic_change_permitted"], False)
        self.assertIn("no interpretation is changed", authority["requested_action"])
        for proposal in result["proposals"]:
            with self.assertRaises(of.FeedbackInputError) as raised:
                of.apply(proposal)
            self.assertEqual(raised.exception.code, "automatic_change_not_permitted")
        self.assertEqual(result["applied_changes"], [])

    def test_exclusions_and_lineage_problems_are_reported(self) -> None:
        result = of.evaluate_feedback(scenario(), tenant_id=TENANT)
        self.assertEqual(result["excluded"], {"no_final_outcome": 1, "withdrawn": 1})
        self.assertEqual(result["lineage_problems"], ["dec-008"])
        counted = {c["decision_id"] for p in result["proposals"] for c in p["overturned_cases"]}
        self.assertNotIn("dec-008", counted)

    def test_evaluation_lineage_is_preserved_and_reproducible(self) -> None:
        first = of.evaluate_feedback(scenario(), tenant_id=TENANT)
        (playbook,) = [p for p in first["proposals"] if p["component"]["kind"] == "playbook"]
        lineage = playbook["lineage"]
        self.assertEqual(lineage["component"]["content_digest"], playbook_stamp()["content_digest"])
        self.assertEqual(
            lineage["component"]["registry_version"], playbook_stamp()["registry_version"]
        )
        self.assertIn(("dec-001", f"{1:064x}", "chl-001"), [tuple(d) for d in lineage["decisions"]])
        self.assertEqual(playbook["lineage_digest"], of._digest(lineage))
        shuffled = list(reversed(scenario())) + scenario()[:3]  # reordered + duplicate delivery
        self.assertEqual(
            of.evaluate_feedback(shuffled, tenant_id=TENANT)["proposals"], first["proposals"]
        )

    def test_different_versions_are_tracked_separately(self) -> None:
        old = {**playbook_stamp(), "version": "0.9", "content_digest": "d" * 64}
        items = scenario() + [item(20 + i, "overturned", [old]) for i in range(3)]
        result = of.evaluate_feedback(items, tenant_id=TENANT)
        versions = {
            (p["component"]["name"], p["component"]["version"]) for p in result["proposals"]
        }
        self.assertIn(("related_party", "0.9"), versions)
        self.assertIn(("related_party", "1.0"), versions)

    def test_no_taxpayer_identifiers_in_proposals(self) -> None:
        items = scenario()
        for entry in items:
            entry["history"]["challenges"] = [
                {"taxpayer_id": "SYN-TP-SECRET", "grounds": "synthetic"}
            ]
        text = json.dumps(of.evaluate_feedback(items, tenant_id=TENANT))
        self.assertNotIn("SYN-TP-SECRET", text)

    def test_invalid_input_fails_closed(self) -> None:
        cases = {
            "tenant_mismatch": lambda xs: xs[0].__setitem__("tenant_id", "tenant-other"),
            "invalid_outcome": lambda xs: xs[0]["history"]["final_outcome"].__setitem__(
                "outcome", "guilty"
            ),
            "invalid_provenance_kind": lambda xs: xs[0]["provenance"].append({"kind": "model"}),
            "invalid_provenance": lambda xs: xs[0]["provenance"][0].__setitem__(
                "content_digest", "x"
            ),
            "conflicting_component_lineage": lambda xs: xs[1]["provenance"][0].__setitem__(
                "content_digest", "e" * 64
            ),
        }
        for code, mutate in cases.items():
            with self.subTest(code=code):
                items = scenario()
                mutate(items)
                with self.assertRaises(of.FeedbackInputError) as raised:
                    of.evaluate_feedback(items, tenant_id=TENANT)
                self.assertEqual(raised.exception.code, code)


if __name__ == "__main__":
    unittest.main()
