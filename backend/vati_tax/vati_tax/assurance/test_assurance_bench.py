"""Bench tests for the assurance pipeline step (TAX-121, ADR-042).

Run on a disposable site: ``python3 scripts/frappe_dev.py verify``. A small synthetic
TAX-118 population (generated here from the read-only ``ai/`` mount) is written to a
temporary RSL source fixture file and loaded by the real ``config.rsl_sources`` factory.
Jube stays unconfigured, as in the live benchmark, so its ``case.registered`` step retries
behind the assurance step and never blocks it.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import frappe
    from frappe.tests import IntegrationTestCase

    from vati_tax.assurance import live_bench, store
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.integrations import config
    from vati_tax.outbox import store as outbox_store
    from vati_tax.outbox import worker
    from vati_tax.pipeline import demo
    from vati_tax.pipeline import store as pipeline_store
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

try:
    from ai.evaluation import assurance_live
    from ai.evaluation.assurance_population import generate
except ImportError as exc:  # pragma: no cover - bench without the ai/ mount
    raise unittest.SkipTest(f"ai/ is not on the bench path ({exc})")

PARAMS = {
    "n_taxpayers": 12,
    "n_dev": 4,
    "issue_counts": {"fake_invoices": 2, "untaxed_imported_services": 1,
                     "disguised_employment": 1, "related_party_loss": 1, "policy_gap": 1,
                     "mixed": 0},
}  # fmt: skip


class AssuranceBenchTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.world, cls.key = generate(13, PARAMS)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.fixture = str(Path(cls.tmp.name) / "rsl_sources.json")
        Path(cls.fixture).write_text(json.dumps(assurance_live.sources_fixture(cls.world)))
        tins = sorted(cls.key["taxpayers"])
        cls.seed = {i["tin"]: i for i in assurance_live.seed(cls.world, tins)["taxpayers"]}
        cls.fake = next(t for t in tins if "fake_invoices" in cls.key["taxpayers"][t]["issues"])
        cls.clean = next(t for t in tins if not cls.key["taxpayers"][t]["issues"])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()
        super().tearDownClass()

    def setUp(self) -> None:
        frappe.set_user("Administrator")
        clear_principal_cache()
        self.run_id = uuid.uuid4().hex[:8]
        self.tenant = ensure_tenant(str(uuid.uuid4()), "Synthetic TAX-121 tenant")
        self.service = ensure_service_identity(f"tax121-svc-{self.run_id}@example.test",
                                               [self.tenant])  # fmt: skip
        self.auditor = f"tax121-aud-{self.run_id}@example.test"
        frappe.get_doc({"doctype": "User", "email": self.auditor, "first_name": "Auditor",
                        "send_welcome_email": 0}).insert(ignore_permissions=True)  # fmt: skip
        grant_membership(self.auditor, self.tenant, policy.AUDITOR)
        frappe.db.commit()
        pipeline_store.set_services(service_user=self.service)
        self.unavailable: list[str] = []
        self.configured = True

        def sources(tenant_id: str) -> Any:
            if not self.configured:
                return config.rsl_sources({}, vati_tenant_id=tenant_id)
            conf = {config.RSL_SOURCES_KEY: {"fixture_path": self.fixture,
                                             "vati_tenant_id": self.tenant,
                                             "unavailable": self.unavailable}}  # fmt: skip
            return config.rsl_sources(conf, vati_tenant_id=tenant_id)

        store.set_sources(sources)

    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        store.set_sources()
        pipeline_store.set_services()
        frappe.db.rollback()

    # --- helpers ---------------------------------------------------------------------

    def send(self, tin: str, claims: int = 1) -> dict[str, Any]:
        item = dict(self.seed[tin])
        item["claims"] = item["claims"][:claims]
        return live_bench.seed_taxpayer(self.tenant, self.run_id, item)

    def drain(self) -> None:
        for _ in range(20):
            if not worker.dispatch(limit=50, tenant_id=self.tenant, now=datetime.now(UTC)):
                break

    def events(self, case_id: str) -> list[Any]:
        return frappe.get_all(outbox_store.EVENT, filters={"case_id": case_id},
                              fields=["event_type", "status", "correlation_id", "sequence",
                                      "last_error_code"], order_by="sequence asc")  # fmt: skip

    def view(self, case_id: str) -> dict[str, Any]:
        frappe.set_user(self.auditor)
        clear_principal_cache()
        try:
            return store.case_assurance(tenant_id=self.tenant, case_id=case_id)
        finally:
            frappe.set_user("Administrator")
            clear_principal_cache()

    # --- tests -----------------------------------------------------------------------

    def test_the_send_enqueues_the_checks_first_with_one_correlation_id(self) -> None:
        case_id = self.send(self.clean)["cases"][0]["case_id"]
        events = self.events(case_id)
        self.assertEqual([e.event_type for e in events], ["assurance.requested", "case.registered"])
        self.assertEqual(len({e.correlation_id for e in events}), 1)
        self.drain()
        events = self.events(case_id)
        self.assertEqual(events[0].status, "delivered")
        # Jube is not configured here: its step retries, the checks did not wait for it.
        self.assertEqual((events[1].status, events[1].last_error_code),
                         ("retry", "jube_not_configured"))  # fmt: skip

    def test_findings_and_the_lane_are_recorded_and_readable_per_claim(self) -> None:
        sent = self.send(self.fake, claims=3)
        self.drain()
        views = [self.view(case["case_id"]) for case in sent["cases"]]
        for view, case in zip(views, sent["cases"], strict=True):
            run = view["runs"][0]
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["services"]["rsl_sources"]["mode"], "recorded")
            self.assertEqual(run["services"]["frappe"], "live")
            self.assertEqual(run["services"]["not_used_by_this_step"]["jube"], "not_configured")
            self.assertEqual([lane["claim_ref"] for lane in view["lane_proposals"]],
                             [case["claim_ref"]])  # fmt: skip
            lane = view["lane_proposals"][0]
            self.assertIn(lane["lane"], ("fast", "standard", "investigation"))
            self.assertIn("decision_due", lane["service_target"])
            self.assertIsNone(lane["decision"])
            self.assertTrue(view["findings"])
            for finding in view["findings"]:
                self.assertEqual(finding["producer"], "vati_check")
                self.assertEqual(finding["producer_version"], "tax139-pipeline-v2")
                self.assertIs(finding["candidate_route"]["eligible_for_assessment"], False)
                self.assertIn(finding["legal_route"]["status"], ("unverified", "no_verified_route"))
                self.assertTrue(finding["proposed_action"]["requires_human_approval"])
                self.assertTrue(finding["evidence_refs"] or finding["source_refs"])
            self.assertIn("refund_reconciliation", {f["category"] for f in view["findings"]})
        frappe.set_user(self.auditor)
        clear_principal_cache()
        lane = store.claim_lane(tenant_id=self.tenant, claim_ref=sent["cases"][0]["claim_ref"])
        frappe.set_user("Administrator")
        self.assertEqual(lane["undisputed_amount"]["currency"], "LSL")

    def test_a_resend_replays_and_records_nothing_twice(self) -> None:
        # A resend's checks queue behind the case's earlier steps (TAX-033 order), so the
        # first Jube step must finish: the TAX-087 port-level fake stands in for Jube here.
        services = demo.Services(live=False)
        services.install(self.service)
        self.addCleanup(demo.Services.uninstall)
        sent = self.send(self.fake)
        case = sent["cases"][0]
        self.drain()
        counts = {d: frappe.db.count(d, {"case_id": case["case_id"]})
                  for d in ("Vati Case Finding", "Vati Lane Proposal")}  # fmt: skip
        frappe.db.set_value("Vati Submission", case["submission_id"], "status",
                            "correction_needed")  # fmt: skip
        frappe.db.commit()
        frappe.set_user(f"tax121-{sent['tin']}-{self.run_id}@example.test")
        clear_principal_cache()
        doc = frappe.get_doc("Vati Submission", case["submission_id"])
        doc.status = "submitted"
        doc.save()
        frappe.set_user("Administrator")
        frappe.db.commit()
        self.drain()
        view = self.view(case["case_id"])
        self.assertEqual(len(view["runs"]), 2)
        self.assertEqual({d: frappe.db.count(d, {"case_id": case["case_id"]})
                          for d in ("Vati Case Finding", "Vati Lane Proposal")}, counts)  # fmt: skip
        self.assertEqual(view["runs"][0]["finding_ids"], view["runs"][1]["finding_ids"])

    def test_missing_configuration_is_not_run_never_clean(self) -> None:
        self.configured = False
        case_id = self.send(self.fake)["cases"][0]["case_id"]
        self.drain()
        run = self.view(case_id)["runs"][0]
        self.assertEqual((run["status"], run["reason"]), ("not_run", "upstream_unavailable"))
        self.assertEqual(run["services"]["rsl_sources"],
                         {"mode": "unavailable", "reason": "rsl_sources_not_configured"})  # fmt: skip
        self.assertEqual(set(run["checks"].values()), {"not_run"})
        self.assertEqual(frappe.db.count("Vati Case Finding", {"case_id": case_id}), 0)
        self.assertEqual(frappe.db.count("Vati Lane Proposal", {"case_id": case_id}), 0)

    def test_a_required_rsl_system_down_is_not_run_and_an_optional_one_degrades(self) -> None:
        self.unavailable = ["etax"]
        down = self.send(self.fake)["cases"][0]["case_id"]
        self.drain()
        run = self.view(down)["runs"][0]
        self.assertEqual((run["status"], run["reason"]), ("not_run", "etax_unavailable"))
        self.assertEqual(run["services"]["rsl_sources"]["systems"]["etax"], "unavailable")
        self.unavailable = ["rsl_fraud_model"]
        self.run_id += "b"
        degraded = self.send(self.clean)["cases"][0]["case_id"]
        self.drain()
        run = self.view(degraded)["runs"][0]
        self.assertEqual(run["status"], "degraded")
        self.assertEqual(run["checks"]["refund_lane"], "degraded_rsl_fraud_model_unavailable")

    def test_taxpayers_cannot_read_assurance_records(self) -> None:
        sent = self.send(self.clean)
        self.drain()
        frappe.set_user(f"tax121-{sent['tin']}-{self.run_id}@example.test")
        clear_principal_cache()
        try:
            with self.assertRaises((frappe.PermissionError, policy.ActionDenied)):
                store.case_assurance(tenant_id=self.tenant, case_id=sent["cases"][0]["case_id"])
        finally:
            frappe.set_user("Administrator")
