"""Frappe-free tests for Vati's outbound results to RSL (TAX-124, ADR-048).

Every message the rules build is checked against the TAX-124 outbound contracts in
integration/rsl_sinks and delivered to the fixture sink, exactly as the outbox handler does.
"""

from __future__ import annotations

import datetime
import importlib
import json
import pathlib
import sys
import tempfile
import unittest
from typing import Any

from vati_tax.rsl_outbound import rules

APP = pathlib.Path(__file__).resolve().parents[1]
REPO = APP.parents[2]
if str(REPO) not in sys.path:
    sys.path.append(str(REPO))
contracts: Any = None
fixture_sink: Any = None
try:
    contracts = importlib.import_module("integration.rsl_sinks.contracts")
    fixture_sink = importlib.import_module("integration.rsl_sinks.fixture_sink")
except ImportError:  # pragma: no cover - integration/ is not on the path
    pass

CASE_ID = "3f9a3c1e-7d2b-4c1a-9e8f-0a1b2c3d4e5f"
DECISION_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
REQUEST_ID = "dddddddd-eeee-4fff-8000-111111111111"
F1, F2 = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff", "bbbbbbbb-cccc-4ddd-8eee-000000000000"
ISSUED = datetime.datetime(2026, 11, 12, 8, 0, 0)
CASE = {"case_id": CASE_ID, "display_id": "VATI-2026-000042", "tin": "510000006"}
AUDIT = rules.RslReference(rules.AUDIT_SYSTEM, "PSRM-AUD-2026-0042")
CLAIM = rules.RslReference(rules.ONLINE_REFUNDS, "ORA-2026-000731")
DECISION = {
    "decision_id": DECISION_ID,
    "status": "approved",
    "outcome": "close_after_correction",
    "proposed_by": "auditor@example.test",
    "reviewed_by": "supervisor@example.test",
    "reviewer_role": "supervisor",
    "reviewed_at": datetime.datetime(2026, 11, 12, 7, 59, 0),
    "finding_ids": json.dumps([F1, F2]),
}


def finding(fid: str, **extra: Any) -> dict[str, Any]:
    return {
        "finding_id": fid,
        "revision": 1,
        "kind": "contradictory_evidence",
        "category": "refund_reconciliation",
        "code": "invoice_not_in_lekuka",
        "confidence": "high",
        "legal_route_status": "unverified",
        "explanation": "Two claimed invoices have no matching Lekuka record.",
        "unresolved_amount": "1500",
        "unresolved_currency": "LSL",
        "evidence_refs": json.dumps([{"evidence_id": "e1"}, {"evidence_id": "e2"}]),
        **extra,
    }


LANE = {
    "proposal_id": "cccccccc-dddd-4eee-8fff-000000000000",
    "revision": 2,
    "period": "2026-09",
    "lane": "standard",
    "claimed_amount": "15000",
    "undisputed_amount": "12000.5",
    "disputed_amount": "2999.50",
    "currency": "LSL",
    "decision_due": datetime.date(2026, 11, 30),
}
REQUEST = {
    "request_id": REQUEST_ID,
    "status": "recorded",
    "authorised_by": "auditor@example.test",
    "due_date": datetime.date(2026, 11, 26),
    "items": json.dumps(
        [
            {"document_type": "invoice", "description": "Invoice 17 with the supplier TIN"},
            {"document_type": "bank_statement", "description": "Proof of payment"},
            {"document_type": "invoice", "description": "Invoice 18"},
        ]
    ),
}


def pack(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "key": rules.idempotency_key(rules.FINDINGS_PACK, DECISION_ID),
        "reference": AUDIT,
        "case": CASE,
        "decision": DECISION,
        "findings": [finding(F2), finding(F1)],
        "issued_at": ISSUED,
        **overrides,
    }
    return rules.findings_pack(**args)


class ReferenceTests(unittest.TestCase):
    def test_audit_referral_first_then_online_refunds_claim(self) -> None:
        claim = {"source": "rsl_online_refunds", "source_ref": "ORA-2026-000731"}
        self.assertEqual(rules.rsl_reference(["PSRM-AUD-2"], claim), rules.RslReference(
            "audit_system", "PSRM-AUD-2"))  # fmt: skip
        self.assertEqual(rules.rsl_reference([], claim), CLAIM)

    def test_a_case_not_from_rsl_has_no_reference(self) -> None:
        self.assertIsNone(rules.rsl_reference([], None))
        self.assertIsNone(rules.rsl_reference([], {"source": "taxpayer", "source_ref": "X"}))
        self.assertIsNone(rules.rsl_reference([], {"source": "rsl_online_refunds"}))


class ItemTests(unittest.TestCase):
    def test_approved_decision_items(self) -> None:
        self.assertEqual(
            rules.items_for_decision(DECISION_ID, CLAIM, has_lane=True),
            [("findings_pack", DECISION_ID), ("lane_recommendation", DECISION_ID)],
        )
        self.assertEqual(
            rules.items_for_decision(DECISION_ID, CLAIM, has_lane=False),
            [("findings_pack", DECISION_ID)],
        )
        # A lane recommendation is for a claim; an audit referral gets the pack only.
        self.assertEqual(
            rules.items_for_decision(DECISION_ID, AUDIT, has_lane=True),
            [("findings_pack", DECISION_ID)],
        )

    def test_keys_are_stable_and_valid_outbox_keys(self) -> None:
        key = rules.idempotency_key(rules.FINDINGS_PACK, DECISION_ID)
        self.assertEqual(key, f"rsl-outbound:findings_pack:{DECISION_ID}")
        self.assertRegex(key, r"^[A-Za-z0-9:._-]{16,255}$")
        with self.assertRaises(ValueError):
            rules.idempotency_key("payment", DECISION_ID)
        self.assertEqual(
            rules.event_payload(rules.FINDINGS_PACK, DECISION_ID, AUDIT),
            {"kind": "findings_pack", "subject_id": DECISION_ID,
             "rsl_system": "audit_system", "rsl_record_id": "PSRM-AUD-2026-0042"},
        )  # fmt: skip


class ApprovalTests(unittest.TestCase):
    def test_only_independently_approved_decisions(self) -> None:
        for status in ("draft", "proposed", "rejected", "withdrawn"):
            with self.subTest(status=status):
                with self.assertRaises(rules.NotApproved) as caught:
                    pack(decision={**DECISION, "status": status})
                self.assertEqual(caught.exception.code, "decision_not_approved")
        for reviewer in (None, "", "auditor@example.test"):
            with self.assertRaises(rules.NotApproved) as caught:
                pack(decision={**DECISION, "reviewed_by": reviewer})
            self.assertEqual(caught.exception.code, "decision_not_independently_approved")

    def test_withdrawn_or_unauthorised_requests_are_not_sent(self) -> None:
        def notice(request: dict[str, Any]) -> dict[str, Any]:
            return rules.evidence_requested(
                key="rsl-outbound:evidence_requested:x", reference=CLAIM, case=CASE,
                request=request, base_url="https://vati.example", issued_at=ISSUED,
            )  # fmt: skip

        with self.assertRaises(rules.NotApproved) as caught:
            notice({**REQUEST, "status": "withdrawn"})
        self.assertEqual(caught.exception.code, "evidence_request_withdrawn")
        with self.assertRaises(rules.NotApproved):
            notice({**REQUEST, "authorised_by": None})

    def test_a_named_finding_that_is_missing_stops_the_pack(self) -> None:
        with self.assertRaises(rules.NotApproved) as caught:
            pack(findings=[finding(F1)])
        self.assertEqual(caught.exception.code, "finding_not_found")


@unittest.skipIf(contracts is None, "integration/ is not on the path")
class MessageTests(unittest.TestCase):
    def deliver(self, message: dict[str, Any]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as folder:
            sink = fixture_sink.FixtureRslSink(folder, "tenant-a")
            receipt: dict[str, Any] = sink.deliver(message, idempotency_key="k" * 16)
            return receipt

    def test_findings_pack_satisfies_the_contract(self) -> None:
        message = pack()
        self.assertEqual(contracts.validate_message(message), message)
        self.assertEqual(message["contract_id"], "rsl.audit_system.findings_pack.v0-assumed")
        self.assertEqual(message["authority"], "findings_for_rsl_review")
        self.assertEqual(message["route"], "wso2_api_manager")
        # The decision's order, decimal money, evidence counted not copied.
        self.assertEqual([f["finding_id"] for f in message["findings"]], [F1, F2])
        self.assertEqual(message["findings"][0]["unresolved_amount"], "1500.00")
        self.assertEqual(message["findings"][0]["evidence_count"], 2)
        self.assertEqual(message["decision"]["approved_at"], "2026-11-12T07:59:00Z")
        self.assertTrue(self.deliver(message)["fixture"])

    def test_rebuilding_gives_the_same_message(self) -> None:
        self.assertEqual(pack(), pack())
        self.assertNotEqual(
            pack()["message_id"],
            pack(key=rules.idempotency_key(rules.FINDINGS_PACK, REQUEST_ID))["message_id"],
        )

    def test_finding_without_amount_and_long_text(self) -> None:
        long = finding(F1, unresolved_amount="", explanation="x " * 3000)
        message = pack(decision={**DECISION, "finding_ids": json.dumps([F1])}, findings=[long])
        view = message["findings"][0]
        self.assertIsNone(view["unresolved_amount"])
        self.assertIsNone(view["currency"])
        self.assertLessEqual(len(view["explanation"]), rules.TEXT_MAX)
        contracts.validate_message(message)

    def test_lane_recommendation_satisfies_the_contract(self) -> None:
        message = rules.lane_recommendation(
            key=rules.idempotency_key(rules.LANE_RECOMMENDATION, DECISION_ID), reference=CLAIM,
            case=CASE, decision=DECISION, lane=LANE, issued_at=ISSUED,
        )  # fmt: skip
        self.assertEqual(contracts.validate_message(message), message)
        self.assertEqual(message["authority"], "recommendation_only")
        self.assertEqual(message["undisputed_amount"], "12000.50")
        self.assertEqual(message["claimed_amount"], "15000.00")
        self.assertEqual(message["decision_due"], "2026-11-30")
        self.assertFalse(set(message) & contracts.FORBIDDEN_FIELDS)
        self.assertTrue(self.deliver(message)["receipt_id"].startswith("FIXTURE-"))
        with self.assertRaises(rules.NotApproved):
            rules.lane_recommendation(
                key="rsl-outbound:lane_recommendation:x", reference=AUDIT, case=CASE,
                decision=DECISION, lane=LANE, issued_at=ISSUED,
            )  # fmt: skip

    def test_evidence_notice_satisfies_the_contract_and_carries_no_text(self) -> None:
        for reference in (CLAIM, AUDIT):
            message = rules.evidence_requested(
                key=rules.idempotency_key(rules.EVIDENCE_REQUESTED, REQUEST_ID),
                reference=reference, case=CASE, request=REQUEST,
                base_url="http://taxathon.localhost:58000/", issued_at=ISSUED,
            )  # fmt: skip
            self.assertEqual(contracts.validate_message(message), message)
            self.assertEqual(message["authority"], "notice_only")
            self.assertEqual(message["item_count"], 3)
            self.assertEqual(message["document_types"], ["bank_statement", "invoice"])
            self.assertEqual(
                message["portal_link"],
                f"http://taxathon.localhost:58000/taxpayer/requests/{REQUEST_ID}",
            )
            self.assertNotIn("Proof of payment", json.dumps(message))


class ViewTests(unittest.TestCase):
    def event(self, status: str, **extra: Any) -> dict[str, Any]:
        payload = rules.event_payload(rules.FINDINGS_PACK, DECISION_ID, AUDIT)
        return {"event_id": "e1", "status": status, "attempts": 3,
                "payload_json": json.dumps(payload), **extra}  # fmt: skip

    def test_states(self) -> None:
        queued = rules.delivery_view(self.event("retry", last_error_code="rsl_sink_unavailable"),
                                     None)  # fmt: skip
        self.assertEqual(queued["state"], "queued")
        self.assertFalse(queued["delivered"])
        self.assertEqual(queued["last_error_code"], "rsl_sink_unavailable")
        self.assertIsNone(queued["receipt_id"])
        dead = rules.delivery_view(self.event("dead_letter"), None)
        self.assertEqual(dead["state"], "not_delivered")
        done = rules.delivery_view(self.event("delivered"), "FIXTURE-ABC")
        self.assertEqual(done["state"], "delivered")
        self.assertEqual(done["receipt_id"], "FIXTURE-ABC")
        self.assertTrue(done["fixture_receipt"])
        self.assertEqual(done["rsl_reference"], AUDIT.as_dict())

    def test_money(self) -> None:
        self.assertEqual(rules.money("0"), "0.00")
        self.assertEqual(rules.money("12.345"), "12.35")
        with self.assertRaises(ValueError):
            rules.money("-1")
        with self.assertRaises(ValueError):
            rules.money("abc")


if __name__ == "__main__":
    unittest.main()
