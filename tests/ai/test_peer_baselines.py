"""TAX-067 explainable taxpayer baselines and peer-group comparisons (model-free)."""

import json
import unittest
from decimal import Decimal

from ai.tools.evidence_search import SearchDenied
from ai.workflows import peer_baselines as pb

TENANT = "tenant-synthetic"
OFFICER = {
    "actor_id": "officer-a",
    "tenant_id": TENANT,
    "roles": ["auditor"],
    "authenticated": True,
}
GOOD = {"complete": True, "amended_unreconciled": False, "source_verified": True}


def row(
    taxpayer,
    period,
    refund,
    turnover="100000.00",
    input_vat=None,
    *,
    region="maseru",
    source="filed_return",
    quality=None,
    sector="retail",
    size="small",
):
    return {
        "tenant_id": TENANT,
        "taxpayer_id": taxpayer,
        "period": period,
        "sector": sector,
        "size_band": size,
        "region": region,
        "data_source": source,
        "amounts": {
            "refund_claim": refund,
            "turnover": turnover,
            "input_vat_claimed": input_vat or refund,
        },
        "data_quality": quality or dict(GOOD),
    }


def population(
    subject_refund="15000.00", peers=12, history=("2024-Q4", "2025-Q1", "2025-Q2", "2025-Q3")
):
    regions = ["maseru", "leribe", "mafeteng", "berea"]
    rows = [row("SYN-TP-SUBJECT", "2025-Q4", subject_refund)]
    rows += [row("SYN-TP-SUBJECT", p, f"{2000 + 100 * i}.00") for i, p in enumerate(history)]
    rows += [
        row(f"SYN-TP-PEER-{i:02d}", "2025-Q4", f"{1500 + 150 * i}.00", region=regions[i % 4])
        for i in range(peers)
    ]
    return rows


def run(rows, **kw):
    args = {
        "actor": OFFICER,
        "tenant_id": TENANT,
        "taxpayer_id": "SYN-TP-SUBJECT",
        "period": "2025-Q4",
    }
    return pb.compare(rows, **{**args, **kw})


class PeerBaselineTests(unittest.TestCase):
    def test_explainable_baseline_and_peer_comparison(self) -> None:
        result = run(population())
        refund = result["comparisons"]["refund_to_turnover"]
        base, peer = refund["baseline"], refund["peer_group"]
        self.assertEqual((base["state"], peer["state"]), ("compared", "compared"))
        self.assertEqual(base["n"], 4)
        self.assertEqual(peer["n"], 12)
        self.assertEqual(peer["subject_value"], "0.1500")
        self.assertEqual(peer["position"], "outside_typical_range")
        self.assertEqual(peer["direction"], "above")
        self.assertEqual(peer["percentile_rank"], "100.0")
        self.assertIn("12 peers in the same sector and size band", peer["explanation"])
        self.assertEqual(
            peer["group"], {"sector": "retail", "size_band": "small", "period": "2025-Q4"}
        )
        self.assertEqual(peer["data_quality"]["used"], 12)
        self.assertIn("alternative_explanations", refund)

    def test_statistics_are_exact_decimals_and_robust(self) -> None:
        values = [Decimal(v) for v in ("0.0100", "0.0200", "0.0300", "0.0400", "1.0000")]
        stats = pb._describe(values, Decimal("0.0300"))
        self.assertEqual(
            (stats["q1"], stats["median"], stats["q3"]), ("0.0200", "0.0300", "0.0400")
        )
        self.assertEqual(
            stats["position"], "within_typical_range"
        )  # one outlier does not move the median
        self.assertEqual(stats["percentile_rank"], "50.0")

    def test_within_range_subject_has_no_alternative_explanations_block(self) -> None:
        result = run(population(subject_refund="2300.00"))
        refund = result["comparisons"]["refund_to_turnover"]
        self.assertEqual(refund["peer_group"]["position"], "within_typical_range")
        self.assertNotIn("alternative_explanations", refund)

    def test_small_samples_yield_insufficient_evidence(self) -> None:
        result = run(population(peers=9, history=("2025-Q2", "2025-Q3")))
        refund = result["comparisons"]["refund_to_turnover"]
        self.assertEqual(refund["baseline"]["state"], "insufficient_evidence")
        self.assertIn("history_sample_too_small", refund["baseline"]["reasons"])
        self.assertEqual(refund["peer_group"]["state"], "insufficient_evidence")
        self.assertIn("peer_sample_too_small", refund["peer_group"]["reasons"])
        self.assertNotIn("median", refund["peer_group"])

    def test_poor_data_quality_yields_insufficient_evidence(self) -> None:
        rows = population(peers=15)
        for r in rows[5:9]:  # 4 of 15 peers (27%) unverified
            r["data_quality"] = {**GOOD, "source_verified": False}
        peer = run(rows)["comparisons"]["refund_to_turnover"]["peer_group"]
        self.assertEqual(peer["state"], "insufficient_evidence")
        self.assertIn("data_quality_exclusions_too_high", peer["reasons"])
        self.assertEqual(peer["data_quality"]["excluded"], {"unverified_source": 4})

    def test_skewed_peer_group_yields_insufficient_evidence(self) -> None:
        rows = [r for r in population(peers=0)]
        rows += [
            row(
                f"SYN-TP-PEER-{i:02d}", "2025-Q4", "2000.00", region="maseru" if i < 11 else "berea"
            )
            for i in range(12)
        ]
        peer = run(rows)["comparisons"]["refund_to_turnover"]["peer_group"]
        self.assertEqual(peer["state"], "insufficient_evidence")
        self.assertIn("peer_group_skewed_by_region", peer["reasons"])

    def test_peers_are_matched_on_sector_size_and_period_only(self) -> None:
        rows = population(peers=10)
        rows += [row(f"SYN-TP-OTHER-{i}", "2025-Q4", "90000.00", sector="mining") for i in range(5)]
        rows += [row(f"SYN-TP-LATER-{i}", "2026-Q1", "90000.00") for i in range(5)]
        peer = run(rows)["comparisons"]["refund_to_turnover"]["peer_group"]
        self.assertEqual(peer["n"], 10)

    def test_zero_turnover_is_undefined_not_zero(self) -> None:
        rows = population()
        rows[0]["amounts"]["turnover"] = "0.00"
        refund = run(rows)["comparisons"]["refund_to_turnover"]
        self.assertEqual(refund["peer_group"]["reasons"], ["subject_undefined_ratio"])

    def test_output_is_aggregate_neutral_and_never_a_fraud_conclusion(self) -> None:
        result = run(population())
        text = json.dumps(result)
        self.assertNotIn("SYN-TP-PEER", text)  # no peer identities
        self.assertIsNone(result["risk_score"])
        self.assertIsNone(result["fraud_conclusion"])
        lowered = text.lower()
        for word in ("fraud indicator", "suspicious", "evasion", "abuse", "high risk"):
            self.assertNotIn(word, lowered.replace("not a risk score, a fraud indicator", ""))

    def test_access_and_input_guards_fail_closed(self) -> None:
        with self.assertRaises(SearchDenied):
            run(population(), actor={**OFFICER, "roles": ["taxpayer"]})
        cases = {
            "tenant_mismatch": lambda rows: rows[3].__setitem__("tenant_id", "tenant-other"),
            "duplicate_taxpayer_period": lambda rows: rows.append(dict(rows[0])),
            "invalid_money": lambda rows: rows[2]["amounts"].__setitem__("refund_claim", 15000.0),
            "invalid_period": lambda rows: rows[2].__setitem__("period", "2025-13"),
        }
        for code, mutate in cases.items():
            with self.subTest(code=code):
                rows = population()
                mutate(rows)
                with self.assertRaises(pb.BaselineInputError) as raised:
                    run(rows)
                self.assertEqual(raised.exception.code, code)
        with self.assertRaises(pb.BaselineInputError) as raised:
            run(population(), taxpayer_id="SYN-TP-MISSING")
        self.assertEqual(raised.exception.code, "subject_not_found")


if __name__ == "__main__":
    unittest.main()
