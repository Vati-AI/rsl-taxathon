"""Bench tests for Vati's outbound results to RSL (TAX-124, ADR-048).

Run on a disposable site: ``python3 scripts/frappe_dev.py verify``. Officers propose and
approve through the real TAX-046 decision service (Jube replaced with the in-process
``FakeLifecycle``); referral cases come from the TAX-123 intake reading the synthetic
referral fixture; the outbound sink is the real ``config.rsl_sinks`` factory writing to a
temporary directory; the TAX-033 outbox worker delivers.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import tempfile
import unittest
import uuid
from datetime import UTC
from pathlib import Path
from typing import Any

try:
    import frappe
    from frappe.tests import IntegrationTestCase

    from vati_tax.decisions import store as decisions
    from vati_tax.decisions.test_engine import FakeCase, FakeLifecycle
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.ids import mapping
    from vati_tax.ids.canonical import CaseNotFound
    from vati_tax.integrations import config
    from vati_tax.outbox import policy as outbox_policy
    from vati_tax.outbox import store as outbox_store
    from vati_tax.outbox import worker
    from vati_tax.pipeline import store as pipeline_store
    from vati_tax.rsl_outbound import api, rules, store
    from vati_tax.rsl_referrals import store as referrals
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

TIN = "510000006"
CLAIM_REF = "ORA-2026-000731"
FAST = outbox_policy.RetryPolicy(
    max_attempts=20,
    base_delay=datetime.timedelta(seconds=1),
    max_delay=datetime.timedelta(seconds=2),
)


def hexkey() -> str:
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()


def idem() -> str:
    return "synthetic-" + uuid.uuid4().hex


class RslOutboundBenchTests(IntegrationTestCase):
    def setUp(self) -> None:
        frappe.set_user("Administrator")
        clear_principal_cache()
        self.run_id = uuid.uuid4().hex[:8]
        self.tenant = ensure_tenant(str(uuid.uuid4()), "Synthetic TAX-124 tenant")
        self.other = ensure_tenant(str(uuid.uuid4()), "Synthetic TAX-124 other tenant")
        self.service = ensure_service_identity(f"tax124-svc-{self.run_id}@example.test",
                                               [self.tenant])  # fmt: skip
        self.users: dict[str, str] = {}
        staff: list[tuple[str, str, policy.Role]] = [
            ("auditor", self.tenant, policy.AUDITOR),
            ("supervisor", self.tenant, policy.SUPERVISOR),
            ("auditor-other", self.other, policy.AUDITOR),
        ]
        for name, tenant, role in staff:
            user = f"tax124-{name}-{self.run_id}@example.test"
            frappe.get_doc({"doctype": "User", "email": user, "first_name": name,
                            "send_welcome_email": 0}).insert(ignore_permissions=True)  # fmt: skip
            grant_membership(user, tenant, role)
            self.users[name] = user
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
        frappe.db.commit()
        pipeline_store.set_services(service_user=self.service)
        self.jube = FakeLifecycle()
        decisions.set_lifecycle_factory(lambda _tenant: self.jube)
        self.tmp = tempfile.TemporaryDirectory()
        self.sink_dir = str(Path(self.tmp.name) / "sinks")
        self.unavailable: list[str] = []

        def sinks(tenant_id: str) -> Any:
            conf = {config.RSL_SINKS_KEY: {"directory": self.sink_dir,
                                           "vati_tenant_id": self.tenant,
                                           "unavailable": self.unavailable}}  # fmt: skip
            return config.rsl_sinks(conf, vati_tenant_id=tenant_id)

        store.set_sinks(sinks)
        doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.sources_path = str(Path(self.tmp.name) / "rsl_sources.json")
        Path(self.sources_path).write_text(json.dumps(doc))
        config._RSL_CACHE.clear()

        def sources(tenant_id: str) -> Any:
            conf = {config.RSL_SOURCES_KEY: {"fixture_path": self.sources_path,
                                             "vati_tenant_id": self.tenant}}  # fmt: skip
            return config.rsl_sources(conf, vati_tenant_id=tenant_id)

        referrals.set_sources(sources)

    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        store.set_sinks(None)
        referrals.set_sources(None)
        decisions.set_lifecycle_factory(None)
        pipeline_store.set_services()
        self.tmp.cleanup()
        frappe.db.rollback()

    # --- helpers ---------------------------------------------------------------------

    def as_user(self, name: str) -> None:
        frappe.set_user(self.users[name])
        clear_principal_cache()

    def admin(self) -> None:
        frappe.set_user("Administrator")
        clear_principal_cache()

    def referral_case(self) -> tuple[str, str]:
        """A case opened by the TAX-123 intake from the synthetic referral fixture."""
        self.admin()
        summary = referrals.ingest_audit_referrals(self.tenant)
        item = next(r for r in summary["referrals"] if r["taxpayer_id"] == self.taxpayer)
        frappe.db.commit()
        return item["case_id"], item["audit_case_ref"]

    def claim_case(self, lane: bool = True) -> str:
        """A case for a claim read from the Online Refunds Application (TAX-122 shape)."""
        self.admin()
        sub = frappe.get_doc({
            "doctype": "Vati Submission", "tenant_id": self.tenant, "taxpayer_id": self.taxpayer,
            "submission_type": "refund_claim", "period": "2026-09",
            "source": rules.CLAIM_SOURCE, "source_ref": CLAIM_REF,
            "source_contract": "rsl.online_refunds.claim.v0-assumed",
            "source_record_sha256": hexkey(), "source_refs": "[]",
            "idempotency_key": idem(), "synthetic": 1,
        }).insert(ignore_permissions=True)  # fmt: skip
        case_id = mapping.register_case(tenant_id=self.tenant, idempotency_key=idem(),
                                        taxpayer_id=self.taxpayer,
                                        submission_id=sub.name)["case_id"]  # fmt: skip
        if lane:
            self.lane(case_id)
        frappe.db.commit()
        return case_id

    def lane(self, case_id: str) -> str:
        money = lambda amount: {"amount": amount, "currency": "LSL"}  # noqa: E731
        body = {
            "claim_ref": CLAIM_REF, "period": "2026-09", "lane": "standard",
            "policy_version": "tax114-v1", "reasons": ["Two invoices need corroboration."],
            "service_target": {"decision_due": "2026-11-30",
                               "decision_target_working_days": 15},
            "claimed_amount": money("15000.00"), "undisputed_amount": money("12000.00"),
            "undisputed_status": "recommended", "disputed_amount": money("3000.00"),
            "disputed_items": [], "questions": [], "inputs": {}, "line_match": [],
            "authenticity_indicator": False, "requires_officer_review": True, "source_refs": [],
        }  # fmt: skip
        proposal_id = str(uuid.uuid4())
        frappe.get_doc({
            "doctype": "Vati Lane Proposal", "proposal_id": proposal_id,
            "tenant_id": self.tenant, "case_id": case_id, "claim_ref": CLAIM_REF,
            "period": "2026-09", "revision": 1, "lane": "standard",
            "decision_due": "2026-11-30", "decision_target_working_days": 15,
            "claimed_amount": "15000.00", "undisputed_amount": "12000.00",
            "disputed_amount": "3000.00", "currency": "LSL", "body_json": json.dumps(body),
            "producer": "vati_check", "producer_version": "tax124-test", "input_digest": hexkey(),
            "created_at": frappe.utils.now_datetime(), "proposal_key": hexkey(),
            "revision_key": hexkey(), "request_digest": hexkey(),
        }).insert(ignore_permissions=True)  # fmt: skip
        return proposal_id

    def finding(self, case_id: str) -> str:
        finding_id = str(uuid.uuid4())
        frappe.get_doc({
            "doctype": "Vati Case Finding", "finding_id": finding_id, "tenant_id": self.tenant,
            "case_id": case_id, "revision": 1, "kind": "contradictory_evidence",
            "category": "refund_reconciliation", "code": "invoice_not_in_lekuka",
            "explanation": "Two claimed invoices have no matching Lekuka record.",
            "evidence_refs": "[]", "unresolved_amount": "1500.00", "unresolved_currency": "LSL",
            "confidence": "high", "alternative_explanations": "[]",
            "legal_route_status": "unverified", "proposed_action": "none", "producer": "officer",
            "producer_version": "officer-manual-v1", "recorded_by": self.users["auditor"],
            "recorder_role": "auditor", "created_at": frappe.utils.now_datetime(),
            "lineage_key": hexkey(), "revision_key": hexkey(), "registration_key": hexkey(),
            "request_digest": hexkey(),
        }).insert(ignore_permissions=True)  # fmt: skip
        return finding_id

    def version(self, case_id: str) -> int:
        return decisions.FrappeRepository().case_version(self.tenant, case_id)

    def propose(self, case_id: str, finding_ids: list[str]) -> str:
        # A closing decision cites at least one finding (TAX-046).
        finding_ids = finding_ids or [self.finding(case_id)]
        self.jube.cases.setdefault(case_id, FakeCase("AWAITING_HUMAN"))
        self.as_user("auditor")
        view = decisions.propose_decision(
            tenant_id=self.tenant, case_id=case_id, idempotency_key=idem(),
            body={"outcome": "close_after_correction",
                  "rationale": "The two unmatched invoices were withdrawn from the claim.",
                  "rating": 2, "expected_case_status": "AWAITING_HUMAN",
                  "expected_version": self.version(case_id), "finding_ids": finding_ids},
        )  # fmt: skip
        frappe.db.commit()
        return str(view["decision_id"])

    def approve(self, decision_id: str, case_id: str, action: str = "approve") -> None:
        self.as_user("supervisor")
        decisions.review_decision(
            tenant_id=self.tenant, decision_id=decision_id, action=action,
            body={"expected_version": self.version(case_id),
                  **({"reason": "Rationale does not cover the import entries."}
                     if action == "reject" else {})},
            idempotency_key=idem(),
        )  # fmt: skip
        frappe.db.commit()

    def request(self, case_id: str) -> str:
        self.as_user("auditor")
        view = decisions.authorise_evidence_request(
            tenant_id=self.tenant, case_id=case_id, idempotency_key=idem(),
            body={"expected_version": self.version(case_id), "finding_ids": [str(uuid.uuid4())],
                  "items": [{"document_type": "invoice", "description": "Supplier invoices."}],
                  "due_date": frappe.utils.add_days(frappe.utils.nowdate(), 14),
                  "message_to_taxpayer": "Please upload the supplier invoices."},
        )  # fmt: skip
        frappe.db.commit()
        return str(view["request_id"])

    def drain(self, at: datetime.datetime | None = None) -> dict[str, int]:
        self.admin()
        moment = at or datetime.datetime.now(UTC)
        totals: dict[str, int] = {}
        for _ in range(10):
            counts = worker.dispatch(limit=50, tenant_id=self.tenant, now=moment, retry=FAST,
                                     rand=lambda: 1.0)  # fmt: skip
            if not counts:
                break
            for k, v in counts.items():
                totals[k] = totals.get(k, 0) + v
        return totals

    def outbound(self, case_id: str) -> list[dict[str, Any]]:
        self.as_user("auditor")
        try:
            return store.case_outbound(tenant_id=self.tenant, case_id=case_id)
        finally:
            self.admin()

    def received(self, system: str) -> list[dict[str, Any]]:
        sink: Any = store.sink_for(self.tenant)
        result: list[dict[str, Any]] = sink.received(system)
        return result

    def ledger_actions(self, case_id: str) -> list[str]:
        return frappe.get_all(
            "Vati Audit Event",
            filters={"tenant_id": self.tenant, "case_id": case_id,
                     "action": ["like", "rsl_outbound.%"]},
            order_by="chain_seq asc", pluck="action",
        )  # fmt: skip

    # --- tests -----------------------------------------------------------------------

    def test_approved_decision_on_a_referral_case_sends_one_findings_pack(self) -> None:
        case_id, audit_ref = self.referral_case()
        finding_id = self.finding(case_id)
        decision_id = self.propose(case_id, [finding_id])
        self.admin()
        # Proposed is not approved: nothing is queued.
        self.assertEqual(self.outbound(case_id), [])
        self.approve(decision_id, case_id)
        items = self.outbound(case_id)
        self.assertEqual([(i["kind"], i["state"]) for i in items], [("findings_pack", "queued")])
        self.assertEqual(items[0]["rsl_reference"],
                         {"system": "audit_system", "record_id": audit_ref})  # fmt: skip
        self.assertEqual(self.drain().get("delivered"), 1)
        items = self.outbound(case_id)
        self.assertEqual(items[0]["state"], "delivered")
        self.assertTrue(items[0]["receipt_id"].startswith("FIXTURE-"))
        self.assertTrue(items[0]["fixture_receipt"])
        packs = self.received("audit_system")
        self.assertEqual(len(packs), 1)
        pack = packs[0]
        self.assertEqual(pack["contract_id"], "rsl.audit_system.findings_pack.v0-assumed")
        self.assertEqual(pack["rsl_reference"]["record_id"], audit_ref)
        self.assertEqual(pack["tin"], TIN)
        self.assertEqual(pack["decision"]["decision_id"], decision_id)
        self.assertEqual(pack["decision"]["approver_role"], "supervisor")
        self.assertEqual([f["finding_id"] for f in pack["findings"]], [finding_id])
        self.assertEqual(self.ledger_actions(case_id),
                         ["rsl_outbound.findings_pack.queued",
                          "rsl_outbound.findings_pack.delivered"])  # fmt: skip
        # Draining again sends nothing more; the referral case has no lane recommendation.
        self.assertEqual(self.drain(), {})
        self.assertEqual(len(self.received("audit_system")), 1)
        self.assertEqual(self.received("online_refunds"), [])

    def test_claim_case_sends_pack_and_lane_recommendation_and_never_acts(self) -> None:
        case_id = self.claim_case()
        decision_id = self.propose(case_id, [])
        self.approve(decision_id, case_id)
        self.assertEqual(self.drain().get("delivered"), 2)
        kinds = sorted(m["kind"] for m in self.received("online_refunds"))
        self.assertEqual(kinds, ["findings_pack", "lane_recommendation"])
        lane = next(m for m in self.received("online_refunds")
                    if m["kind"] == "lane_recommendation")  # fmt: skip
        self.assertEqual(lane["authority"], "recommendation_only")
        self.assertEqual(lane["rsl_reference"], {"system": "online_refunds",
                                                 "record_id": CLAIM_REF})  # fmt: skip
        self.assertEqual((lane["lane"], lane["undisputed_amount"]), ("standard", "12000.00"))
        self.assertEqual(lane["approved_decision_id"], decision_id)
        for forbidden in ("status", "payment_amount", "offset_amount", "assessed_amount"):
            self.assertNotIn(forbidden, lane)
        # Vati changed nothing on its own claim record either.
        submission = frappe.db.get_value("Vati Case Identity", case_id, "submission_id")
        self.assertEqual(frappe.db.get_value("Vati Submission", submission, "status"), "draft")

    def test_rejected_decision_and_a_case_not_from_rsl_send_nothing(self) -> None:
        case_id = self.claim_case()
        decision_id = self.propose(case_id, [])
        self.approve(decision_id, case_id, action="reject")
        self.admin()
        plain = mapping.register_case(tenant_id=self.tenant, idempotency_key=idem(),
                                      taxpayer_id=self.taxpayer)["case_id"]  # fmt: skip
        frappe.db.commit()
        other = self.propose(plain, [])
        self.approve(other, plain)
        self.drain()
        self.assertEqual(self.outbound(case_id), [])
        self.assertEqual(self.outbound(plain), [])
        self.assertEqual(self.received("online_refunds"), [])
        self.assertEqual(self.received("audit_system"), [])

    def test_evidence_request_notice_carries_a_portal_link_once(self) -> None:
        case_id = self.claim_case(lane=False)
        request_id = self.request(case_id)
        self.assertEqual([i["kind"] for i in self.outbound(case_id)], ["evidence_requested"])
        self.drain()
        notices = self.received("online_refunds")
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["request_id"], request_id)
        self.assertTrue(notices[0]["portal_link"].endswith(f"/taxpayer/requests/{request_id}"))
        self.assertNotIn("Please upload", json.dumps(notices[0]))
        self.assertEqual(self.ledger_actions(case_id),
                         ["rsl_outbound.evidence_requested.queued",
                          "rsl_outbound.evidence_requested.delivered"])  # fmt: skip

    def test_unavailable_sink_keeps_the_item_queued_and_visible_as_not_delivered(self) -> None:
        case_id, _ref = self.referral_case()
        decision_id = self.propose(case_id, [])
        self.approve(decision_id, case_id)
        self.unavailable.append("audit_system")
        self.drain()
        item = self.outbound(case_id)[0]
        self.assertEqual(item["state"], "queued")
        self.assertFalse(item["delivered"])
        self.assertEqual(item["last_error_code"], "rsl_sink_unavailable")
        self.assertIsNone(item["receipt_id"])
        self.assertEqual(self.received("audit_system"), [])
        self.assertEqual(self.ledger_actions(case_id), ["rsl_outbound.findings_pack.queued"])
        # RSL answers again: the same item goes once.
        self.unavailable.clear()
        later = datetime.datetime.now(UTC) + datetime.timedelta(minutes=5)
        self.assertEqual(self.drain(later).get("delivered"), 1)
        self.assertEqual(self.outbound(case_id)[0]["state"], "delivered")
        self.assertEqual(len(self.received("audit_system")), 1)

    def test_exhausted_budget_is_not_delivered_until_an_operator_retries(self) -> None:
        case_id, _ref = self.referral_case()
        decision_id = self.propose(case_id, [])
        self.approve(decision_id, case_id)
        self.admin()
        frappe.db.set_value(outbox_store.EVENT, {"case_id": case_id}, "max_attempts", 1)
        frappe.db.commit()
        self.unavailable.append("audit_system")
        self.drain()
        item = self.outbound(case_id)[0]
        self.assertEqual(item["state"], "not_delivered")
        self.unavailable.clear()
        outbox_store.retry_dead_letter(tenant_id=self.tenant, event_id=item["event_id"],
                                       actor="Administrator")  # fmt: skip
        frappe.db.commit()
        self.drain()
        self.assertEqual(self.outbound(case_id)[0]["state"], "delivered")

    def test_withdrawn_request_is_never_sent(self) -> None:
        case_id = self.claim_case(lane=False)
        request_id = self.request(case_id)
        self.admin()
        frappe.db.set_value("Vati Evidence Request", request_id, "status", "withdrawn")
        frappe.db.commit()
        self.drain()
        item = self.outbound(case_id)[0]
        self.assertEqual(item["state"], "not_delivered")
        self.assertEqual(item["last_error_code"], "evidence_request_withdrawn")
        self.assertEqual(self.received("online_refunds"), [])

    def test_replayed_approval_queues_nothing_twice(self) -> None:
        case_id, _ref = self.referral_case()
        decision_id = self.propose(case_id, [])
        self.approve(decision_id, case_id)
        self.admin()
        doc = frappe.get_doc("Vati Decision", decision_id)
        store.on_decision_update(doc)  # as if the hook ran again
        frappe.db.commit()
        self.assertEqual(len(self.outbound(case_id)), 1)
        self.assertEqual(self.ledger_actions(case_id), ["rsl_outbound.findings_pack.queued"])

    def test_officer_view_is_tenant_scoped_and_staff_only(self) -> None:
        case_id, _ref = self.referral_case()
        self.as_user("auditor-other")
        with self.assertRaises((CaseNotFound, frappe.PermissionError)):
            store.case_outbound(tenant_id=self.tenant, case_id=case_id)
        with self.assertRaises(CaseNotFound):
            store.case_outbound(tenant_id=self.other, case_id=case_id)
        self.admin()
        self.assertTrue(callable(api.get_case_outbound))
