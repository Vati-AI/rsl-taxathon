"""Bench tests for refund-confidentiality access (TAX-142, ADR-049).

Run on a disposable site: ``python3 scripts/frappe_dev.py verify``. A refund case (a case
whose submission is a refund claim) and an ordinary case are read by every staff
combination through the gate, Desk list queries, ``has_permission`` and the /api/v1
router. Every refusal must be sealed into the tenant's audit ledger.
"""

from __future__ import annotations

import hashlib
import json
import unittest
import uuid
from typing import Any

try:
    import frappe
    from frappe.tests import IntegrationTestCase
    from werkzeug.test import EnvironBuilder
    from werkzeug.wrappers import Request

    from vati_tax.api_v1 import api as api_v1
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.ids import mapping
    from vati_tax.ids.canonical import InvalidMapping
    from vati_tax.refund_access import store
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

AUDIT = "Vati Audit Event"
FINDING = "Vati Case Finding"


def hexkey() -> str:
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()


class RefundAccessBenchTests(IntegrationTestCase):
    def setUp(self) -> None:
        frappe.set_user("Administrator")
        clear_principal_cache()
        store.clear_cache()
        run = uuid.uuid4().hex[:8]
        self.tenant = ensure_tenant(str(uuid.uuid4()), "Synthetic TAX-142 tenant")
        self.users: dict[str, str] = {}
        staff: list[tuple[str, policy.Role, bool]] = [
            ("auditor", policy.AUDITOR, False),
            ("refund-auditor-a", policy.AUDITOR, True),
            ("refund-auditor-b", policy.AUDITOR, True),
            ("refund-specialist", policy.SPECIALIST, True),
            ("supervisor", policy.SUPERVISOR, False),
            ("refund-supervisor", policy.SUPERVISOR, True),
        ]
        for name, role, refund_team in staff:
            user = f"tax142-{name}-{run}@example.test"
            frappe.get_doc({"doctype": "User", "email": user, "first_name": name,
                            "send_welcome_email": 0}).insert(ignore_permissions=True)  # fmt: skip
            grant_membership(user, self.tenant, role, refund_team=refund_team)
            self.users[name] = user
        self.users["service"] = ensure_service_identity(f"tax142-svc-{run}@example.test",
                                                        [self.tenant])  # fmt: skip
        self.taxpayer = (
            frappe.get_doc(
                {
                    "doctype": "Vati Taxpayer",
                    "tenant_id": self.tenant,
                    "tin": "510000006",
                    "display_name": "Synthetic refund taxpayer",
                    "taxpayer_type": "company",
                }
            )  # fmt: skip
            .insert(ignore_permissions=True)
            .name
        )
        self.users["taxpayer"] = f"tax142-taxpayer-{run}@example.test"
        frappe.get_doc({"doctype": "User", "email": self.users["taxpayer"], "first_name": "tp",
                        "send_welcome_email": 0}).insert(ignore_permissions=True)  # fmt: skip
        grant_membership(self.users["taxpayer"], self.tenant, policy.TAXPAYER, self.taxpayer)
        self.submission = frappe.get_doc({
            "doctype": "Vati Submission", "tenant_id": self.tenant, "taxpayer_id": self.taxpayer,
            "submission_type": "refund_claim", "period": "2026-09",
            "idempotency_key": "synthetic-" + uuid.uuid4().hex, "synthetic": 1,
        }).insert(ignore_permissions=True).name  # fmt: skip
        self.refund_case = mapping.register_case(
            tenant_id=self.tenant, idempotency_key="synthetic-" + uuid.uuid4().hex,
            taxpayer_id=self.taxpayer, submission_id=self.submission,
        )["case_id"]  # fmt: skip
        # An ordinary (VAT return) case: the case list shows only cases with a submission.
        plain_submission = frappe.get_doc({
            "doctype": "Vati Submission", "tenant_id": self.tenant, "taxpayer_id": self.taxpayer,
            "submission_type": "vat_return", "period": "2026-09",
            "idempotency_key": "synthetic-" + uuid.uuid4().hex, "synthetic": 1,
        }).insert(ignore_permissions=True).name  # fmt: skip
        self.plain_case = mapping.register_case(
            tenant_id=self.tenant, idempotency_key="synthetic-" + uuid.uuid4().hex,
            taxpayer_id=self.taxpayer, submission_id=plain_submission,
        )["case_id"]  # fmt: skip
        self.refund_finding = self.finding(self.refund_case)
        self.plain_finding = self.finding(self.plain_case)
        frappe.db.commit()

    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        frappe.local.request = None
        store.clear_cache()
        frappe.db.rollback()

    # --- helpers ---------------------------------------------------------------------

    def finding(self, case_id: str) -> str:
        finding_id = str(uuid.uuid4())
        frappe.get_doc({
            "doctype": FINDING, "finding_id": finding_id, "tenant_id": self.tenant,
            "case_id": case_id, "revision": 1, "kind": "missing_evidence",
            "category": "refund_reconciliation", "code": "invoice_not_in_lekuka",
            "explanation": "Two claimed invoices have no matching Lekuka record.",
            "evidence_refs": "[]", "confidence": "medium", "alternative_explanations": "[]",
            "legal_route_status": "unverified", "proposed_action": "none", "producer": "officer",
            "producer_version": "officer-manual-v1", "recorded_by": "Administrator",
            "recorder_role": "auditor", "created_at": frappe.utils.now_datetime(),
            "lineage_key": hexkey(), "revision_key": hexkey(), "registration_key": hexkey(),
            "request_digest": hexkey(),
        }).insert(ignore_permissions=True)  # fmt: skip
        return finding_id

    def as_user(self, name: str) -> str:
        frappe.set_user(self.users[name])
        clear_principal_cache()
        store.clear_cache()
        return self.users[name]

    def admin(self) -> None:
        frappe.set_user("Administrator")
        clear_principal_cache()

    def can(self, name: str, case_id: str) -> bool:
        self.as_user(name)
        try:
            store.require_case(case_id, policy.CASE_READ, self.tenant)
            return True
        except frappe.PermissionError:
            return False
        finally:
            self.admin()

    def assign(self, assignee: str, who: str = "refund-supervisor", **kw: Any) -> dict[str, Any]:
        self.as_user(who)
        try:
            return store.assign_case(tenant_id=self.tenant, case_id=self.refund_case,
                                     assignee=self.users[assignee],
                                     reason="Refund team workload: this claim needs review.",
                                     **kw)  # fmt: skip
        finally:
            self.admin()

    def denials(self, user: str) -> list[Any]:
        return frappe.get_all(
            AUDIT,
            filters={"tenant_id": self.tenant, "actor": user, "category": "denied"},
            fields=["action", "outcome", "case_id", "subject_type"],
            order_by="chain_seq asc",
        )

    def api(self, who: str, path: str) -> tuple[int, dict[str, Any]]:
        frappe.set_user(self.users[who])
        clear_principal_cache()
        store.clear_cache()
        headers = {"X-Vati-Tenant-ID": self.tenant, "X-Correlation-ID": str(uuid.uuid4())}
        builder = EnvironBuilder(method="GET", path="/api/method/vati_tax.api_v1.api.route",
                                 query_string={"path": path}, headers=headers)  # fmt: skip
        frappe.local.request = Request(builder.get_environ())
        try:
            response = api_v1.route()
        finally:
            frappe.local.request = None
            self.admin()
        return response.status_code, json.loads(response.get_data())

    # --- tests -----------------------------------------------------------------------

    def test_only_refund_roles_read_a_refund_case(self) -> None:
        expected = {
            "auditor": False, "refund-auditor-a": False, "refund-auditor-b": False,
            "refund-specialist": False, "supervisor": False, "refund-supervisor": True,
            "service": True,
        }  # fmt: skip
        self.assertEqual({n: self.can(n, self.refund_case) for n in expected}, expected)
        self.assign("refund-auditor-a")
        expected["refund-auditor-a"] = True
        self.assertEqual({n: self.can(n, self.refund_case) for n in expected}, expected)
        # Reassigned: the first officer loses access, the new one gains it.
        self.assign("refund-specialist", expected_version=1)
        expected.update({"refund-auditor-a": False, "refund-specialist": True})
        self.assertEqual({n: self.can(n, self.refund_case) for n in expected}, expected)
        # An ordinary case stays visible to all staff.
        self.assertTrue(all(self.can(n, self.plain_case) for n in expected))

    def test_every_refusal_is_recorded_in_the_ledger(self) -> None:
        self.assertFalse(self.can("auditor", self.refund_case))
        frappe.db.rollback()  # the refusal was committed by the gate itself
        rows = self.denials(self.users["auditor"])
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].action, rows[0].outcome, rows[0].case_id, rows[0].subject_type),
                         ("case.read.denied", "refund_confidentiality", self.refund_case,
                          "case"))  # fmt: skip
        self.assertTrue(self.can("refund-supervisor", self.refund_case))
        self.assertEqual(self.denials(self.users["refund-supervisor"]), [])

    def test_desk_lists_and_documents_follow_the_rule(self) -> None:
        def visible(name: str, doctype: str, field: str) -> set[str]:
            self.as_user(name)
            try:
                return set(frappe.get_list(doctype, pluck=field, limit_page_length=0))
            finally:
                self.admin()

        plain = {self.plain_finding}
        both = {self.plain_finding, self.refund_finding}
        self.assertEqual(visible("auditor", FINDING, "finding_id") & both, plain)
        self.assertEqual(visible("refund-auditor-a", FINDING, "finding_id") & both, plain)
        self.assertEqual(visible("refund-supervisor", FINDING, "finding_id") & both, both)
        self.assertNotIn(self.submission, visible("auditor", "Vati Submission", "name"))
        self.assign("refund-auditor-a")
        self.assertEqual(visible("refund-auditor-a", FINDING, "finding_id") & both, both)
        self.assertIn(self.submission, visible("refund-auditor-a", "Vati Submission", "name"))
        # The taxpayer still sees its own refund claim.
        self.assertIn(self.submission, visible("taxpayer", "Vati Submission", "name"))
        # Opening the document directly is refused and recorded.
        doc = frappe.get_doc(FINDING, self.refund_finding)
        self.as_user("auditor")
        try:
            self.assertFalse(frappe.has_permission(FINDING, "read", doc))
        finally:
            self.admin()
        actions = [r.action for r in self.denials(self.users["auditor"])]
        self.assertIn("vati_case_finding.read.denied", actions)
        self.as_user("refund-auditor-a")
        try:
            self.assertTrue(frappe.has_permission(FINDING, "read", doc))
        finally:
            self.admin()

    def test_api_v1_hides_and_refuses_refund_cases(self) -> None:
        status, body = self.api("auditor", f"/api/v1/cases/{self.refund_case}")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "forbidden")
        status, body = self.api("auditor", "/api/v1/cases")
        self.assertEqual(status, 200)
        listed = {c["case_id"] for c in body["data"]}
        self.assertIn(self.plain_case, listed)
        self.assertNotIn(self.refund_case, listed)
        status, _ = self.api("auditor", f"/api/v1/cases/{self.refund_case}/findings")
        self.assertEqual(status, 403)
        status, body = self.api("refund-supervisor", "/api/v1/cases")
        self.assertIn(self.refund_case, {c["case_id"] for c in body["data"]})
        self.assertEqual(self.api("refund-supervisor", f"/api/v1/cases/{self.refund_case}")[0],
                         200)  # fmt: skip
        actions = [r.action for r in self.denials(self.users["auditor"])]
        self.assertIn("api_v1.getCase.denied", actions)
        self.assertIn("api_v1.listFindings.denied", actions)

    def test_assignment_rules(self) -> None:
        with self.assertRaises(frappe.PermissionError):
            self.assign("refund-auditor-a", who="refund-auditor-b")  # not a supervisor
        with self.assertRaises(frappe.PermissionError):
            self.assign("refund-auditor-a", who="supervisor")  # not on the refund team
        with self.assertRaises(InvalidMapping) as caught:
            self.assign("auditor")  # assignee not on the refund team
        self.assertEqual(caught.exception.field_errors[0]["code"], "not_refund_team")
        view = self.assign("refund-auditor-a")
        self.assertEqual((view["assignee"], view["assignee_role"], view["version"]),
                         (self.users["refund-auditor-a"], "auditor", 1))  # fmt: skip
        with self.assertRaises(InvalidMapping):
            self.assign("refund-auditor-b", expected_version=5)  # stale
        self.assertEqual(self.assign("refund-auditor-b", expected_version=1)["version"], 2)
        self.as_user("supervisor")
        try:
            with self.assertRaises(InvalidMapping):
                store.assign_case(tenant_id=self.tenant, case_id=self.plain_case,
                                  assignee=self.users["refund-auditor-a"],
                                  reason="Not a refund case at all, should fail.")  # fmt: skip
        finally:
            self.admin()
        entries = frappe.get_all(AUDIT, filters={"tenant_id": self.tenant,
                                                 "action": "case.assigned"},
                                 fields=["actor", "target_actor"], order_by="chain_seq asc")  # fmt: skip
        self.assertEqual([e.target_actor for e in entries],
                         [self.users["refund-auditor-a"], self.users["refund-auditor-b"]])  # fmt: skip
        self.as_user("refund-auditor-b")
        try:
            got = store.case_assignment(tenant_id=self.tenant, case_id=self.refund_case)
        finally:
            self.admin()
        self.assertEqual(got["assignee"] if got else None, self.users["refund-auditor-b"])
        with self.assertRaises(frappe.PermissionError):
            self.as_user("refund-auditor-a")
            try:
                store.case_assignment(tenant_id=self.tenant, case_id=self.refund_case)
            finally:
                self.admin()

    def test_refund_team_cannot_be_given_to_a_taxpayer_or_service(self) -> None:
        with self.assertRaises(frappe.ValidationError):
            grant_membership(self.users["taxpayer"], self.tenant, policy.TAXPAYER,
                             self.taxpayer, refund_team=True)  # fmt: skip
