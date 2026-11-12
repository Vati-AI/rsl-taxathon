"""Bench tests for check before you file (TAX-129, ADR-051).

Run on a disposable site: ``python3 scripts/frappe_dev.py verify``. A synthetic RSL source
fixture (e-Tax return, Lekuka invoices, ASYCUDA entry) is loaded by the real
``config.rsl_sources`` factory; the taxpayer portal and RSL's front door (a service
identity) call the check.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any

try:
    import frappe
    from frappe.tests import IntegrationTestCase

    from vati_tax.assurance import store as assurance_store
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.integrations import config
    from vati_tax.portal import api as portal_api
    from vati_tax.prefiling import api as prefiling_api
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

TIN_A, TIN_B = "510000006", "510000014"
RSL_TENANT = "SYN-TENANT-RSL"
TABLES = ("Vati Submission", "Vati Refund Claim", "Vati Evidence", "Vati Audit Event")


def vat_return(tin: str, input_vat: str, refund: str) -> dict[str, Any]:
    return {"return_id": f"VAT-{tin}-09", "tin": tin, "period": "2026-09",
            "standard_rated_sales": "8000.00", "zero_rated_sales": "0.00",
            "exempt_sales": "0.00", "output_vat": "1200.00", "input_vat_claimed": input_vat,
            "imported_services_vat_declared": "0.00", "refund_claim": refund,
            "filed_on": "2026-10-19"}  # fmt: skip


def invoice(n: int, buyer: str, vat: str, declared: bool = True) -> dict[str, Any]:
    return {"idms_id": f"IDMS-{n}", "invoice_number": f"INV-{n}", "supplier_tin": "510000022",
            "buyer_tin": buyer, "issue_date": "2026-09-12", "net_amount": "10000.00",
            "vat_amount": vat, "supplier_output_declared": declared}  # fmt: skip


class PrefilingBenchTests(IntegrationTestCase):
    def setUp(self) -> None:
        frappe.set_user("Administrator")
        clear_principal_cache()
        run = uuid.uuid4().hex[:8]
        self.tenant = ensure_tenant(str(uuid.uuid4()), "Synthetic TAX-129 tenant")
        self.taxpayers: dict[str, str] = {}
        self.users: dict[str, str] = {}
        for tin, name in ((TIN_A, "a"), (TIN_B, "b")):
            self.taxpayers[tin] = (
                frappe.get_doc(
                    {
                        "doctype": "Vati Taxpayer",
                        "tenant_id": self.tenant,
                        "tin": tin,
                        "display_name": f"Synthetic {tin}",
                        "taxpayer_type": "company",
                    }
                )  # fmt: skip
                .insert(ignore_permissions=True)
                .name
            )
            user = f"tax129-taxpayer-{name}-{run}@example.test"
            frappe.get_doc({"doctype": "User", "email": user, "first_name": name,
                            "send_welcome_email": 0}).insert(ignore_permissions=True)  # fmt: skip
            grant_membership(user, self.tenant, policy.TAXPAYER, self.taxpayers[tin])
            self.users[name] = user
        self.users["auditor"] = f"tax129-auditor-{run}@example.test"
        frappe.get_doc({"doctype": "User", "email": self.users["auditor"], "first_name": "aud",
                        "send_welcome_email": 0}).insert(ignore_permissions=True)  # fmt: skip
        grant_membership(self.users["auditor"], self.tenant, policy.AUDITOR)
        self.users["service"] = ensure_service_identity(f"tax129-svc-{run}@example.test",
                                                        [self.tenant])  # fmt: skip
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "rsl_sources.json"
        self.records: dict[str, list[dict[str, Any]]] = {
            # Taxpayer A: claims 3,000 more input VAT than Lekuka holds; one supplier undeclared.
            "etax.vat_return": [vat_return(TIN_A, "4500.00", "3300.00"),
                                vat_return(TIN_B, "1500.00", "300.00")],
            "idms.invoice": [invoice(1, TIN_A, "1500.00", declared=False),
                             invoice(2, TIN_B, "1500.00")],
            "asycuda.entry": [],
        }  # fmt: skip
        self.unavailable: list[str] = []
        self.write()

        def sources(tenant_id: str) -> Any:
            conf = {config.RSL_SOURCES_KEY: {"fixture_path": str(self.path),
                                             "vati_tenant_id": self.tenant,
                                             "unavailable": self.unavailable}}  # fmt: skip
            return config.rsl_sources(conf, vati_tenant_id=tenant_id)

        assurance_store.set_sources(sources)
        frappe.db.commit()

    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        frappe.local.request = None
        assurance_store.set_sources()
        self.tmp.cleanup()
        frappe.db.rollback()

    def write(self) -> None:
        self.path.write_text(json.dumps({
            "format": config.RSL_FIXTURE_FORMAT, "fixture": True, "synthetic": True,
            "description": "Synthetic TAX-129 records; assumed shapes.", "tenant_id": RSL_TENANT,
            "retrieved_at": "2026-11-12T08:00:00Z", "rates_assumed": {"vat": "0.15"},
            "materiality": {}, "records": self.records,
        }))  # fmt: skip
        config._RSL_CACHE.clear()

    def as_user(self, name: str) -> None:
        frappe.set_user(self.users[name])
        clear_principal_cache()

    def counts(self) -> dict[str, int]:
        return {t: frappe.db.count(t, {"tenant_id": self.tenant}) for t in TABLES}

    def portal(self, who: str = "a", period: str = "2026-09") -> dict[str, Any]:
        self.as_user(who)
        try:
            result: dict[str, Any] = portal_api.check_before_filing(period=period)
            return result
        finally:
            frappe.set_user("Administrator")

    def test_taxpayer_sees_guidance_from_rsl_data_and_nothing_is_stored(self) -> None:
        before = self.counts()
        data = self.portal()["data"]
        self.assertEqual(data["status"], "check_these")
        self.assertEqual(data["figures"]["input_vat_claimed"], "4500.00")
        self.assertEqual(data["figures"]["lekuka_input_vat"], "1500.00")
        codes = [p["code"] for p in data["prompts"]]
        self.assertEqual(sorted(codes), ["input_vat_above_rsl_records", "supplier_not_declared"])
        self.assertTrue(data["guidance_only"])
        self.assertEqual(data["file_with"]["name"], "Online Refunds Application")
        self.assertEqual(self.counts(), before)  # nothing filed, stored or recorded
        text = json.dumps(data).lower()
        for word in ("score", "risk", "lane", "eligible", "approved", "rejected", "fraud"):
            self.assertNotIn(word, text)

    def test_each_taxpayer_reads_only_its_own_records(self) -> None:
        data = self.portal("b")["data"]
        self.assertEqual(data["status"], "no_issues_found")
        self.assertEqual(data["figures"]["lekuka_input_vat"], "1500.00")
        self.assertEqual(data["counts"]["lekuka_invoices"], 1)

    def test_unavailable_source_is_never_reported_as_clean(self) -> None:
        self.unavailable.append("idms")
        data = self.portal("b")["data"]
        self.assertEqual(data["status"], "incomplete")
        self.assertIn("source_not_checked", [p["code"] for p in data["prompts"]])
        self.assertIsNone(data["figures"]["lekuka_input_vat"])

    def test_bad_period_and_missing_return(self) -> None:
        with self.assertRaises(frappe.ValidationError):
            self.portal(period="2099-01")
        data = self.portal(period="2026-08")["data"]
        self.assertEqual(data["prompts"][0]["code"], "no_vat_return")
        self.assertEqual(data["status"], "incomplete")

    def test_rsl_front_door_calls_it_as_a_service_and_staff_cannot(self) -> None:
        from werkzeug.test import EnvironBuilder
        from werkzeug.wrappers import Request

        def call(who: str) -> dict[str, Any]:
            builder = EnvironBuilder(method="GET", headers={"X-Vati-Tenant-ID": self.tenant})
            frappe.local.request = Request(builder.get_environ())
            self.as_user(who)
            try:
                result: dict[str, Any] = prefiling_api.check_before_filing(
                    tin=TIN_A, period="2026-09"
                )
                return result
            finally:
                frappe.set_user("Administrator")
                frappe.local.request = None  # fmt: skip

        data = call("service")["data"]
        self.assertEqual(data["status"], "check_these")
        self.assertTrue(data["guidance_only"])
        for who in ("auditor", "a"):
            with self.subTest(who=who):
                with self.assertRaises(frappe.PermissionError):
                    call(who)
        # Staff and service cannot use the taxpayer portal method.
        for who in ("auditor", "service"):
            with self.subTest(portal=who):
                with self.assertRaises(frappe.PermissionError):
                    self.portal(who)
