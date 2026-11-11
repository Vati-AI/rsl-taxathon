"""Frappe-free tests for RSL audit referral rules (TAX-123).

The synthetic fixture (integration/rsl_sources/fixtures/audit_referrals.json) is served
through the TAX-111 fixture adapter, exactly as the site reads it.
"""

from __future__ import annotations

import copy
import importlib
import json
import pathlib
import sys
import unittest
from typing import Any

from vati_tax.rsl_referrals import rules

APP = pathlib.Path(__file__).resolve().parents[1]
REPO = APP.parents[2]
if str(REPO) not in sys.path:
    sys.path.append(str(REPO))
contracts: Any = None
fixture_source: Any = None
try:
    contracts = importlib.import_module("integration.rsl_sources.contracts")
    fixture_source = importlib.import_module("integration.rsl_sources.fixture_source")
except ImportError:  # pragma: no cover - integration/ is not on the path
    pass
# Next to the adapter, wherever integration/ is mounted (repository or bench vati_shared).
FIXTURE = (
    pathlib.Path(fixture_source.__file__).parent / "fixtures" / "audit_referrals.json"
    if fixture_source is not None
    else REPO / "integration" / "rsl_sources" / "fixtures" / "audit_referrals.json"
)

TIN_A = "510000006"
TIN_B = "510000014"


def load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@unittest.skipIf(fixture_source is None, "integration/ is not on the path")
class FixtureTests(unittest.TestCase):
    def setUp(self):
        doc = load()
        self.doc = doc
        records = {tuple(k.split(".", 1)): v for k, v in doc["records"].items()}
        self.sources = fixture_source.FixtureRslSources(
            doc["tenant_id"], records, retrieved_at=doc["retrieved_at"]
        )

    def read(self, tin: str) -> dict:
        return self.sources.read(rules.SYSTEM, rules.KIND, tenant_id=self.doc["tenant_id"],
                                 taxpayer=tin)  # fmt: skip

    def test_fixture_identifies_itself_and_follows_the_assumed_contract(self):
        self.assertTrue(self.doc["fixture"] and self.doc["synthetic"])
        self.assertIn("Assumed contract", self.doc["description"])
        envelope = self.read(TIN_A)
        self.assertTrue(envelope["fixture"])
        self.assertEqual(envelope["contract_id"], "rsl.audit_system.referral.v0-assumed")
        self.assertIn("not an RSL interface", envelope["contract_notice"])
        contract = contracts.contract_for(rules.SYSTEM, rules.KIND)
        for record in self.doc["records"]["audit_system.referral"]:
            contracts.validate_record(contract, record)
        text = json.dumps(self.doc).lower()
        for invented in ("http://", "https://", "psrm.", ".gov.ls", "/api/"):
            self.assertNotIn(invented, text)

    def test_plan_keeps_rsl_scores_as_rsl_inputs(self):
        status, referrals = rules.plan_referrals(self.read(TIN_A), tin=TIN_A)
        self.assertEqual(status, "ok")
        (referral,) = referrals
        self.assertEqual(referral.audit_case_ref, "SYN-AUD-2026-0001")
        self.assertEqual(referral.idempotency_key, "rsl:audit-referral:SYN-AUD-2026-0001")
        self.assertTrue(referral.fixture)
        self.assertEqual(len(referral.record_sha256), 64)
        inputs = referral.rsl_inputs
        self.assertEqual(inputs["owner"], "RSL")
        self.assertEqual({r["owner"] for r in inputs["risk_sets"]}, {"RSL"})
        self.assertEqual([r["risk_set_id"] for r in inputs["risk_sets"]],
                         ["SYN-RS-INPUT-VAT-TREND", "SYN-RS-VAT-REFUND"])  # fmt: skip
        self.assertIn("Not a Vati score", inputs["note"])
        self.assertNotIn("vati_score", json.dumps(inputs))

    def test_a_taxpayer_may_have_several_referrals_and_none_without_scores_is_lost(self):
        _, referrals = rules.plan_referrals(self.read(TIN_B), tin=TIN_B)
        self.assertEqual([r.audit_case_ref for r in referrals],
                         ["SYN-AUD-2026-0002", "SYN-AUD-2026-0003"])  # fmt: skip
        self.assertEqual(referrals[0].rsl_inputs["risk_sets"], [])
        self.assertIsNone(referrals[0].period)

    def test_unavailable_rsl_is_reported_not_empty(self):
        self.sources.unavailable = frozenset({rules.SYSTEM})
        self.assertEqual(rules.plan_referrals(self.read(TIN_A), tin=TIN_A), ("unavailable", []))

    def test_a_score_not_owned_by_rsl_is_refused_by_the_contract(self):
        record = copy.deepcopy(self.doc["records"]["audit_system.referral"][0])
        record["risk_sets"][0]["owner"] = "Vati"
        contract = contracts.contract_for(rules.SYSTEM, rules.KIND)
        with self.assertRaises(contracts.SourceContractError):
            contracts.validate_record(contract, record)


ENVELOPE = {
    "fixture": True,
    "source_system": "audit_system",
    "kind": "referral",
    "contract_id": "rsl.audit_system.referral.v0-assumed",
    "taxpayer": TIN_A,
    "retrieved_at": "2026-11-11T08:00:00Z",
    "status": "ok",
    "records": [
        {
            "audit_case_ref": "R-1",
            "tin": TIN_A,
            "referred_on": "2026-11-01",
            "selection_reason": "risk_set_threshold",
            "risk_sets": [
                {"risk_set_id": "S", "risk_set_version": "1", "score": "0.5", "owner": "RSL"}
            ],
        },
    ],  # fmt: skip
    "references": [{"record_id": "R-1", "record_sha256": "a" * 64}],
}


class PlanTests(unittest.TestCase):
    def env(self, **changes):
        out = copy.deepcopy(ENVELOPE)
        out.update(changes)
        return out

    def refused(self, envelope, reason):
        with self.assertRaises(rules.InvalidReferral) as caught:
            rules.plan_referrals(envelope, tin=TIN_A)
        self.assertEqual(caught.exception.reason, reason)

    def test_wrong_reads_are_refused_with_a_reason(self):
        self.refused(self.env(kind="claim"), "wrong_source")
        self.refused(self.env(contract_id="rsl.online_refunds.claim.v0-assumed"), "wrong_contract")
        self.refused(self.env(taxpayer=TIN_B), "wrong_taxpayer")
        self.refused(self.env(status="maybe"), "unknown_status")
        self.refused(self.env(references=[]), "missing_reference")
        other = self.env()
        other["records"][0]["tin"] = TIN_B
        self.refused(other, "wrong_taxpayer")
        foreign = self.env()
        foreign["records"][0]["risk_sets"][0]["owner"] = "Vati"
        self.refused(foreign, "score_not_rsl_owned")

    def test_replay_and_conflict(self):
        _, (referral,) = rules.plan_referrals(self.env(), tin=TIN_A)
        self.assertEqual(rules.replay_outcome("a" * 64, referral), rules.REPLAYED)
        self.assertEqual(rules.replay_outcome("b" * 64, referral), rules.CONFLICT)

    def test_run_status(self):
        self.assertEqual(rules.run_status(["ok", "ok"]), "completed")
        self.assertEqual(rules.run_status(["ok", "unavailable"]), "partial")
        self.assertEqual(rules.run_status(["unavailable"]), "unavailable")
        self.assertEqual(rules.run_status([]), "completed")

    def test_view_labels_the_source_and_owner(self):
        _, (referral,) = rules.plan_referrals(self.env(), tin=TIN_A)
        view = rules.referral_view(
            {"referral_id": "r", "tenant_id": "t", "case_id": "c", "taxpayer_id": "p",
             "audit_case_ref": "R-1", "referred_on": "2026-11-01",
             "selection_reason": "risk_set_threshold", "period": "",
             "rsl_inputs": referral.rsl_inputs, "contract_id": referral.contract_id,
             "record_sha256": referral.record_sha256, "retrieved_at": referral.retrieved_at,
             "fixture": 1, "adapter_id": None}
        )  # fmt: skip
        self.assertEqual(view["rsl_inputs"]["owner"], "RSL")
        self.assertTrue(view["source"]["fixture"])
        self.assertIsNone(view["period"])


if __name__ == "__main__":
    unittest.main()
