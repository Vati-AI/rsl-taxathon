"""Bench tests for the RSL claim intake (TAX-122, ADR-043).

Run on a disposable site: ``python3 scripts/frappe_dev.py verify``. A small synthetic
TAX-118 population is written to a temporary RSL source fixture (two Online Refunds
claims per chosen taxpayer) and loaded by the real ``config.rsl_sources`` factory. The
intake reads it, records the claims and sends them, and the TAX-121 assurance step runs
exactly as for a portal send. Jube stays unconfigured, as in the TAX-121 bench tests.
"""

from __future__ import annotations

import copy
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

    from vati_tax.assurance import live_bench
    from vati_tax.assurance import store as assurance_store
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.ids.canonical import CaseNotFound
    from vati_tax.integrations import config
    from vati_tax.outbox import store as outbox_store
    from vati_tax.outbox import worker
    from vati_tax.pipeline import store as pipeline_store
    from vati_tax.portal import api as portal_api
    from vati_tax.references import store as references_store
    from vati_tax.rsl_intake import api, store
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
CLAIMS_PER_TAXPAYER = 2
SUBMISSION = "Vati Submission"
LINK = "Vati Service Link"


def _keep_claims(doc: dict[str, Any], tins: list[str]) -> dict[str, Any]:
    """The fixture with only the first claims of ``tins`` in the Online Refunds records."""
    doc = copy.deepcopy(doc)
    kept: dict[str, int] = {}
    rows = []
    for claim in sorted(doc["records"]["online_refunds.claim"], key=lambda c: c["period"]):
        tin = str(claim["tin"])
        if tin in tins and kept.get(tin, 0) < CLAIMS_PER_TAXPAYER:
            kept[tin] = kept.get(tin, 0) + 1
            rows.append(claim)
    # TAX-125: RSL's own status for the first claim; the second has none.
    if rows:
        rows[0]["status"] = "audit_in_progress"
    doc["records"]["online_refunds.claim"] = rows
    return doc


class RslIntakeBenchTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        world, key = generate(13, PARAMS)
        tins = sorted(key["taxpayers"])
        cls.fake = next(t for t in tins if "fake_invoices" in key["taxpayers"][t]["issues"])
        cls.clean = next(t for t in tins if not key["taxpayers"][t]["issues"])
        cls.seed = {i["tin"]: i for i in assurance_live.seed(world, tins)["taxpayers"]}
        cls.tmp = tempfile.TemporaryDirectory()
        cls.doc = _keep_claims(assurance_live.sources_fixture(world), [cls.fake, cls.clean])
        cls.fixture = str(Path(cls.tmp.name) / "rsl_sources.json")
        Path(cls.fixture).write_text(json.dumps(cls.doc))
        cls.claims = {
            tin: [c for c in cls.doc["records"]["online_refunds.claim"] if str(c["tin"]) == tin]
            for tin in (cls.fake, cls.clean)
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()
        super().tearDownClass()

    def setUp(self) -> None:
        frappe.set_user("Administrator")
        clear_principal_cache()
        self.run_id = uuid.uuid4().hex[:8]
        self.tenant = ensure_tenant(str(uuid.uuid4()), "Synthetic TAX-122 tenant")
        self.service = ensure_service_identity(f"tax122-svc-{self.run_id}@example.test",
                                               [self.tenant])  # fmt: skip
        self.auditor = f"tax122-aud-{self.run_id}@example.test"
        frappe.get_doc({"doctype": "User", "email": self.auditor, "first_name": "Auditor",
                        "send_welcome_email": 0}).insert(ignore_permissions=True)  # fmt: skip
        grant_membership(self.auditor, self.tenant, policy.AUDITOR)
        frappe.db.commit()
        pipeline_store.set_services(service_user=self.service)
        self.fixture_path = self.fixture
        self.unavailable: list[str] = []
        self.configured = True

        def sources(tenant_id: str) -> Any:
            if not self.configured:
                return config.rsl_sources({}, vati_tenant_id=tenant_id)
            conf = {config.RSL_SOURCES_KEY: {"fixture_path": self.fixture_path,
                                             "vati_tenant_id": self.tenant,
                                             "unavailable": self.unavailable}}  # fmt: skip
            return config.rsl_sources(conf, vati_tenant_id=tenant_id)

        store.set_sources(sources)
        assurance_store.set_sources(sources)
        # The taxpayers exist in Vati with their own records, but have filed nothing here.
        self.taxpayers = {}
        for tin in (self.fake, self.clean):
            item = {**self.seed[tin], "claims": []}
            self.taxpayers[tin] = live_bench.seed_taxpayer(self.tenant, self.run_id, item)
        frappe.set_user("Administrator")
        clear_principal_cache()

    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        store.set_sources()
        assurance_store.set_sources()
        pipeline_store.set_services()
        frappe.db.rollback()

    # --- helpers ---------------------------------------------------------------------

    def ingest(self) -> dict[str, Any]:
        frappe.set_user("Administrator")
        clear_principal_cache()
        summary = api.ingest_online_refunds(self.tenant)
        frappe.db.commit()
        return summary

    def drain(self) -> None:
        for _ in range(20):
            if not worker.dispatch(limit=50, tenant_id=self.tenant, now=datetime.now(UTC)):
                break

    def submissions(self) -> list[Any]:
        return frappe.get_all(
            SUBMISSION, filters={"tenant_id": self.tenant},
            fields=["name", "taxpayer_id", "status", "source", "source_ref", "source_contract",
                    "source_record_sha256", "source_refs", "case_id", "synthetic", "declaration"],
            order_by="source_ref asc",
        )  # fmt: skip

    def as_user(self, user: str) -> None:
        frappe.set_user(user)
        clear_principal_cache()

    # --- tests -----------------------------------------------------------------------

    def test_rsl_claims_become_sent_cases_and_the_checks_run_unchanged(self) -> None:
        summary = self.ingest()
        expected = sorted(c["claim_id"] for rows in self.claims.values() for c in rows)
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["counts"], {"created": len(expected)})
        self.assertEqual(summary["source"]["mode"], "recorded")
        subs = self.submissions()
        self.assertEqual([s.source_ref for s in subs], expected)
        for sub in subs:
            self.assertEqual(sub.source, "rsl_online_refunds")
            self.assertEqual(sub.status, "submitted")
            self.assertEqual(sub.source_contract, "rsl.online_refunds.claim.v0-assumed")
            self.assertEqual(len(sub.source_record_sha256), 64)
            self.assertTrue(sub.case_id)
            self.assertEqual(sub.synthetic, 1)
            events = frappe.get_all(outbox_store.EVENT, filters={"case_id": sub.case_id},
                                    pluck="event_type", order_by="sequence asc")  # fmt: skip
            self.assertEqual(events, ["assurance.requested", "case.registered"])
        self.drain()
        self.as_user(self.auditor)
        try:
            for sub in subs:
                view = assurance_store.case_assurance(tenant_id=self.tenant, case_id=sub.case_id)
                run = view["runs"][0]
                self.assertEqual(run["status"], "completed", run.get("reason"))
                self.assertEqual(run["claim_ref"], sub.source_ref)
                self.assertEqual([lane["claim_ref"] for lane in view["lane_proposals"]],
                                 [sub.source_ref])  # fmt: skip
                self.assertIsNone(view["lane_proposals"][0]["decision"])
        finally:
            self.as_user("Administrator")

    def test_a_second_read_replays_and_a_changed_record_conflicts(self) -> None:
        first = self.ingest()
        before = {s.name: (s.source_record_sha256, s.case_id) for s in self.submissions()}
        again = self.ingest()
        self.assertEqual(again["counts"], {"replayed": len(before)})
        self.assertEqual({s.name: (s.source_record_sha256, s.case_id)
                          for s in self.submissions()}, before)  # fmt: skip
        self.assertEqual({c["submission_id"] for c in again["claims"]},
                         {c["submission_id"] for c in first["claims"]})  # fmt: skip
        changed = copy.deepcopy(self.doc)
        target = changed["records"]["online_refunds.claim"][0]
        target["claimed_amount"] = "1.00" if target["claimed_amount"] != "1.00" else "2.00"
        path = Path(self.tmp.name) / f"changed-{self.run_id}.json"
        path.write_text(json.dumps(changed))
        self.fixture_path = str(path)
        third = self.ingest()
        outcome = {c["claim_ref"]: c["outcome"] for c in third["claims"]}
        self.assertEqual(outcome.pop(target["claim_id"]), "conflict")
        self.assertEqual(set(outcome.values()), {"replayed"})
        self.assertEqual({s.name: (s.source_record_sha256, s.case_id)
                          for s in self.submissions()}, before)  # fmt: skip

    def test_rsl_not_answering_is_reported_as_unavailable_never_as_no_claims(self) -> None:
        self.unavailable = ["online_refunds"]
        summary = self.ingest()
        self.assertEqual(summary["status"], "unavailable")
        self.assertEqual({t["status"] for t in summary["taxpayers"]}, {"unavailable"})
        self.assertEqual(self.submissions(), [])
        self.configured = False
        summary = self.ingest()
        self.assertEqual(summary["status"], "unavailable")
        self.assertTrue(summary["reason"])
        self.assertEqual(self.submissions(), [])

    def test_rsl_held_documents_are_references_and_nothing_is_copied(self) -> None:
        self.ingest()
        for sub in self.submissions():
            refs = json.loads(sub.source_refs)
            self.assertEqual(refs["record_id"], sub.source_ref)
            self.assertEqual(refs["record_sha256"], sub.source_record_sha256)
            self.assertTrue(all(d["held_by"] == "RSL" for d in refs["documents"]))
            claim = frappe.get_all("Vati Refund Claim", filters={"submission_id": sub.name},
                                   fields=["evidence_ids", "claim_reason"])[0]  # fmt: skip
            self.assertEqual(json.loads(claim.evidence_ids or "[]"), [])
            self.assertEqual(claim.claim_reason, "other")
            self.assertEqual(frappe.db.count("Vati Evidence", {"case_id": sub.case_id}), 0)

    def test_only_the_intake_records_and_sends_an_rsl_claim(self) -> None:
        self.ingest()
        sub = self.submissions()[0]
        tin = next(t for t, seeded in self.taxpayers.items()
                   if seeded["taxpayer_id"] == sub.taxpayer_id)  # fmt: skip
        # live_bench.seed_taxpayer names the taxpayer's user this way.
        self.as_user(f"tax121-{tin}-{self.run_id}@example.test")
        try:
            # The taxpayer cannot change what they filed with RSL.
            doc = frappe.get_doc(SUBMISSION, sub.name)
            doc.contact_email = "someone@example.test"
            with self.assertRaises(frappe.PermissionError):
                doc.save()
            # Nor record a claim as if it came from RSL.
            with self.assertRaises(frappe.PermissionError):
                frappe.get_doc(
                    {"doctype": SUBMISSION, "tenant_id": self.tenant,
                     "taxpayer_id": sub.taxpayer_id,
                     "submission_type": "refund_claim", "period": "2026-01",
                     "source": "rsl_online_refunds", "source_ref": "FAKE-1",
                     "source_record_sha256": "0" * 64,
                     "idempotency_key": f"tax122-forged-{self.run_id}"}
                ).insert()  # fmt: skip
        finally:
            self.as_user("Administrator")
        # Staff cannot run the intake.
        self.as_user(self.auditor)
        try:
            with self.assertRaises(frappe.PermissionError):
                api.ingest_online_refunds(self.tenant)
        finally:
            self.as_user("Administrator")

    def test_the_rsl_claim_joins_the_reference_register_and_the_card(self) -> None:
        # TAX-133: one online_refunds link per claim, on the case the claim opened.
        self.ingest()
        subs = [s for s in self.submissions() if s.case_id]
        self.assertEqual(len(subs), sum(len(rows) for rows in self.claims.values()))
        rsl = {"tenant_id": self.tenant, "service": "online_refunds"}
        for sub in subs:
            link = frappe.db.get_value(LINK, {**rsl, "case_id": sub.case_id},
                                       ["external_type", "external_id"], as_dict=True)  # fmt: skip
            self.assertEqual((link.external_type, link.external_id), ("claim", sub.source_ref))
        linked = frappe.db.count(LINK, rsl)
        self.assertEqual(self.ingest()["counts"], {"replayed": len(subs)})
        self.assertEqual(frappe.db.count(LINK, rsl), linked)

        sub = subs[0]
        tin = next(t for t, rows in self.claims.items()
                   if sub.source_ref in {c["claim_id"] for c in rows})  # fmt: skip
        self.as_user(self.auditor)
        card = references_store.officer_card(tenant_id=self.tenant, case_id=sub.case_id)
        self.assertEqual((card["audience"], card["rsl_claim_id"]), ("officer", sub.source_ref))
        self.assertEqual(card["tenant_id"], self.tenant)
        self.assertRegex(card["tin_masked"] or "", r"^\*{4,}[0-9]{2,4}$")
        self.assertNotIn(tin, repr(card))
        with self.assertRaises(CaseNotFound):
            references_store.officer_card(tenant_id=str(uuid.uuid4()), case_id=sub.case_id)

        self.as_user(f"tax121-{tin}-{self.run_id}@example.test")
        mine = portal_api.get_case_references(submission_id=sub.name)["data"]
        self.assertEqual((mine["audience"], mine["rsl_claim_id"]), ("taxpayer", sub.source_ref))
        self.assertIsNone(mine["jube_case_id"])
        self.assertIsNone(mine["openaleph_collection_id"])
        self.assertEqual(mine["rsl_records"], [])
        theirs = next(s for s in subs if s.taxpayer_id != sub.taxpayer_id)
        for kwargs in ({"submission_id": theirs.name}, {"request_id": str(uuid.uuid4())}, {}):
            with self.assertRaises(frappe.DoesNotExistError):
                portal_api.get_case_references(**kwargs)
        self.as_user("Administrator")

    def test_the_taxpayer_sees_rsl_reference_and_status_labelled_with_its_source(self) -> None:
        # TAX-125: the portal reads RSL's status from the site's RSL source on each view.
        self.ingest()
        conf_key = config.RSL_SOURCES_KEY
        previous = frappe.local.conf.get(conf_key)
        frappe.local.conf[conf_key] = {"fixture_path": self.fixture_path,
                                       "vati_tenant_id": self.tenant,
                                       "unavailable": []}  # fmt: skip
        try:
            # The claim _keep_claims gave an RSL status, and its taxpayer.
            first = next(c for rows in self.claims.values() for c in rows if c.get("status"))
            tin = str(first["tin"])
            self.as_user(f"tax121-{tin}-{self.run_id}@example.test")
            items = portal_api.get_submissions()["data"]
            mine = {i["rsl"]["claim_ref"]: i for i in items if i["origin"] == "rsl_online_refunds"}
            self.assertEqual(set(mine), {c["claim_id"] for c in self.claims[tin]})
            block = mine[first["claim_id"]]["rsl"]
            self.assertEqual((block["status"], block["status_label"], block["status_state"]),
                             ("audit_in_progress", "Audit in progress", "reported"))  # fmt: skip
            self.assertTrue(block["recorded"])
            self.assertIsNone(block["dashboard_url"])
            for other in self.claims[tin]:
                if other is not first:
                    state = mine[other["claim_id"]]["rsl"]["status_state"]
                    self.assertEqual(state, "not_reported")
            for item in items:
                self.assertNotIn("case_id", item)
            frappe.local.conf[conf_key]["unavailable"] = ["online_refunds"]
            down = portal_api.get_submission(mine[first["claim_id"]]["id"])["data"]["rsl"]
            self.assertEqual((down["status_state"], down["status"]), ("unavailable", None))
        finally:
            self.as_user("Administrator")
            frappe.local.conf[conf_key] = previous
