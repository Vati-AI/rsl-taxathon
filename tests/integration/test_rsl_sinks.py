"""TAX-124 outbound contracts to RSL and the fixture sink (no network, synthetic data)."""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.rsl_sinks.contracts import (  # noqa: E402
    ASSUMED_SHAPE_NOTICE,
    AUTHORITY,
    CONTRACTS,
    FORBIDDEN_FIELDS,
    ROUTE,
    OutboundContractError,
    validate_message,
)
from integration.rsl_sinks.fixture_sink import (  # noqa: E402
    FixtureRslSink,
    SinkConflict,
    SinkUnavailable,
)

CASE = "3f9a3c1e-7d2b-4c1a-9e8f-0a1b2c3d4e5f"


def envelope(system: str, kind: str) -> dict:
    return {
        "message_id": "11111111-2222-4333-8444-555555555555",
        "contract_id": f"rsl.{system}.{kind}.v0-assumed",
        "kind": kind,
        "authority": AUTHORITY[kind],
        "route": ROUTE,
        "notice": ASSUMED_SHAPE_NOTICE,
        "rsl_reference": {"system": system, "record_id": "PSRM-AUD-2026-0042"},
        "tin": "510000006",
        "vati_case_id": CASE,
        "vati_case_ref": "VATI-2026-000042",
        "issued_at": "2026-11-12T08:00:00Z",
    }


PACK = {
    **envelope("audit_system", "findings_pack"),
    "decision": {
        "decision_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        "outcome": "close_after_correction",
        "approved_at": "2026-11-12T07:59:00Z",
        "approver_role": "supervisor",
    },
    "findings": [
        {
            "finding_id": "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
            "revision": 1,
            "kind": "contradictory_evidence",
            "category": "refund_reconciliation",
            "code": "invoice_not_in_lekuka",
            "confidence": "high",
            "legal_route_status": "unverified",
            "explanation": "Two claimed invoices have no matching Lekuka record.",
            "unresolved_amount": "1500.00",
            "currency": "LSL",
            "evidence_count": 2,
        }
    ],
}
LANE = {
    **envelope("online_refunds", "lane_recommendation"),
    "period": "2026-09",
    "lane": "standard",
    "claimed_amount": "15000.00",
    "undisputed_amount": "12000.00",
    "disputed_amount": "3000.00",
    "currency": "LSL",
    "decision_due": "2026-11-30",
    "proposal_id": "cccccccc-dddd-4eee-8fff-000000000000",
    "proposal_revision": 2,
    "approved_decision_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
}
NOTICE = {
    **envelope("online_refunds", "evidence_requested"),
    "request_id": "dddddddd-eeee-4fff-8000-111111111111",
    "due_date": "2026-11-26",
    "item_count": 2,
    "document_types": ["bank_statement", "invoice"],
    "portal_link": "https://vati.example/taxpayer/requests/dddddddd-eeee-4fff-8000-111111111111",
}


class ContractTests(unittest.TestCase):
    def test_every_contract_is_versioned_assumed_and_routed_through_wso2(self) -> None:
        self.assertEqual(len(CONTRACTS), 5)
        for (system, kind), contract in CONTRACTS.items():
            self.assertEqual(contract.contract_id, f"rsl.{system}.{kind}.v0-assumed")
        for message in (PACK, LANE, NOTICE):
            self.assertEqual(validate_message(message), message)
            self.assertEqual(message["route"], "wso2_api_manager")

    def test_no_contract_can_set_status_pay_offset_or_assess(self) -> None:
        def names(rules: object) -> set[str]:
            if isinstance(rules, dict):
                return set(rules) | {n for v in rules.values() for n in names(v)}
            if isinstance(rules, list):
                return {n for v in rules for n in names(v)}
            return set()

        for contract in CONTRACTS.values():
            self.assertFalse(names(dict(contract.fields)) & FORBIDDEN_FIELDS)
        for field in ("status", "payment_amount", "offset_amount", "assessed_amount"):
            with self.subTest(field=field):
                with self.assertRaises(OutboundContractError) as caught:
                    validate_message({**LANE, field: "1.00"})
                self.assertEqual(caught.exception.code, "forbidden_field")
        nested = copy.deepcopy(PACK)
        nested["decision"]["rsl_status"] = "processed"
        with self.assertRaises(OutboundContractError) as caught:
            validate_message(nested)
        self.assertEqual(caught.exception.code, "forbidden_field")

    def test_authority_states_what_the_message_is(self) -> None:
        for message in (PACK, LANE, NOTICE):
            with self.assertRaises(OutboundContractError):
                validate_message({**message, "authority": "instruction"})

    def test_lane_recommendation_goes_only_to_online_refunds(self) -> None:
        wrong = {**LANE, "rsl_reference": {"system": "audit_system", "record_id": "X1"}}
        with self.assertRaises(OutboundContractError) as caught:
            validate_message(wrong)
        self.assertEqual(caught.exception.code, "unknown_contract")

    def test_refusals(self) -> None:
        cases = {
            "unexpected_field": {**NOTICE, "message_to_taxpayer": "Please send"},
            "float_value": {**LANE, "undisputed_amount": 12000.0},
            "invalid_field": {**LANE, "undisputed_amount": "12000.001"},
            "missing_field": {k: v for k, v in NOTICE.items() if k != "portal_link"},
        }
        for code, message in cases.items():
            with self.subTest(code=code):
                with self.assertRaises(OutboundContractError) as caught:
                    validate_message(message)
                self.assertEqual(caught.exception.code, code)
        for link in ("javascript:alert(1)", "https://vati.example/desk/app/x"):
            with self.assertRaises(OutboundContractError):
                validate_message({**NOTICE, "portal_link": link})
        mismatch = {**PACK, "contract_id": "rsl.online_refunds.findings_pack.v0-assumed"}
        with self.assertRaises(OutboundContractError):
            validate_message(mismatch)


class FixtureSinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.sink = FixtureRslSink(self.dir.name, "tenant-a")

    def test_delivery_is_a_fixture_receipt_and_idempotent(self) -> None:
        first = self.sink.deliver(PACK, idempotency_key="rsl-outbound:findings_pack:1")
        self.assertTrue(first["fixture"])
        self.assertTrue(first["receipt_id"].startswith("FIXTURE-"))
        self.assertEqual(first["route"], "wso2_api_manager")
        self.assertFalse(first["replayed"])
        again = self.sink.deliver(PACK, idempotency_key="rsl-outbound:findings_pack:1")
        self.assertTrue(again["replayed"])
        self.assertEqual(again["receipt_id"], first["receipt_id"])
        self.assertEqual(self.sink.received("audit_system"), [PACK])
        self.assertEqual(
            self.sink.lookup("audit_system", "rsl-outbound:findings_pack:1")["receipt_id"],
            first["receipt_id"],
        )
        self.assertIsNone(self.sink.lookup("audit_system", "rsl-outbound:other"))

    def test_same_key_different_message_is_refused(self) -> None:
        self.sink.deliver(LANE, idempotency_key="rsl-outbound:lane_recommendation:1")
        changed = {**LANE, "undisputed_amount": "1.00"}
        with self.assertRaises(SinkConflict):
            self.sink.deliver(changed, idempotency_key="rsl-outbound:lane_recommendation:1")
        self.assertEqual(self.sink.received("online_refunds"), [LANE])

    def test_unavailable_system_records_nothing(self) -> None:
        down = FixtureRslSink(self.dir.name, "tenant-a", unavailable=["online_refunds"])
        with self.assertRaises(SinkUnavailable) as caught:
            down.deliver(NOTICE, idempotency_key="rsl-outbound:evidence_requested:1")
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(down.received("online_refunds"), [])
        # The audit system is still up.
        down.deliver(PACK, idempotency_key="rsl-outbound:findings_pack:2")
        self.assertEqual(len(down.received("audit_system")), 1)

    def test_invalid_message_never_lands(self) -> None:
        with self.assertRaises(OutboundContractError):
            self.sink.deliver({**NOTICE, "status": "pending"}, idempotency_key="k" * 16)
        self.assertEqual(self.sink.received("online_refunds"), [])

    def test_tenants_are_separate(self) -> None:
        self.sink.deliver(PACK, idempotency_key="rsl-outbound:findings_pack:1")
        other = FixtureRslSink(self.dir.name, "tenant-b")
        self.assertEqual(other.received("audit_system"), [])
        self.assertIsNone(other.lookup("audit_system", "rsl-outbound:findings_pack:1"))

    def test_unknown_unavailable_system_is_a_configuration_error(self) -> None:
        with self.assertRaises(ValueError):
            FixtureRslSink(self.dir.name, "tenant-a", unavailable=["psrm_ledger"])


if __name__ == "__main__":
    unittest.main()
