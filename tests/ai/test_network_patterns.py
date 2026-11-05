"""TAX-075 emerging network pattern review on real TAX-074 case graphs (model-free)."""

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integration" / "followthemoney"))
sys.path.insert(0, str(ROOT / "backend" / "vati_tax"))

import scheme_graph as sg  # noqa: E402

from ai.tools.evidence_search import SearchDenied  # noqa: E402
from ai.workflows import network_patterns as np_  # noqa: E402

INPUTS = json.loads((ROOT / "data/synthetic/tax074_scheme_graph.json").read_text(encoding="utf-8"))[
    "inputs"
]
TENANT = INPUTS[0]["tenant_id"]
ANALYST = {
    "actor_id": "officer-an",
    "tenant_id": TENANT,
    "roles": ["specialist"],
    "authenticated": True,
}


def clone(base: dict, n: int) -> dict:
    data = copy.deepcopy(base)
    data["case_id"] = f"74000000-0000-4000-8000-{n:012d}"
    return data


def graphs(*inputs: dict) -> list[dict]:
    return [sg.build_case_graph(d) for d in inputs]


def full_case(n: int) -> dict:
    return clone(INPUTS[0], 100 + n)  # fully cited case with adviser, beneficial owner, scheme


def detect(current, baseline=(), actor=ANALYST):
    return np_.detect_patterns(current, baseline_graphs=baseline, actor=actor, tenant_id=TENANT)


class NetworkPatternTests(unittest.TestCase):
    def test_recurring_pattern_is_queued_with_explainable_evidence(self) -> None:
        result = detect(graphs(full_case(1), full_case(2), full_case(3)))
        kinds = {i["pattern_type"] for i in result["review_queue"]}
        self.assertTrue(
            {"shared_intermediary", "common_beneficial_owner", "multi_case_scheme"} <= kinds
        )
        item = next(i for i in result["review_queue"] if i["pattern_type"] == "shared_intermediary")
        self.assertEqual(item["case_count"], 3)
        self.assertEqual(item["status"], "queued_for_analyst_review")
        self.assertEqual(
            item["review"], {"route": "analyst_review", "decided_by": "analyst", "outcome": None}
        )
        for edge in item["supporting_edges"]:
            self.assertEqual(edge["evidence_status"], "cited")
            self.assertTrue(edge["evidence"])
            self.assertTrue(edge["source_record"])
        self.assertIsNone(item["enforcement_action"])
        self.assertIs(item["novelty_alone_sufficient"], False)

    def test_below_threshold_patterns_are_not_raised(self) -> None:
        result = detect(graphs(full_case(1), full_case(2)))
        kinds = {i["pattern_type"] for i in result["review_queue"]}
        self.assertNotIn("shared_intermediary", kinds)  # 2 < MIN_CASES
        self.assertIn("common_beneficial_owner", kinds)  # MIN_OWNER_CASES is 2

    def test_novelty_is_reported_but_never_sufficient(self) -> None:
        current = graphs(full_case(1), full_case(2), full_case(3))
        novel = detect(current)
        self.assertTrue(all(i["novel"] for i in novel["review_queue"]))
        seen = detect(current, baseline=graphs(full_case(11), full_case(12), full_case(13)))
        self.assertTrue(all(i["novel"] is False for i in seen["review_queue"]))
        self.assertEqual(
            len(seen["review_queue"]), len(novel["review_queue"])
        )  # novelty changes no status

    def test_uncited_cases_give_insufficient_evidence_with_source_gaps(self) -> None:
        current = graphs(full_case(1), full_case(2), full_case(3))
        for edge in current[2]["edges"].values():  # one case's adviser edges lose their citations
            if edge["type"] in ("adviser_to", "intermediary_for"):
                edge["evidence_status"] = "source_record_only"
                edge["provenance"]["evidence"] = []
        result = detect(current)
        kinds = {i["pattern_type"] for i in result["insufficient_evidence"]}
        self.assertIn("shared_intermediary", kinds)
        self.assertNotIn("shared_intermediary", {i["pattern_type"] for i in result["review_queue"]})
        for item in result["insufficient_evidence"]:
            self.assertEqual(item["reason"], "not_every_case_has_cited_evidence")
            self.assertIn("edge_without_cited_evidence", {g["gap"] for g in item["source_gaps"]})
            self.assertNotIn("review", item)

    def test_circular_ownership_is_detected_within_a_case(self) -> None:
        g = sg.build_case_graph(full_case(1))
        owned = [e for e in g["edges"].values() if e["type"] in np_.OWNERSHIP_TYPES]
        first = owned[0]
        loop = copy.deepcopy(first)
        loop["edge_id"] = "ge-synthetic-loop"
        loop["source"], loop["target"] = first["target"], first["source"]
        g["edges"]["ge-synthetic-loop"] = loop
        result = detect([g])
        cycles = [i for i in result["review_queue"] if i["pattern_type"] == "circular_ownership"]
        self.assertEqual(len(cycles), 1)
        self.assertEqual(
            {e["edge_id"] for e in cycles[0]["supporting_edges"]}
            >= {first["edge_id"], "ge-synthetic-loop"},
            True,
        )

    def test_output_never_labels_parties(self) -> None:
        result = detect(
            graphs(
                full_case(1),
                full_case(2),
                full_case(3),
                *[clone(INPUTS[1], 300 + i) for i in range(3)],
            )
        )
        text = json.dumps(result)
        self.assertEqual(sg.labelling_terms(text.replace(np_.NOTICE, "")), [])
        for item in result["review_queue"] + result["insufficient_evidence"]:
            self.assertNotIn("score", item)
            self.assertNotIn("risk", item)

    def test_false_positives_and_source_gaps_are_evaluated(self) -> None:
        result = detect(graphs(full_case(1), full_case(2), full_case(3)))
        queue = result["review_queue"]
        reviews = {
            queue[0]["pattern_id"]: "explained_legitimate",
            queue[1]["pattern_id"]: "warranted_further_review",
        }
        report = np_.evaluate_reviews(queue, reviews)
        totals = {k: v for k, v in report.items()}
        reviewed = sum(v["reviewed"] for v in totals.values())
        self.assertEqual(reviewed, 2)
        fp_types = [k for k, v in totals.items() if v["explained_legitimate"]]
        self.assertEqual(len(fp_types), 1)
        self.assertEqual(totals[fp_types[0]]["false_positive_share"], "1/1")
        for row in totals.values():
            self.assertRegex(row["source_gap_share"], r"^\d+/\d+$")
        with self.assertRaises(np_.PatternInputError):
            np_.evaluate_reviews(queue, {queue[0]["pattern_id"]: "guilty"})

    def test_access_scope_and_determinism(self) -> None:
        with self.assertRaises(SearchDenied):
            detect(graphs(full_case(1)), actor={**ANALYST, "roles": ["taxpayer"]})
        other = graphs(INPUTS[2]) if INPUTS[2]["tenant_id"] != TENANT else None
        if other:
            with self.assertRaises(np_.PatternInputError) as raised:
                detect(graphs(full_case(1)) + other)
            self.assertEqual(raised.exception.code, "tenant_mismatch")
        current = graphs(full_case(1), full_case(2), full_case(3))
        self.assertEqual(detect(current), detect(list(reversed(current)) + current[:1]))


if __name__ == "__main__":
    unittest.main()
