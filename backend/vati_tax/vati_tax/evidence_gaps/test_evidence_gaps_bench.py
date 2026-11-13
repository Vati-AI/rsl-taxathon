"""Bench tests for gap-only evidence requests (TAX-127, ADR-050).

Run on a disposable site: ``python3 scripts/frappe_dev.py verify``. A claim read from the
Online Refunds Application (a synthetic RSL source fixture loaded by the real
``config.rsl_sources`` factory) has a lane proposal with the TAX-112 line match. An officer
reviews the proposal and approves it; only then does the taxpayer see the request.
"""

from __future__ import annotations

import hashlib
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
    from vati_tax.decisions import store as decisions_store
    from vati_tax.evidence_gaps import store
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.ids import mapping
    from vati_tax.integrations import config
    from vati_tax.refund_access import store as refund_access
    from vati_tax.responses import store as responses_store
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

TIN = "510000006"
CLAIM_REF = "ORA-2026-000731"
RSL_TENANT = "SYN-TENANT-RSL"
REQUEST = "Vati Evidence Request"


def hexkey() -> str:
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()


def idem() -> str:
    return "synthetic-" + uuid.uuid4().hex


def line(line_id: str, kind: str = "domestic") -> dict[str, Any]:
    return {"line_id": line_id, "kind": kind, "supplier_ref": "510000014",
            "invoice_number": f"INV-{line_id}", "invoice_date": "2026-09-14",
            "net_amount": "1000.00", "vat_amount": "150.00"}  # fmt: skip


CLAIM = {
    "claim_id": CLAIM_REF, "tin": TIN, "period": "2026-09", "return_id": "VAT-2026-09",
    "claimed_amount": "750.00", "submitted_on": "2026-10-19",
    "lines": [line("L1"), line("L2"), line("L3"), line("E1", "import"), line("E2", "import")],
    "uploaded_documents": [],
}  # fmt: skip
MATCH = [
    {"line_id": "L1", "status": "matched", "treatment": "accepted_as_evidence"},
    {"line_id": "L2", "status": "supplier_gap", "treatment": "targeted_question"},
    {"line_id": "L3", "status": "unintegrated_supplier", "treatment": "accepted_as_evidence"},
    {"line_id": "E1", "status": "matched", "treatment": "accepted_as_evidence"},
    {"line_id": "E2", "status": "unmatched", "treatment": "targeted_question"},
]


class EvidenceGapBenchTests(IntegrationTestCase):
    def setUp(self) -> None:
        frappe.set_user("Administrator")
        clear_principal_cache()
        refund_access.clear_cache()
        run = uuid.uuid4().hex[:8]
        self.tenant = ensure_tenant(str(uuid.uuid4()), "Synthetic TAX-127 tenant")
        self.users: dict[str, str] = {}
        staff: list[tuple[str, policy.Role, bool]] = [
            ("officer", policy.SUPERVISOR, True),
            ("auditor", policy.AUDITOR, False),
        ]
        for name, role, team in staff:
            user = f"tax127-{name}-{run}@example.test"
            frappe.get_doc({"doctype": "User", "email": user, "first_name": name,
                            "send_welcome_email": 0}).insert(ignore_permissions=True)  # fmt: skip
            grant_membership(user, self.tenant, role, refund_team=team)
            self.users[name] = user
        self.users["service"] = ensure_service_identity(f"tax127-svc-{run}@example.test",
                                                        [self.tenant])  # fmt: skip
        self.taxpayer = (
            frappe.get_doc(
                {
                    "doctype": "Vati Taxpayer",
                    "tenant_id": self.tenant,
                    "tin": TIN,
                    "display_name": "Synthetic 510000006",
                    "taxpayer_type": "company",
                }
            )  # fmt: skip
            .insert(ignore_permissions=True)
            .name
        )
        self.users["taxpayer"] = f"tax127-taxpayer-{run}@example.test"
        frappe.get_doc({"doctype": "User", "email": self.users["taxpayer"], "first_name": "tp",
                        "send_welcome_email": 0}).insert(ignore_permissions=True)  # fmt: skip
        grant_membership(self.users["taxpayer"], self.tenant, policy.TAXPAYER, self.taxpayer)
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "rsl_sources.json"
        path.write_text(json.dumps({
            "format": config.RSL_FIXTURE_FORMAT, "fixture": True, "synthetic": True,
            "description": "Synthetic TAX-127 claim; assumed shape.", "tenant_id": RSL_TENANT,
            "retrieved_at": "2026-11-12T08:00:00Z", "rates_assumed": {"vat": "0.15"},
            "materiality": {}, "records": {"online_refunds.claim": [CLAIM]},
        }))  # fmt: skip
        config._RSL_CACHE.clear()

        def sources(tenant_id: str) -> Any:
            conf = {config.RSL_SOURCES_KEY: {"fixture_path": str(path),
                                             "vati_tenant_id": self.tenant}}  # fmt: skip
            return config.rsl_sources(conf, vati_tenant_id=tenant_id)

        assurance_store.set_sources(sources)
        self.case_id = self.claim_case(CLAIM_REF)
        self.lane(self.case_id, MATCH)
        frappe.db.commit()

    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        assurance_store.set_sources()
        decisions_store.set_lifecycle_factory(None)
        refund_access.clear_cache()
        self.tmp.cleanup()
        frappe.db.rollback()

    # --- helpers ---------------------------------------------------------------------

    def claim_case(self, claim_ref: str | None, source: str = "rsl_online_refunds") -> str:
        values: dict[str, Any] = {
            "doctype": "Vati Submission", "tenant_id": self.tenant,
            "taxpayer_id": self.taxpayer, "submission_type": "refund_claim",
            "period": "2026-09", "idempotency_key": idem(), "synthetic": 1, "source": source,
        }  # fmt: skip
        if source == "rsl_online_refunds":
            values.update(source_ref=claim_ref, source_record_sha256=hexkey(),
                          source_contract="rsl.online_refunds.claim.v0-assumed",
                          source_refs="[]")  # fmt: skip
        sub = frappe.get_doc(values).insert(ignore_permissions=True)
        return str(mapping.register_case(tenant_id=self.tenant, idempotency_key=idem(),
                                         taxpayer_id=self.taxpayer,
                                         submission_id=sub.name)["case_id"])  # fmt: skip

    def lane(self, case_id: str, match: list[dict[str, Any]], revision: int = 1,
             supersedes: str | None = None) -> str:  # fmt: skip
        money = lambda a: {"amount": a, "currency": "LSL"}  # noqa: E731
        body = {
            "claim_ref": CLAIM_REF, "period": "2026-09", "lane": "standard",
            "policy_version": "tax114-v1", "reasons": [], "service_target":
            {"decision_due": "2026-11-30"}, "claimed_amount": money("750.00"),
            "undisputed_amount": money("450.00"), "undisputed_status": "recommendation_for_refund_auditor",
            "disputed_amount": money("300.00"), "disputed_items": [], "questions": [],
            "inputs": {}, "line_match": match, "authenticity_indicator": False,
            "requires_officer_review": True, "source_refs": [],
            # TAX-132: the RSL status the lane was proposed against.
            "rsl_claim_status": {"status": "audit_in_progress", "reported": True,
                                 "in_rsl_audit": True, "owner": "RSL"},
        }  # fmt: skip
        proposal_id = str(uuid.uuid4())
        frappe.get_doc({
            "doctype": "Vati Lane Proposal", "proposal_id": proposal_id,
            "tenant_id": self.tenant, "case_id": case_id, "claim_ref": CLAIM_REF,
            "period": "2026-09", "revision": revision, "supersedes_proposal_id": supersedes,
            "lane": "standard", "decision_due": "2026-11-30", "claimed_amount": "750.00",
            "undisputed_amount": "450.00", "disputed_amount": "300.00", "currency": "LSL",
            "body_json": json.dumps(body), "producer": "vati_check",
            "producer_version": "tax127-test", "input_digest": hexkey(),
            "created_at": frappe.utils.now_datetime(), "proposal_key": hexkey(),
            "revision_key": hexkey(), "request_digest": hexkey(),
        }).insert(ignore_permissions=True)  # fmt: skip
        return proposal_id

    def as_user(self, name: str) -> None:
        frappe.set_user(self.users[name])
        clear_principal_cache()
        refund_access.clear_cache()

    def admin(self) -> None:
        frappe.set_user("Administrator")
        clear_principal_cache()

    def propose(self, who: str = "officer", case_id: str | None = None) -> dict[str, Any]:
        self.as_user(who)
        try:
            return store.propose_gap_request(tenant_id=self.tenant,
                                             case_id=case_id or self.case_id)  # fmt: skip
        finally:
            self.admin()

    def approve(self, digest: str, who: str = "officer") -> dict[str, Any]:
        self.as_user(who)
        try:
            version = decisions_store.FrappeRepository().case_version(self.tenant, self.case_id)
            return store.approve_gap_request(
                tenant_id=self.tenant, case_id=self.case_id, proposal_digest=digest,
                expected_version=version,
                due_date=frappe.utils.add_days(frappe.utils.nowdate(), 14),
                message_to_taxpayer="Please answer the claim lines below.",
                idempotency_key=idem(), finding_ids=[str(uuid.uuid4())],
            )  # fmt: skip
        finally:
            self.admin()

    def portal(self) -> list[dict[str, Any]]:
        self.as_user("taxpayer")
        try:
            return responses_store.list_requests([(self.tenant, self.taxpayer)])
        finally:
            self.admin()

    # --- tests -----------------------------------------------------------------------

    def test_proposal_asks_only_for_the_gap_and_stores_nothing(self) -> None:
        proposal = self.propose()
        self.assertEqual([i["line"]["line_id"] for i in proposal["items"]], ["L2", "E2"])
        self.assertEqual({h["line_id"] for h in proposal["already_held"]}, {"L1", "L3", "E1"})
        self.assertTrue(proposal["source"]["fixture"])
        self.assertEqual(frappe.db.count(REQUEST, {"case_id": self.case_id}), 0)
        self.assertEqual(self.portal(), [])  # nothing reaches the taxpayer without approval

    def test_approved_request_reaches_the_taxpayer_with_lines_and_answers(self) -> None:
        proposal = self.propose()
        view = self.approve(proposal["proposal_digest"])
        self.assertEqual(view["counts"], {"lines": 5, "already_held": 3, "asked": 2})
        stored = frappe.get_doc(REQUEST, view["request_id"])
        self.assertEqual(stored.authorised_by, self.users["officer"])
        self.assertEqual(stored.gap_proposal_digest, proposal["proposal_digest"])
        requests = self.portal()
        self.assertEqual(len(requests), 1)
        shown = requests[0]
        self.assertEqual([i["line"]["line_id"] for i in shown["items"]], ["L2", "E2"])
        self.assertEqual(shown["items"][0]["answer_options"],
                         ["confirm", "dispute", "attach_proof"])  # fmt: skip
        self.assertEqual({h["line_id"] for h in shown["already_held"]}, {"L1", "L3", "E1"})
        text = json.dumps(shown).lower()
        for internal in ("finding", "score", "lane", "supplier_gap", "unmatched", "officer@",
                         self.case_id):  # fmt: skip
            self.assertNotIn(internal, text)
        # The taxpayer answers line by line.
        self.as_user("taxpayer")
        try:
            answered = responses_store.respond(
                [(self.tenant, self.taxpayer)], view["request_id"],
                {"idempotency_key": idem(), "items": [
                    {"item_index": 0, "answer": "confirm"},
                    {"item_index": 1, "answer": "dispute",
                     "unavailable_reason": "The import was cancelled before clearance."},
                ]},
            )  # fmt: skip
        finally:
            self.admin()
        self.assertEqual([i.get("answer") for i in answered["response"]["items"]],
                         ["confirm", "dispute"])  # fmt: skip

    def test_a_changed_line_match_needs_a_new_review(self) -> None:
        proposal = self.propose()
        current = frappe.get_all("Vati Lane Proposal", filters={"case_id": self.case_id},
                                 pluck="proposal_id")  # fmt: skip
        moved = [dict(m) for m in MATCH]
        moved[1].update(status="matched", treatment="accepted_as_evidence")
        self.lane(self.case_id, moved, revision=2, supersedes=current[0])
        frappe.db.commit()
        with self.assertRaises(store.ProposalChanged):
            self.approve(proposal["proposal_digest"])
        self.assertEqual(frappe.db.count(REQUEST, {"case_id": self.case_id}), 0)
        fresh = self.propose()
        self.assertEqual([i["line"]["line_id"] for i in fresh["items"]], ["E2"])

    def test_only_officers_propose_and_approve(self) -> None:
        digest = self.propose()["proposal_digest"]
        for who in ("taxpayer", "service"):
            with self.subTest(who=who):
                with self.assertRaises(frappe.PermissionError):
                    self.propose(who)
                with self.assertRaises(frappe.PermissionError):
                    self.approve(digest, who)
        # TAX-142: an auditor outside the refund team cannot see this refund claim.
        with self.assertRaises(frappe.PermissionError):
            self.propose("auditor")
        self.assertEqual(frappe.db.count(REQUEST, {"case_id": self.case_id}), 0)

    def test_cases_that_cannot_be_itemised(self) -> None:
        portal_case = self.claim_case(None, source="portal")
        frappe.db.commit()
        with self.assertRaises(store.ProposalUnavailable) as caught:
            self.propose(case_id=portal_case)
        self.assertEqual(caught.exception.field_errors[0]["code"], "not_an_online_refunds_claim")
        unmatched = self.claim_case(CLAIM_REF)
        frappe.db.commit()
        with self.assertRaises(store.ProposalUnavailable) as caught:
            self.propose(case_id=unmatched)
        self.assertEqual(caught.exception.field_errors[0]["code"], "line_match_missing")
