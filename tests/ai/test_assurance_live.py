"""TAX-121 live benchmark mode, end to end without a bench (small synthetic population).

Runs what the ``assurance.requested`` handler runs for every claim (the site's recorded RSL
fixture file loaded by the real factory, the taxpayer's records round-tripped through the
evidence CSV, ``rules.assess_case``, the findings engine and the lane engine over memory
repositories), reads the records back the way ``live_bench`` exports them, converts them
with ``assurance_live.platform_results`` and checks the TAX-118 scorer sees exactly what the
standalone run produces. The site-backed version is
backend/vati_tax/vati_tax/assurance/test_assurance_bench.py.
"""

from __future__ import annotations

import datetime
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "vati_tax"))

from vati_tax.assurance import engine, rules  # noqa: E402
from vati_tax.findings import engine as findings_engine  # noqa: E402
from vati_tax.identity import policy  # noqa: E402
from vati_tax.integrations import config  # noqa: E402

from ai.evaluation import assurance_live  # noqa: E402
from ai.evaluation.assurance_benchmark import run_system, score  # noqa: E402
from ai.evaluation.assurance_pipeline import assess_taxpayer  # noqa: E402
from ai.evaluation.assurance_population import generate  # noqa: E402
from ai.workflows.rule_register import load_rule_register  # noqa: E402

PARAMS = {
    "n_taxpayers": 16,
    "n_dev": 4,
    "issue_counts": {
        "fake_invoices": 2,
        "untaxed_imported_services": 2,
        "disguised_employment": 2,
        "related_party_loss": 1,
        "policy_gap": 1,
        "mixed": 1,
    },
}
TENANT = "10000000-0000-4000-8000-000000000121"
NOW = datetime.datetime(2026, 11, 6, 12, 0, tzinfo=datetime.UTC)
SERVICE = policy.TenantScope(user="svc@example.test", tenant_id=TENANT,
                             roles=frozenset({policy.SERVICE}), taxpayer_ids=frozenset())  # fmt: skip
AUDITOR = policy.TenantScope(user="aud@example.test", tenant_id=TENANT,
                             roles=frozenset({policy.AUDITOR}), taxpayer_ids=frozenset())  # fmt: skip


class Platform:
    """The handler's steps over memory repositories, one case per sent claim."""

    def __init__(self, fixture_path: str, unavailable: list[str] | None = None) -> None:
        self.conf = {config.RSL_SOURCES_KEY: {"fixture_path": fixture_path,
                                              "vati_tenant_id": TENANT,
                                              "unavailable": unavailable or []}}  # fmt: skip
        self.findings = findings_engine.MemoryRepository()
        self.lanes = engine.MemoryRepository()
        self.register = load_rule_register().version

    def send(self, tin: str, records: dict[str, Any], claim: dict[str, str]) -> dict[str, Any]:
        case_id, taxpayer, bundle = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        content = rules.bundle_csv(records)
        sha = rules.sha256_text(content.decode())  # any stable digest stands in for the file hash
        self.findings.cases[case_id] = findings_engine.CaseRef(TENANT, case_id, taxpayer)
        self.findings.evidence[bundle] = (TENANT, taxpayer, sha)
        return {"tin": tin, "claim_ref": claim["claim_id"], "case_id": case_id,
                "bundle": bundle, "sha": sha, "content": content, "claim": claim}  # fmt: skip

    def handle(self, sent: dict[str, Any], event_id: str) -> dict[str, Any]:
        fixture = config.rsl_sources(self.conf, vati_tenant_id=TENANT)
        plan = rules.assess_case(
            fixture.sources, fixture.params, rules.parse_bundle(sent["content"]),
            tin=sent["tin"], period=sent["claim"]["period"],
            claimed_amount=sent["claim"]["claimed_amount"], bundle_evidence_id=sent["bundle"],
            bundle_sha256=sent["sha"], assess=assess_taxpayer, register_version=self.register,
        )  # fmt: skip
        ids: tuple[str, ...] = ()
        lane_id = None
        if plan["status"] != "not_run":
            ctx = findings_engine.Context.build(SERVICE, None, NOW)
            ids = tuple(
                findings_engine.record_finding(ctx, self.findings, case_id=sent["case_id"],
                                               body=b, idempotency_key=k)["finding_id"]
                for b, k in plan["bodies"]
            )  # fmt: skip
            lane_id = engine.record_lane_proposal(
                SERVICE, self.lanes, case_id=sent["case_id"], body=plan["lane"],
                producer_version=plan["version"], input_digest=plan["input_digest"],
                run_key="k", now=NOW, correlation_id=TENANT,
            )["proposal_id"]  # fmt: skip
        engine.record_run(self.lanes, engine.RunRecord(
            run_id=str(uuid.uuid4()), tenant_id=TENANT, case_id=sent["case_id"],
            submission_id=str(uuid.uuid4()), claim_ref=plan["claim_ref"], status=plan["status"],
            reason=plan["reason"], services={"rsl_sources": {"mode": fixture.mode,
                                                             "systems": plan["systems"]}},
            checks=plan["checks"], summary=plan.get("summary", {}), finding_ids=ids,
            lane_proposal_id=lane_id, producer_version=plan.get("version"),
            input_digest=plan.get("input_digest"), event_id=event_id, correlation_id=TENANT,
            started_at=NOW, seconds=0.01, run_key=engine.run_key(TENANT, event_id),
        ))  # fmt: skip
        return plan

    def read(self, case_id: str) -> dict[str, Any]:
        view = engine.case_assurance(AUDITOR, self.lanes,
                                     case={"case_id": case_id, "tenant_id": TENANT})  # fmt: skip
        view["findings"] = findings_engine.list_findings(
            findings_engine.Context.build(AUDITOR, None, NOW), self.findings, case_id=case_id
        )
        return view


class LiveModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.world, cls.key = generate(7, PARAMS)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.fixture = str(Path(cls.tmp.name) / "rsl_sources.json")
        Path(cls.fixture).write_text(json.dumps(assurance_live.sources_fixture(cls.world)))
        cls.tins = sorted(cls.key["taxpayers"])
        cls.seed = assurance_live.seed(cls.world, cls.tins)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def run_platform(self, platform: Platform, replay: bool = False) -> dict[str, Any]:
        results = []
        for item in self.seed["taxpayers"]:
            cases = []
            for claim in item["claims"]:
                sent = platform.send(item["tin"], item["records"], claim)
                platform.handle(sent, event_id=sent["case_id"])
                if replay:
                    platform.handle(sent, event_id=sent["case_id"])
                cases.append({"claim_ref": sent["claim_ref"], "case_id": sent["case_id"],
                              "assurance": platform.read(sent["case_id"]), "events": []})  # fmt: skip
            results.append({"tin": item["tin"], "cases": cases})
        return {"results": results}

    def test_seed_and_fixture_hold_no_answer_key_and_no_submissions(self) -> None:
        text = json.dumps(self.seed) + Path(self.fixture).read_text()
        for secret in (
            "policy_lane",
            "fake_vat",
            "fake_strategy",
            "amount_at_risk",
            "expected_routes",
        ):
            self.assertNotIn(secret, text)
        self.assertNotIn("submissions", json.loads(Path(self.fixture).read_text()))

    def test_platform_readback_scores_exactly_like_the_standalone_run(self) -> None:
        export = self.run_platform(Platform(self.fixture), replay=True)
        live, gaps = assurance_live.platform_results(export)
        standalone = run_system(self.world, self.tins)
        self.assertEqual(gaps, {"cases_without_completed_run": [], "summary_mismatch": []})
        self.assertEqual(assurance_live.differences(live, standalone), [])
        a, b = score(live, self.key), score(standalone, self.key)
        for section in (
            "detection_by_issue",
            "detection_overall",
            "false_positive_overall",
            "lanes",
            "refund_amounts",
            "amount_at_risk",
            "recompute",
            "legal_routes",
        ):
            self.assertEqual(a[section], b[section], section)
        self.assertTrue(any(r["flags"] for r in live.values()))

    def test_a_replay_records_nothing_twice(self) -> None:
        platform = Platform(self.fixture)
        self.run_platform(platform, replay=True)
        findings, lanes = len(platform.findings.findings), len(platform.lanes.lanes)
        claims = sum(len(i["claims"]) for i in self.seed["taxpayers"])
        self.assertEqual(lanes, claims)
        self.assertEqual(len(platform.lanes.runs), claims)
        self.assertGreater(findings, 0)

    def test_an_unavailable_required_source_is_not_run_never_clean(self) -> None:
        platform = Platform(self.fixture, unavailable=["etax"])
        export = self.run_platform(platform)
        live, gaps = assurance_live.platform_results(export)
        self.assertEqual(live, {})
        self.assertEqual(len(gaps["cases_without_completed_run"]),
                         sum(len(i["claims"]) for i in self.seed["taxpayers"]))  # fmt: skip
        self.assertEqual({g["reason"] for g in gaps["cases_without_completed_run"]},
                         {"etax_unavailable"})  # fmt: skip
        self.assertEqual(platform.findings.findings, {})
        self.assertEqual(platform.lanes.lanes, [])

    def test_an_unavailable_optional_source_degrades_and_says_so(self) -> None:
        platform = Platform(self.fixture, unavailable=["rsl_fraud_model"])
        item = self.seed["taxpayers"][0]
        plan = platform.handle(platform.send(item["tin"], item["records"], item["claims"][0]), "e")
        self.assertEqual(plan["status"], "degraded")
        self.assertEqual(plan["checks"]["refund_lane"], "degraded_rsl_fraud_model_unavailable")
        self.assertEqual(plan["systems"]["rsl_fraud_model"], "unavailable")

    def test_a_claim_that_differs_from_the_rsl_record_is_not_assessed(self) -> None:
        platform = Platform(self.fixture)
        item = self.seed["taxpayers"][0]
        claim = {**item["claims"][0], "claimed_amount": "1.00"}
        plan = platform.handle(platform.send(item["tin"], item["records"], claim), "e")
        self.assertEqual((plan["status"], plan["reason"]),
                         ("not_run", "claim_differs_from_rsl_record"))  # fmt: skip

    def test_the_factory_refuses_a_missing_wrong_or_foreign_fixture(self) -> None:
        cases = [
            ({}, "rsl_sources_not_configured"),
            ({config.RSL_SOURCES_KEY: {"fixture_path": self.fixture, "vati_tenant_id": "other"}},
             "rsl_sources_tenant_mismatch"),
            ({config.RSL_SOURCES_KEY: {"fixture_path": "/nonexistent.json",
                                       "vati_tenant_id": TENANT}}, "rsl_sources_file_missing"),
        ]  # fmt: skip
        not_fixture = Path(self.tmp.name) / "real.json"
        not_fixture.write_text(json.dumps({"format": config.RSL_FIXTURE_FORMAT, "fixture": False}))
        cases.append(({config.RSL_SOURCES_KEY: {"fixture_path": str(not_fixture),
                                                "vati_tenant_id": TENANT}},
                      "rsl_sources_not_a_fixture"))  # fmt: skip
        for conf, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(config.ServiceNotConfigured) as ctx:
                config.rsl_sources(conf, vati_tenant_id=TENANT)
            self.assertEqual(ctx.exception.reason, reason)
            self.assertEqual(ctx.exception.code, "upstream_unavailable")


if __name__ == "__main__":
    unittest.main()
