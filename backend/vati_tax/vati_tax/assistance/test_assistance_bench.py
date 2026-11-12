"""Bench tests for the taxpayer-assistance service (TAX-100).

Run on a disposable site (``scripts/frappe_dev.py verify`` does this)::

    bench --site SITE run-tests --module vati_tax.assistance.test_assistance_bench

Under plain ``unittest`` without a site this module is skipped, not passed. The
tenants, users, requests and uploads come from the TAX-045 bench helpers; the
assistant runtime is a stand-in that drafts from the stored request, so these
tests cover the Frappe ports (records, drafts, TAX-045 confirmation, ledger),
not the assistant's judgement (that is TAX-098/TAX-099 with the TAX-102 harness).
"""

from __future__ import annotations

import json
import unittest
import uuid
from typing import Any
from unittest import mock

try:
    import frappe

    from vati_tax.api_v1 import backend, core
    from vati_tax.assistance import runtime
    from vati_tax.responses.test_responses_bench import EvidenceResponseTests
except unittest.SkipTest:
    raise
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

DRAFT = "Vati Assistance Draft"
RESPONSE = "Vati Evidence Response"
REQUEST = "Vati Evidence Request"
AUDIT = "Vati Audit Event"


def stand_in(store: dict[str, Any]) -> Any:
    """Drafts every item as missing (it cites nothing), in the contract shape."""

    def answer(request: dict[str, Any]) -> dict[str, Any]:
        if request["operation_id"] != "draftEvidenceResponse":
            return {"status": 503, "body": {"error": {"code": "upstream_unavailable"}}}
        req = store["evidence_requests"][request["body"]["request_id"]]
        data = {
            "draft_id": str(uuid.uuid4()),
            "tenant_id": request["caller"]["tenant_id"],
            "taxpayer_id": request["caller"]["taxpayer_id"],
            "request_id": req["request_id"],
            "status": "draft",
            "version": 1,
            "due_date": req["due_date"],
            "request_summary": "Synthetic: RSL asks for the documents listed.",
            "items": [
                {"item_number": i["item_number"], "document_type": i["document_type"],
                 "plain_language_request": i["description"], "match": "missing",
                 "proposed_documents": [],
                 "missing_explanation": "Upload this document, or explain why you cannot."}
                for i in req["items"]
            ],
            "proposed_message": None,
            "disclosure": {"source": "template", "provider": "template",
                           "model_id": "deterministic-template-v1", "fell_back": False,
                           "advisory_only": True},
            "created_at": "2026-11-12T09:00:00Z",
            "confirmation": None,
            "sent_to_rsl": False,
        }  # fmt: skip
        return {"status": 201, "body": {"data": data}}

    return answer


class AssistanceServiceTests(EvidenceResponseTests):
    # Reuse the TAX-045 fixtures, not its tests.
    locals().update({name: None for name in dir(EvidenceResponseTests) if name.startswith("test_")})

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(runtime, "load", return_value=stand_in)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, who: str, method: str, path: str, body: Any = None,
             key: str | None = None, tenant: str | None = None) -> core.ApiResponse:  # fmt: skip
        self.as_user(who)
        headers = {"X-Vati-Tenant-ID": tenant or self.t1,
                   "X-Correlation-ID": str(uuid.uuid4())}  # fmt: skip
        if method == "POST":
            headers["Idempotency-Key"] = key or ("tax100-" + uuid.uuid4().hex)
        return core.handle(core.ApiRequest(method, path, headers, body=body), backend.HANDLERS,
                           authenticated=True, translate=backend.translate)  # fmt: skip

    def drafts(self, taxpayer: str) -> str:
        return f"/api/v1/taxpayers/{taxpayer}/assistance/evidence-response-drafts"

    def test_draft_then_confirm_enters_the_tax045_flow_once(self) -> None:
        request_id = self.new_request(self.t1, self.tp_a)
        evidence = self.upload("taxpayer-a", self.t1)
        made = self.call("taxpayer-a", "POST", self.drafts(self.tp_a),
                         {"request_id": request_id, "taxpayer_note": None})  # fmt: skip
        self.assertEqual(made.status, 201, made.body)
        draft = made.body["data"]
        frappe.set_user("Administrator")
        # Unconfirmed: nothing reached TAX-045.
        self.assertEqual(frappe.db.count(RESPONSE, {"request_id": request_id}), 0)
        self.assertEqual(frappe.db.get_value(REQUEST, request_id, "status"), "recorded")
        body = {"expected_version": 1, "declaration": True, "message": None, "items": [
            {"item_number": 1, "evidence_ids": [evidence], "reason_unavailable": None},
            {"item_number": 2, "evidence_ids": [], "reason_unavailable": "Account closed."},
        ]}  # fmt: skip
        key = "tax100-confirm-" + uuid.uuid4().hex
        path = f"{self.drafts(self.tp_a)}/{draft['draft_id']}/confirm"
        done = self.call("taxpayer-a", "POST", path, body, key=key)
        self.assertEqual(done.status, 200, done.body)
        self.assertEqual(done.body["data"]["status"], "confirmed")
        again = self.call("taxpayer-a", "POST", path, body, key=key)
        self.assertEqual(again.headers.get("Idempotent-Replayed"), "true")
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.count(RESPONSE, {"request_id": request_id}), 1)
        self.assertEqual(frappe.db.get_value(REQUEST, request_id, "status"), "responded")
        stored = frappe.get_doc(DRAFT, draft["draft_id"])
        self.assertEqual((stored.status, stored.version), ("confirmed", 2))
        actions = frappe.get_all(AUDIT, filters={"subject_type": "assistance"}, pluck="action")
        self.assertIn("assistance.draft_saved", actions)
        self.assertIn("assistance.draft_confirmed", actions)

    def test_only_the_taxpayers_own_records_are_reachable(self) -> None:
        theirs = self.new_request(self.t1, self.tp_b)
        result = self.call("taxpayer-a", "POST", self.drafts(self.tp_b),
                           {"request_id": theirs, "taxpayer_note": None})  # fmt: skip
        self.assertEqual(result.status, 404)
        result = self.call("taxpayer-a", "POST", self.drafts(self.tp_a),
                           {"request_id": theirs, "taxpayer_note": None})  # fmt: skip
        self.assertEqual(result.status, 422)
        staff = self.call("auditor-t1", "POST", self.drafts(self.tp_a),
                          {"request_id": theirs, "taxpayer_note": None})  # fmt: skip
        self.assertEqual(staff.status, 403)
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.count(DRAFT, {"request_id": theirs}), 0)

    def test_taxpayers_have_no_direct_access_to_drafts(self) -> None:
        request_id = self.new_request(self.t1, self.tp_a)
        made = self.call("taxpayer-a", "POST", self.drafts(self.tp_a),
                         {"request_id": request_id, "taxpayer_note": None})  # fmt: skip
        self.as_user("taxpayer-a")
        with self.assertRaises(frappe.PermissionError):
            frappe.get_list(DRAFT, pluck="name")
        text = json.dumps(made.body)
        for internal in ("case_id", "finding_ids", "authorised_by", "internal-digest"):
            self.assertNotIn(internal, text)


if __name__ == "__main__":
    unittest.main()
