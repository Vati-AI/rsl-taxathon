"""TAX-118 population generator, blind pipeline boundary and scorer (small population)."""

from __future__ import annotations

import json
import unittest

from ai.evaluation.assurance_benchmark import canonical_sha256, run_system, score, simple_rule
from ai.evaluation.assurance_population import generate

PARAMS = {
    "n_taxpayers": 24,
    "n_dev": 8,
    "issue_counts": {
        "fake_invoices": 2,
        "untaxed_imported_services": 2,
        "disguised_employment": 1,
        "related_party_loss": 1,
        "policy_gap": 1,
        "mixed": 1,
    },
}


class PopulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world, cls.key = generate(5, PARAMS)

    def test_same_seed_reproduces_world_and_key(self):
        world, key = generate(5, PARAMS)
        self.assertEqual(canonical_sha256(world), canonical_sha256(self.world))
        self.assertEqual(canonical_sha256(key), canonical_sha256(self.key))

    def test_world_carries_no_answer_key_fields_and_is_synthetic(self):
        text = json.dumps(self.world)
        for secret in ("policy_lane", "fake_vat", "fake_strategy", "amount_at_risk"):
            self.assertNotIn(secret, text)
        self.assertTrue(self.world["synthetic"])

    def test_split_sizes_and_clean_share(self):
        splits = [v["split"] for v in self.key["taxpayers"].values()]
        self.assertEqual(splits.count("dev"), 8)
        clean = sum(not v["issues"] for v in self.key["taxpayers"].values())
        self.assertEqual(clean, 24 - 8)

    def test_coverage_changes_only_lekuka_records(self):
        other, _ = generate(5, {**PARAMS, "lekuka_coverage": "0.90"})
        for kind, rows in self.world["records"].items():
            if kind.startswith("idms."):
                continue
            if kind == "online_refunds.claim":
                strip = [{k: v for k, v in r.items() if k != "uploaded_documents"} for r in rows]
                other_strip = [
                    {k: v for k, v in r.items() if k != "uploaded_documents"}
                    for r in other["records"][kind]
                ]
                self.assertEqual(strip, other_strip)
                continue
            self.assertEqual(rows, other["records"][kind], kind)

    def test_scoring_runs_and_no_finding_is_assessment_eligible(self):
        tins = sorted(self.key["taxpayers"])
        results = run_system(self.world, tins)
        report = score(results, self.key)
        self.assertFalse(report["legal_routes"]["any_finding_eligible_for_assessment"])
        self.assertEqual(report["taxpayers"], 24)
        for r in results.values():
            self.assertIsNone(r["decision"])
            for c in r["claims"]:
                self.assertIsNone(c["lane"]["decision"])
        base = simple_rule(results, self.key, "500000.00")
        self.assertIn("clean_taxpayers_flagged", base)


if __name__ == "__main__":
    unittest.main()
