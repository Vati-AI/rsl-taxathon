"""Bench tests for the live /api/v1 service (TAX-090, ADR-046).

Run on a disposable site: ``python3 scripts/frappe_dev.py verify``. Every request
goes through the whitelisted ``vati_tax.api_v1.api.route`` with a real werkzeug
request, as the signed-in user, and every response is checked with the contract's
own ``check_example`` (status, headers, body schema, tenant/case consistency).
Jube is the in-process ``FakeLifecycle`` from decisions/test_engine.py.
"""

from __future__ import annotations

import datetime
import importlib
import json
import unittest
import uuid
from typing import Any

try:
    import frappe
    from frappe.tests import IntegrationTestCase
    from werkzeug.test import EnvironBuilder
    from werkzeug.wrappers import Request

    from vati_tax.api_v1 import api, backend
    from vati_tax.api_v1.core import officer_id
    from vati_tax.api_v1.operations import SERVED
    from vati_tax.decisions import store as decisions_store
    from vati_tax.decisions.test_engine import FakeCase, FakeLifecycle
    from vati_tax.evidence import store as evidence_store
    from vati_tax.evidence.test_evidence_bench import pdf
    from vati_tax.findings import store as findings_store
    from vati_tax.findings.test_engine import SIGNALS
    from vati_tax.findings.test_findings_bench import SOURCES
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.ids import mapping
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

RUN = uuid.uuid4().hex[:8]
contract = importlib.import_module("integration.api.contract")
SPEC = contract.load_spec()


def email(name: str) -> str:
    return f"tax090-{name}-{RUN}@example.test"


def make_user(name: str) -> str:
    user = email(name)
    frappe.get_doc(
        {"doctype": "User", "email": user, "first_name": f"Synthetic {name}",
         "send_welcome_email": 0}
    ).insert(ignore_permissions=True)  # fmt: skip
    return user


def key() -> str:
    return "synthetic-" + uuid.uuid4().hex


class ApiV1BenchTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.t1 = ensure_tenant(str(uuid.uuid4()), "Synthetic API tenant one")
        cls.t2 = ensure_tenant(str(uuid.uuid4()), "Synthetic API tenant two")
        cls.taxpayer = (
            frappe.get_doc(
                {
                    "doctype": "Vati Taxpayer",
                    "tenant_id": cls.t1,
                    "display_name": "Synthetic Imports (Pty) Ltd",
                    "taxpayer_type": "company",
                    "tin": str(400000000 + uuid.uuid4().int % 99999999),
                }
            )
            .insert(ignore_permissions=True)
            .name  # fmt: skip
        )
        cls.users = {}
        grants: list[tuple[str, str, policy.Role, str | None]] = [
            ("taxpayer", cls.t1, policy.TAXPAYER, cls.taxpayer),
            ("auditor", cls.t1, policy.AUDITOR, None),
            ("supervisor", cls.t1, policy.SUPERVISOR, None),
            ("auditor-t2", cls.t2, policy.AUDITOR, None),
        ]
        for name, tenant, role, taxpayer in grants:
            cls.users[name] = make_user(name)
            grant_membership(cls.users[name], tenant, role, taxpayer)
        cls.users["service"] = ensure_service_identity(email("service"), [cls.t1])
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        decisions_store.set_lifecycle_factory(None)
        frappe.local.request = None
        frappe.db.rollback()
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        clear_principal_cache()
        self.jube = FakeLifecycle()
        decisions_store.set_lifecycle_factory(lambda _tenant: self.jube)
        self.case_id = mapping.register_case(
            tenant_id=self.t1,
            idempotency_key=key(),
            taxpayer_id=self.taxpayer,
            submission_id=str(uuid.uuid4()),
        )["case_id"]
        self.jube.cases[self.case_id] = FakeCase("AWAITING_HUMAN")
        frappe.get_doc(
            {"doctype": "Vati Case Sync", "tenant_id": self.t1, "case_id": self.case_id,
             "case_status": "AWAITING_HUMAN", "observed_at": datetime.datetime.utcnow()}
        ).insert(ignore_permissions=True)  # fmt: skip

    def tearDown(self):
        frappe.set_user("Administrator")
        decisions_store.set_lifecycle_factory(None)
        frappe.local.request = None

    # --- helpers ---------------------------------------------------------------------

    def call(
        self,
        who: str | None,
        method: str,
        path: str,
        *,
        tenant: str | None = None,
        body: Any = None,
        idem: str | None = None,
        query: dict[str, str] | None = None,
        conformant: bool = True,
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        """One request through the whitelisted route, checked against the contract."""
        user = self.users[who] if who else "Guest"
        frappe.set_user(user)
        clear_principal_cache()
        tenant = self.t1 if tenant is None else tenant
        headers = {"X-Vati-Tenant-ID": tenant, "X-Correlation-ID": str(uuid.uuid4())}
        if idem:
            headers["Idempotency-Key"] = idem
        builder = EnvironBuilder(
            method=method,
            path="/api/method/vati_tax.api_v1.api.route",
            query_string={"path": path, **(query or {})},
            headers=headers,
            data=json.dumps(body) if body is not None else None,
            content_type="application/json" if body is not None else None,
        )
        frappe.local.request = Request(builder.get_environ())
        try:
            response = api.route()
        finally:
            frappe.set_user("Administrator")
            frappe.local.request = None
        out = json.loads(response.get_data())
        res_headers = dict(response.headers.items())
        if who is not None:
            problems = contract.check_example(
                SPEC,
                {
                    "name": f"live {method} {path}",
                    "operation_id": contract.match_operation(SPEC, method, path)[1]["operationId"],
                    "synthetic": True,
                    "request_conformant": conformant,
                    "request": {
                        "method": method,
                        "path": path,
                        "headers": headers,
                        "query": query or {},
                        "body": body,
                    },  # fmt: skip
                    "response": {
                        "status": response.status_code,
                        "headers": res_headers,
                        "body": out,
                    },  # fmt: skip
                },
            )
            self.assertEqual(problems, [], out)
        self.assertEqual(res_headers["X-Vati-Mode"], "live")
        return response.status_code, res_headers, out

    def case_path(self, suffix: str = "") -> str:
        return f"/api/v1/cases/{self.case_id}{suffix}"

    def version(self) -> int:
        return decisions_store.FrappeRepository().case_version(self.t1, self.case_id)

    def proposal(self, **overrides: Any) -> dict[str, Any]:
        return {
            "outcome": "continue_investigation",
            "rationale": "Import declarations need comparing with the invoices first.",
            "rating": 2,
            "expected_case_status": "AWAITING_HUMAN",
            "expected_version": self.version(),
            "evidence_ids": [],
            "finding_ids": [],
            **overrides,
        }

    def evidence(self) -> dict[str, str]:
        frappe.set_user(self.users["auditor"])
        clear_principal_cache()
        ids = {}
        for source in SOURCES:
            view = evidence_store.register_upload(
                tenant_id=self.t1, content=pdf(f"tax090-{source}-{RUN}-{self.case_id}"),
                filename=f"synthetic-{source.lower()}.pdf", document_type="invoice",
                idempotency_key=key(), taxpayer_id=self.taxpayer, case_id=self.case_id,
            )  # fmt: skip
            ids[source] = view["evidence_id"]
        frappe.set_user("Administrator")
        return ids

    def findings(self) -> list[dict[str, Any]]:
        evidence = self.evidence()
        frappe.set_user(self.users["service"])
        clear_principal_cache()
        for signal in SIGNALS:
            findings_store.record_rule_signal(
                tenant_id=self.t1, case_id=self.case_id, signal=signal, evidence=evidence
            )
        frappe.set_user(self.users["auditor"])
        clear_principal_cache()
        views = findings_store.list_findings(tenant_id=self.t1, case_id=self.case_id) or []
        frappe.set_user("Administrator")
        return views

    # --- tests -----------------------------------------------------------------------

    def test_handlers_serve_exactly_the_declared_operations(self):
        self.assertEqual(set(backend.HANDLERS), set(SERVED))

    def test_queue_and_case_project_the_jube_status_and_version(self):
        status, _, body = self.call("auditor", "GET", "/api/v1/cases", query={"limit": "100"})
        self.assertEqual(status, 200)
        case = next(c for c in body["data"] if c["case_id"] == self.case_id)
        self.assertEqual(case["status"], "AWAITING_HUMAN")
        self.assertEqual(case["version"], self.version())
        self.assertEqual(case["taxpayer"]["display_name"], "Synthetic Imports (Pty) Ltd")
        _, _, filtered = self.call(
            "auditor", "GET", "/api/v1/cases", query={"status": "RESOLVED", "limit": "100"}
        )
        self.assertNotIn(self.case_id, [c["case_id"] for c in filtered["data"]])
        status, _, one = self.call("auditor", "GET", self.case_path())
        self.assertEqual((status, one["data"]["case_id"]), (200, self.case_id))

    def test_paging_is_complete_and_stable(self):
        for _ in range(2):
            mapping.register_case(tenant_id=self.t1, idempotency_key=key(),
                                  taxpayer_id=self.taxpayer, submission_id=str(uuid.uuid4()))  # fmt: skip
        seen, cursor = [], None
        while True:
            query = {"limit": "1", **({"cursor": cursor} if cursor else {})}
            _, _, body = self.call("auditor", "GET", "/api/v1/cases", query=query)
            seen += [c["case_id"] for c in body["data"]]
            if not body["meta"]["has_more"]:
                break
            cursor = body["meta"]["next_cursor"]
        self.assertEqual(len(seen), len(set(seen)))
        self.assertIn(self.case_id, seen)

    def test_tenant_isolation_and_roles_are_enforced(self):
        status, _, body = self.call("auditor-t2", "GET", self.case_path(), tenant=self.t1)
        self.assertEqual((status, body["error"]["code"]), (403, "tenant_scope_denied"))
        status, _, body = self.call("auditor-t2", "GET", self.case_path(), tenant=self.t2)
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))
        for path in ("/api/v1/cases", self.case_path("/briefing"), self.case_path("/review")):
            status, _, body = self.call("taxpayer", "GET", path)
            self.assertEqual((status, body["error"]["code"]), (403, "forbidden"), path)
        status, _, body = self.call("auditor", "GET", "/api/v1/management/summary")
        self.assertEqual((status, body["error"]["code"]), (403, "forbidden"))
        status, _, body = self.call(None, "GET", "/api/v1/cases")
        self.assertEqual((status, body["error"]["code"]), (401, "unauthenticated"))

    def test_refusals_are_kept_in_the_ledger(self):
        denied = {"tenant_id": self.t1, "action": "case.read.denied"}
        before = frappe.db.count("Vati Audit Event", denied)
        self.call("taxpayer", "GET", "/api/v1/cases")
        after = frappe.db.count("Vati Audit Event", denied)
        self.assertEqual(after, before + 1)

    def test_taxpayer_profile_lists_open_cases(self):
        status, _, body = self.call("auditor", "GET", f"/api/v1/taxpayers/{self.taxpayer}")
        self.assertEqual(status, 200)
        self.assertIn(self.case_id, [c["case_id"] for c in body["data"]["open_cases"]])
        status, _, _ = self.call("auditor", "GET", f"/api/v1/taxpayers/{uuid.uuid4()}")
        self.assertEqual(status, 404)

    def test_decision_lifecycle_through_the_api(self):
        idem = key()
        status, headers, body = self.call(
            "auditor", "POST", self.case_path("/decisions"), body=self.proposal(), idem=idem
        )
        self.assertEqual(status, 201, body)
        decision = body["data"]
        self.assertEqual(decision["proposed_by"], officer_id(self.users["auditor"]))
        self.assertNotIn("Idempotent-Replayed", headers)
        replay = self.call("auditor", "POST", self.case_path("/decisions"),
                           body=self.proposal(expected_version=decision["case_version"]), idem=idem)  # fmt: skip
        self.assertEqual(replay[0], 201)
        self.assertEqual(replay[1].get("Idempotent-Replayed"), "true")
        changed = self.call("auditor", "POST", self.case_path("/decisions"),
                            body=self.proposal(rating=5), idem=idem)  # fmt: skip
        self.assertEqual((changed[0], changed[2]["error"]["code"]), (422, "idempotency_conflict"))

        _, _, review = self.call("supervisor", "GET", self.case_path("/review"))
        self.assertEqual(review["data"]["open_decision"]["decision_id"], decision["decision_id"])
        self.assertIn("decision.approve", review["data"]["viewer"]["allowed_actions"])
        path = self.case_path(f"/decisions/{decision['decision_id']}/review")
        stale = self.call("supervisor", "POST", path, idem=key(),
                          body={"action": "approve", "expected_version": 1})  # fmt: skip
        self.assertEqual((stale[0], stale[2]["error"]["code"]), (409, "version_conflict"))
        self.assertEqual(stale[2]["error"]["current_version"], self.version())
        own = self.call("auditor", "POST", path, idem=key(),
                        body={"action": "approve", "expected_version": self.version()})  # fmt: skip
        self.assertEqual((own[0], own[2]["error"]["code"]), (403, "forbidden"))
        status, _, approved = self.call("supervisor", "POST", path, idem=key(),
                                        body={"action": "approve", "expected_version": self.version()})  # fmt: skip
        self.assertEqual(status, 200, approved)
        self.assertEqual(approved["data"]["status"], "approved")
        self.assertEqual(approved["data"]["reviewed_by"], officer_id(self.users["supervisor"]))
        other = mapping.register_case(tenant_id=self.t1, idempotency_key=key(),
                                      taxpayer_id=self.taxpayer, submission_id=str(uuid.uuid4()))  # fmt: skip
        wrong = self.call("supervisor", "POST",
                          f"/api/v1/cases/{other['case_id']}/decisions/{decision['decision_id']}/review",
                          idem=key(), body={"action": "approve", "expected_version": 1})  # fmt: skip
        self.assertEqual(wrong[0], 404)

    def test_evidence_findings_feedback_and_requests(self):
        views = self.findings()
        status, _, evidence = self.call("auditor", "GET", self.case_path("/evidence"))
        self.assertEqual((status, len(evidence["data"])), (200, len(SOURCES)))
        status, _, listed = self.call("auditor", "GET", self.case_path("/findings"))
        self.assertEqual((status, len(listed["data"])), (200, len(views)))
        finding = listed["data"][0]
        feedback = {"verdict": "rejected", "reason_code": "evidence_does_not_support",
                    "reason": "The cited invoice was paid in the next period.",
                    "modified_explanation": None}  # fmt: skip
        idem = key()
        status, _, recorded = self.call(
            "auditor", "POST", self.case_path(f"/findings/{finding['finding_id']}/feedback"),
            body=feedback, idem=idem,
        )  # fmt: skip
        self.assertEqual(status, 201, recorded)
        self.assertEqual(
            recorded["data"]["recorded_by"]["officer_id"], officer_id(self.users["auditor"])
        )
        self.assertFalse(recorded["data"]["changes_finding"])
        actor = self.call("auditor", "POST", self.case_path(f"/findings/{finding['finding_id']}/feedback"),
                          body={**feedback, "recorded_by": "someone"}, idem=key(), conformant=False)  # fmt: skip
        self.assertEqual((actor[0], actor[2]["error"]["code"]), (400, "invalid_request"))
        request = {"expected_version": self.version(), "finding_ids": [finding["finding_id"]],
                   "items": [{"document_type": "contract", "description": "The signed agreement."}],
                   "due_date": (datetime.date.today() + datetime.timedelta(days=14)).isoformat(),
                   "message_to_taxpayer": "Please upload the signed service agreement."}  # fmt: skip
        status, _, made = self.call("auditor", "POST", self.case_path("/request-evidence"),
                                    body=request, idem=key())  # fmt: skip
        self.assertEqual(status, 202, made)
        self.assertEqual(made["data"]["authorised_by"], officer_id(self.users["auditor"]))

    def test_entities_are_empty_until_the_pipeline_indexes_the_case(self):
        status, _, body = self.call("auditor", "GET", self.case_path("/entities"))
        self.assertEqual((status, body["data"]), (200, []))

    def test_briefing_is_not_found_until_one_is_recorded(self):
        status, _, body = self.call("auditor", "GET", self.case_path("/briefing"))
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))

    def test_supervisor_summary_and_integrity_review(self):
        status, _, body = self.call("supervisor", "GET", "/api/v1/management/summary",
                                    query={"window_days": "30"})  # fmt: skip
        self.assertEqual(status, 200, body)
        self.assertGreaterEqual(body["data"]["workload"]["open_cases"], 1)
        self.assertTrue(body["data"]["assurance"]["available"])
        metrics = [m["metric"] for m in body["data"]["missing_data"]]
        self.assertIn("workload", metrics)
        status, _, review = self.call("supervisor", "GET", "/api/v1/management/integrity")
        self.assertEqual(status, 200, review)
        status, _, denied = self.call("auditor", "GET", "/api/v1/management/integrity")
        self.assertEqual((status, denied["error"]["code"]), (403, "forbidden"))

    def test_workspace_session_follows_the_memberships(self):
        status, _, auditor = self.call("auditor", "GET", "/api/v1/session")
        self.assertEqual(status, 200, auditor)
        self.assertEqual(auditor["data"]["roles"], ["auditor"])
        self.assertEqual(auditor["data"]["officer_id"], officer_id(self.users["auditor"]))
        self.assertEqual(auditor["data"]["tenant_name"], "Synthetic API tenant one")
        self.assertNotIn("management", auditor["data"]["views"])
        _, _, supervisor = self.call("supervisor", "GET", "/api/v1/session")
        self.assertIn("reports", [a["key"] for a in supervisor["data"]["apps"]])
        status, _, body = self.call("taxpayer", "GET", "/api/v1/session")
        self.assertEqual((status, body["error"]["code"]), (403, "forbidden"))

    def test_unserved_operations_are_not_found(self):
        status, _, body = self.call(
            "auditor", "POST", self.case_path("/investigate"), idem=key(),
            body={"expected_version": 1, "playbook_id": "related_party_review@1"},
        )  # fmt: skip
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))
