"""Bench tests for RSL audit referrals (TAX-123, ADR-047).

Run on a disposable site: ``python3 scripts/frappe_dev.py verify``. The synthetic referral
fixture (integration/rsl_sources/fixtures/audit_referrals.json, mounted read-only in the
bench) is copied to a temporary file and loaded by the real ``config.rsl_sources``
factory, exactly as a site would load it.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any

try:
    import frappe
    from frappe.tests import IntegrationTestCase

    from vati_tax.api_v1 import backend as api_backend
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.ids.canonical import CaseNotFound
    from vati_tax.integrations import config
    from vati_tax.patches.v0_4 import link_rsl_references
    from vati_tax.pipeline import store as pipeline_store
    from vati_tax.references import store as references_store
    from vati_tax.rsl_referrals import api, store
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

FIXTURE_NAME = Path("integration") / "rsl_sources" / "fixtures" / "audit_referrals.json"
CANDIDATES = [
    Path("/workspace/vati_shared") / FIXTURE_NAME,
    Path(__file__).resolve().parents[4] / FIXTURE_NAME,
]
_FOUND = [p for p in CANDIDATES if p.exists()]
if not _FOUND:  # pragma: no cover - bench without the integration/ mount
    raise unittest.SkipTest("integration/rsl_sources fixtures are not on the bench")
FIXTURE: Path = _FOUND[0]

TIN_A, TIN_B = "510000006", "510000014"
REFERRAL = "Vati RSL Referral"
CASE = "Vati Case Identity"
LINK = "Vati Service Link"


class RslReferralBenchTests(IntegrationTestCase):
    def setUp(self) -> None:
        frappe.set_user("Administrator")
        clear_principal_cache()
        self.run_id = uuid.uuid4().hex[:8]
        self.tenant = ensure_tenant(str(uuid.uuid4()), "Synthetic TAX-123 tenant")
        self.other = ensure_tenant(str(uuid.uuid4()), "Synthetic TAX-123 other tenant")
        self.service = ensure_service_identity(f"tax123-svc-{self.run_id}@example.test",
                                               [self.tenant])  # fmt: skip
        self.users: dict[str, str] = {}
        staff: list[tuple[str, str, policy.Role]] = [
            ("auditor", self.tenant, policy.AUDITOR),
            ("auditor-other", self.other, policy.AUDITOR),
        ]
        for name, tenant, role in staff:
            user = f"tax123-{name}-{self.run_id}@example.test"
            frappe.get_doc({"doctype": "User", "email": user, "first_name": name,
                            "send_welcome_email": 0}).insert(ignore_permissions=True)  # fmt: skip
            grant_membership(user, tenant, role)
            self.users[name] = user
        self.taxpayers = {
            tin: frappe.get_doc(
                {
                    "doctype": "Vati Taxpayer",
                    "tenant_id": self.tenant,
                    "tin": tin,
                    "display_name": f"Synthetic {tin}",
                    "taxpayer_type": "company",
                }
            )
            .insert(ignore_permissions=True)
            .name  # fmt: skip
            for tin in (TIN_A, TIN_B)
        }
        self.users["taxpayer"] = f"tax123-taxpayer-{self.run_id}@example.test"
        frappe.get_doc({"doctype": "User", "email": self.users["taxpayer"], "first_name": "tp",
                        "send_welcome_email": 0}).insert(ignore_permissions=True)  # fmt: skip
        grant_membership(self.users["taxpayer"], self.tenant, policy.TAXPAYER,
                         self.taxpayers[TIN_A])  # fmt: skip
        frappe.db.commit()
        pipeline_store.set_services(service_user=self.service)
        self.tmp = tempfile.TemporaryDirectory()
        self.doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.path = str(Path(self.tmp.name) / "rsl_sources.json")
        self.write(self.doc)
        self.unavailable: list[str] = []
        self.configured = True

        def sources(tenant_id: str) -> Any:
            if not self.configured:
                return config.rsl_sources({}, vati_tenant_id=tenant_id)
            conf = {config.RSL_SOURCES_KEY: {"fixture_path": self.path,
                                             "vati_tenant_id": self.tenant,
                                             "unavailable": self.unavailable}}  # fmt: skip
            return config.rsl_sources(conf, vati_tenant_id=tenant_id)

        store.set_sources(sources)

    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        store.set_sources(None)
        pipeline_store.set_services()
        self.tmp.cleanup()
        frappe.db.rollback()

    def write(self, doc: dict[str, Any]) -> None:
        Path(self.path).write_text(json.dumps(doc))
        config._RSL_CACHE.clear()

    def ingest(self) -> dict[str, Any]:
        frappe.set_user("Administrator")
        return store.ingest_audit_referrals(self.tenant)

    def referrals(self) -> list[Any]:
        return frappe.get_all(REFERRAL, filters={"tenant_id": self.tenant},
                              fields=["*"], order_by="audit_case_ref asc")  # fmt: skip

    def test_each_referral_opens_one_case_linked_to_the_rsl_reference(self):
        summary = self.ingest()
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["counts"], {"created": 3})
        self.assertTrue(summary["source"]["fixture"])
        rows = self.referrals()
        self.assertEqual([r.audit_case_ref for r in rows],
                         ["SYN-AUD-2026-0001", "SYN-AUD-2026-0002", "SYN-AUD-2026-0003"])  # fmt: skip
        self.assertEqual(len({r.case_id for r in rows}), 3)
        for row in rows:
            case = frappe.db.get_value(
                CASE, row.case_id, ["tenant_id", "taxpayer_id"], as_dict=True
            )
            self.assertEqual(case.tenant_id, self.tenant)
            self.assertEqual(case.taxpayer_id, row.taxpayer_id)
            self.assertTrue(row.fixture)
            self.assertEqual(row.contract_id, "rsl.audit_system.referral.v0-assumed")
            inputs = json.loads(row.rsl_inputs)
            self.assertEqual(inputs["owner"], "RSL")
            self.assertTrue(all(r["owner"] == "RSL" for r in inputs["risk_sets"]))
        first = rows[0]
        self.assertEqual(first.taxpayer_id, self.taxpayers[TIN_A])
        self.assertEqual(json.loads(first.rsl_inputs)["risk_sets"][1]["score"], "0.8100")
        # No Vati score, finding or decision is written for a referral.
        for doctype in ("Vati Case Finding", "Vati Decision", "Vati Lane Proposal"):
            self.assertEqual(frappe.db.count(doctype, {"case_id": first.case_id}), 0)

    def test_each_rsl_audit_case_joins_the_reference_register(self):
        # TAX-133: one audit_system link per referral; a replay records nothing new.
        self.ingest()
        rsl = {"tenant_id": self.tenant, "service": "audit_system"}
        for row in self.referrals():
            link = frappe.db.get_value(LINK, {**rsl, "case_id": row.case_id},
                                       ["external_type", "external_id"], as_dict=True)  # fmt: skip
            self.assertEqual((link.external_type, link.external_id),
                             ("audit_case", row.audit_case_ref))  # fmt: skip
        self.assertEqual(frappe.db.count(LINK, rsl), 3)
        self.ingest()
        self.assertEqual(frappe.db.count(LINK, rsl), 3)
        first = self.referrals()[0]
        frappe.set_user(self.users["auditor"])
        clear_principal_cache()
        card = references_store.officer_card(tenant_id=self.tenant, case_id=first.case_id)
        self.assertEqual(card["rsl_audit_case_ref"], "SYN-AUD-2026-0001")
        self.assertIsNone(card["rsl_claim_id"])
        with self.assertRaises(CaseNotFound):
            references_store.officer_card(tenant_id=self.other, case_id=first.case_id)

    def test_backfill_links_references_stored_before_the_register_had_them(self):
        # TAX-133 patch: links missing from earlier runs are recorded; a rerun is a no-op.
        self.ingest()
        frappe.db.delete(LINK, {"tenant_id": self.tenant, "service": "audit_system"})
        counts = link_rsl_references.link_all()
        self.assertGreaterEqual(counts.get("audit_system.linked", 0), 3)
        self.assertEqual(frappe.db.count(LINK, {"tenant_id": self.tenant,
                                                "service": "audit_system"}), 3)  # fmt: skip
        again = link_rsl_references.link_all()
        self.assertEqual(again.get("audit_system.linked", 0), 0)

    def test_the_case_shows_rsl_selection_as_rsls_own(self):
        # TAX-131 (ADR-043 §6): RSL's reason and risk-set scores, labelled as RSL's.
        self.ingest()
        first = self.referrals()[0]
        frappe.set_user(self.users["auditor"])
        clear_principal_cache()
        (case,) = api_backend.project_cases(
            self.tenant,
            frappe.get_all(
                CASE, filters={"name": first.case_id}, fields=api_backend._CASE_FIELDS
            ),  # fmt: skip
        )
        reasons = case["selection_reasons"]
        self.assertEqual([r["code"] for r in reasons],
                         ["rsl_audit_referral", "rsl_risk_set", "rsl_risk_set"])  # fmt: skip
        self.assertTrue(all(r["source"] == "audit_system" for r in reasons))
        self.assertIn("risk_set_threshold", reasons[0]["summary"])
        self.assertTrue(
            any("scored 0.8100. RSL's score, not a Vati score." in r["summary"] for r in reasons)
        )
        # No Jube rule ran on a referral: nothing claims to be a Vati signal.
        self.assertFalse([r for r in reasons if r["source"] == "jube"])

    def test_a_second_read_replays_and_reuses_the_case(self):
        self.ingest()
        before = {r.audit_case_ref: r.case_id for r in self.referrals()}
        cases = frappe.db.count(CASE, {"tenant_id": self.tenant})
        summary = self.ingest()
        self.assertEqual(summary["counts"], {"replayed": 3})
        self.assertEqual({i["audit_case_ref"]: i["case_id"] for i in summary["referrals"]}, before)
        self.assertEqual(frappe.db.count(CASE, {"tenant_id": self.tenant}), cases)
        self.assertEqual(len(self.referrals()), 3)

    def test_a_changed_rsl_record_is_a_conflict_and_changes_nothing(self):
        self.ingest()
        changed = copy.deepcopy(self.doc)
        changed["records"]["audit_system.referral"][0]["risk_sets"][0]["score"] = "0.9900"
        self.write(changed)
        summary = self.ingest()
        item = next(i for i in summary["referrals"] if i["audit_case_ref"] == "SYN-AUD-2026-0001")
        self.assertEqual((item["outcome"], item["reason"]), ("conflict", "rsl_record_changed"))
        stored = frappe.db.get_value(REFERRAL, {"tenant_id": self.tenant,
                                                "audit_case_ref": "SYN-AUD-2026-0001"},
                                     "rsl_inputs")  # fmt: skip
        self.assertNotIn("0.9900", stored)

    def test_rsl_unavailable_or_not_configured_writes_nothing(self):
        self.unavailable = ["audit_system"]
        summary = self.ingest()
        self.assertEqual(summary["status"], "unavailable")
        self.assertEqual(summary["referrals"], [])
        self.configured = False
        summary = self.ingest()
        self.assertEqual((summary["status"], summary["reason"]),
                         ("unavailable", "rsl_sources_not_configured"))  # fmt: skip
        self.assertEqual(self.referrals(), [])

    def test_officers_read_the_referral_behind_a_case(self):
        self.ingest()
        case_id = self.referrals()[0].case_id
        frappe.set_user(self.users["auditor"])
        clear_principal_cache()
        (view,) = store.case_referrals(tenant_id=self.tenant, case_id=case_id)
        self.assertEqual(view["audit_case_ref"], "SYN-AUD-2026-0001")
        self.assertEqual(view["rsl_inputs"]["owner"], "RSL")
        self.assertTrue(view["source"]["fixture"])
        frappe.set_user(self.users["taxpayer"])
        clear_principal_cache()
        with self.assertRaises(frappe.PermissionError):
            store.case_referrals(tenant_id=self.tenant, case_id=case_id)
        frappe.set_user(self.users["auditor-other"])
        clear_principal_cache()
        with self.assertRaises(CaseNotFound):
            store.case_referrals(tenant_id=self.other, case_id=case_id)

    def test_referrals_are_append_only_and_the_intake_is_operator_only(self):
        self.ingest()
        doc = frappe.get_doc(REFERRAL, self.referrals()[0].name)
        doc.selection_reason = "something_else"
        with self.assertRaises(frappe.PermissionError):
            doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(REFERRAL, doc.name, ignore_permissions=True)
        frappe.set_user(self.users["auditor"])
        with self.assertRaises(frappe.PermissionError):
            api.ingest_audit_referrals(self.tenant)
