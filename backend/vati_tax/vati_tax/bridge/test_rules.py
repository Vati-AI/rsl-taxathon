"""Frappe-free tests for the Frappe to Jube bridge rules (TAX-039).

Covers the task's verification list at the rule level: webhook replay, stale
updates, the synchronisation-loop guard and the taxpayer-safe status. The bench
tests (test_bridge_bench.py) cover the same through Frappe.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from vati_tax.bridge import rules
from vati_tax.bridge.rules import Mirror, Projection, StaleUpdate
from vati_tax.ids.canonical import IdempotencyConflict, InvalidMapping, MappingConflict

TENANT = "10000000-0000-4000-8000-000000000001"
CASE = "60000000-0000-4000-8000-000000000001"
SUBMISSION = "50000000-0000-4000-8000-000000000001"
T0 = datetime(2026, 11, 5, 3, 0, tzinfo=UTC)


def envelope(**payload_overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jube_case_id": "42",
        "status": "INVESTIGATING",
        "escalated": False,
        "closed": False,
        "observed_at": "2026-11-05T03:00:00Z",
    }
    payload.update(payload_overrides)
    return {
        "schema_version": "1",
        "event_id": str(uuid.uuid4()),
        "tenant_id": TENANT,
        "case_id": CASE,
        "correlation_id": str(uuid.uuid4()),
        "type": "case.status_projected",
        "occurred_at": "2026-11-05T03:00:01Z",
        "idempotency_key": "jube:case:42:" + uuid.uuid4().hex,
        "source_service": "jube",
        "payload": payload,
    }


def seen(status: str = "INVESTIGATING", at: datetime = T0, **kw: Any) -> rules.Observation:
    view = Projection(kw.get("jube_case_id", "42"), status, kw.get("escalated", False),
                      kw.get("closed", False))  # fmt: skip
    return rules.observe(TENANT, CASE, view, at)


def stored(status: str = "INVESTIGATING", at: datetime = T0, **kw: Any) -> Mirror:
    view = Projection(kw.get("jube_case_id", "42"), status, kw.get("escalated", False),
                      kw.get("closed", False))  # fmt: skip
    return Mirror(view.jube_case_id, view, at)


class TaxpayerStatusTests(unittest.TestCase):
    def test_every_case_status_maps_to_one_of_three_taxpayer_words(self):
        self.assertEqual(set(rules.REVIEW_STATUS), set(rules.CASE_STATUSES))
        self.assertTrue(set(rules.REVIEW_STATUS.values()) <= set(rules.REVIEW_STATUSES))
        self.assertEqual(rules.REVIEW_STATUS["AWAITING_EVIDENCE"], "evidence_requested")
        self.assertEqual(rules.REVIEW_STATUS["RESOLVED"], "completed")

    def test_internal_states_are_indistinguishable_to_the_taxpayer(self):
        # Escalation, failed processing and validation all read "under review".
        internal = [
            "RECEIVED",
            "VALIDATING",
            "INVESTIGATING",
            "AWAITING_HUMAN",
            "FAILED_PROCESSING",
        ]
        self.assertEqual({rules.REVIEW_STATUS[s] for s in internal}, {"under_review"})
        escalated = Projection("42", "AWAITING_HUMAN", True, False)
        plain = Projection("42", "AWAITING_HUMAN", False, False)
        self.assertEqual(escalated.review_status, plain.review_status)

    def test_frappe_owned_statuses_win_before_and_after_review(self):
        self.assertEqual(rules.taxpayer_status("draft", "completed"), "draft")
        self.assertEqual(rules.taxpayer_status("correction_needed", None), "correction_needed")
        self.assertEqual(rules.taxpayer_status("submitted", None), "under_review")
        self.assertEqual(rules.taxpayer_status("submitted", "AWAITING_HUMAN"), "under_review")
        self.assertEqual(
            rules.taxpayer_status("submitted", "evidence_requested"), "evidence_requested"
        )

    def test_taxpayer_fields_are_only_the_review_status(self):
        self.assertEqual(rules.TAXPAYER_FIELDS, {"review_status", "review_status_at"})


class EnvelopeTests(unittest.TestCase):
    def test_valid_envelope_parses_to_an_observation(self):
        obs = rules.parse_envelope(envelope())
        self.assertEqual(obs.projection, Projection("42", "INVESTIGATING", False, False))
        self.assertEqual(obs.observed_at, T0)
        self.assertEqual(obs.tenant_id, TENANT)

    def test_risk_details_cannot_ride_along(self):
        for field in ("risk_score", "rule_ids", "activations", "assigned_officer_id", "note"):
            with self.subTest(field=field), self.assertRaises(InvalidMapping) as ctx:
                rules.parse_envelope(envelope(**{field: "x"}))
            self.assertEqual(ctx.exception.field_errors[0]["code"], "unexpected_field")

    def test_wrong_type_source_or_shape_is_refused(self):
        cases = [
            {"type": "case.registered"},
            {"source_service": "vati"},
            {"source_service": "frappe"},
            {"schema_version": "2"},
            {"tenant_id": "not-a-uuid"},
        ]
        for change in cases:
            body = {**envelope(), **change}
            with self.subTest(change=change), self.assertRaises(InvalidMapping):
                rules.parse_envelope(body)

    def test_payload_values_are_validated(self):
        bad: list[dict[str, Any]] = [
            {"status": "OPEN"},
            {"status": "resolved"},
            {"escalated": "false"},
            {"jube_case_id": "0"},
            {"jube_case_id": True},
            {"observed_at": "2026-11-05 03:00:00"},
            {"observed_at": "2026-11-05T03:00:00+02:00"},
            {"closed": True, "status": "INVESTIGATING"},
        ]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(InvalidMapping):
                rules.parse_envelope(envelope(**change))
        partial = envelope()
        del partial["payload"]["observed_at"]
        with self.assertRaises(InvalidMapping):
            rules.parse_envelope(partial)


class ObservationOrderTests(unittest.TestCase):
    def test_first_observation_applies(self):
        self.assertEqual(rules.decide_observation(seen(), None, None), "apply")

    def test_newer_change_applies_and_newer_same_state_only_moves_the_watermark(self):
        later = T0 + timedelta(minutes=5)
        self.assertEqual(rules.decide_observation(seen("RESOLVED", later, closed=True),
                                                  stored(), "42"), "apply")  # fmt: skip
        self.assertEqual(rules.decide_observation(seen(at=later), stored(), "42"), "unchanged")

    def test_stale_update_is_refused(self):
        earlier = T0 - timedelta(seconds=1)
        with self.assertRaises(StaleUpdate) as ctx:
            rules.decide_observation(seen("RECEIVED", earlier), stored("AWAITING_HUMAN"), "42")
        self.assertEqual(ctx.exception.code, "version_conflict")
        self.assertEqual(ctx.exception.http_status, 409)

    def test_same_moment_same_state_is_a_noop_and_different_state_is_refused(self):
        self.assertEqual(rules.decide_observation(seen(), stored(), "42"), "unchanged")
        with self.assertRaises(StaleUpdate):
            rules.decide_observation(seen("RESOLVED", closed=True), stored(), "42")

    def test_a_different_jube_case_is_a_conflict(self):
        with self.assertRaises(MappingConflict):
            rules.decide_observation(seen(jube_case_id="43"), None, "42")
        with self.assertRaises(MappingConflict):
            rules.decide_observation(seen(jube_case_id="43", at=T0 + timedelta(1)), stored(), None)

    def test_vati_made_observation_ids_are_deterministic(self):
        self.assertEqual(seen().event_id, seen().event_id)
        self.assertNotEqual(seen().event_id, seen(at=T0 + timedelta(seconds=1)).event_id)


class InboundReplayTests(unittest.TestCase):
    def test_same_event_and_content_is_a_replay(self):
        body = envelope()
        first, again = rules.parse_envelope(body), rules.parse_envelope(dict(body))
        self.assertEqual(rules.decide_inbound(first.digest, None), "new")
        self.assertEqual(rules.decide_inbound(again.digest, first.digest), "replay")

    def test_same_event_with_changed_content_is_a_conflict(self):
        body = envelope()
        first = rules.parse_envelope(body)
        body["payload"]["status"] = "RESOLVED"
        body["payload"]["closed"] = True
        changed = rules.parse_envelope(body)
        with self.assertRaises(IdempotencyConflict):
            rules.decide_inbound(changed.digest, first.digest)

    def test_inbound_key_is_tenant_scoped(self):
        event = str(uuid.uuid4())
        other = "10000000-0000-4000-8000-000000000002"
        self.assertNotEqual(rules.inbound_key(TENANT, event), rules.inbound_key(other, event))


class LoopGuardTests(unittest.TestCase):
    def test_only_a_taxpayer_send_registers(self):
        self.assertTrue(rules.should_register("draft", "submitted", "taxpayer"))
        self.assertTrue(rules.should_register("correction_needed", "submitted", "taxpayer"))
        self.assertFalse(rules.should_register("submitted", "submitted", "taxpayer"))
        self.assertFalse(rules.should_register("submitted", "correction_needed", "staff"))
        self.assertFalse(rules.should_register("draft", "withdrawn", "taxpayer"))
        self.assertFalse(rules.should_register(None, "submitted", "operator"))

    def test_a_mirror_write_never_registers(self):
        self.assertFalse(rules.should_register("draft", "submitted", rules.ORIGIN_MIRROR))

    def test_registration_keys_are_stable_per_send(self):
        key1, payload = rules.registration_event(SUBMISSION, 2)
        self.assertEqual(rules.registration_event(SUBMISSION, 2)[0], key1)
        self.assertNotEqual(rules.registration_event(SUBMISSION, 3)[0], key1)
        self.assertEqual(rules.parse_registration(payload), (SUBMISSION, 2))
        self.assertEqual(set(payload), {"submission_id", "submission_version"})
        self.assertEqual(rules.case_registration_key(SUBMISSION),
                         rules.case_registration_key(SUBMISSION.upper()))  # fmt: skip
        for bad in (0, -1, True, "2"):
            with self.subTest(version=bad), self.assertRaises(InvalidMapping):
                rules.registration_event(SUBMISSION, bad)  # type: ignore[arg-type]


class DeclaredFactsTests(unittest.TestCase):
    def test_refund_claim_without_documents_counts_all_input_vat_as_unsupported(self):
        facts = rules.declared_facts(
            refund_claimed=Decimal("15000.00"),
            input_vat_claimed=Decimal("21000.5"),
            imported_treatments=["vat_accounted", "unsure", "not_declared", None],
        )
        self.assertEqual(tuple(facts), rules.FACT_NAMES)
        self.assertEqual(facts["refund_claim"], "15000.00")
        self.assertEqual(facts["claimed_input_vat"], "21000.50")
        self.assertEqual(facts["documented_input_vat"], "0.00")
        self.assertEqual(facts["unsupported_input_vat"], "21000.50")
        self.assertEqual(facts["imported_service_unverified_count"], "2")
        self.assertEqual(facts["consecutive_loss_periods"], "0")

    def test_missing_figures_are_zero_not_errors(self):
        facts = rules.declared_facts(
            refund_claimed=None, input_vat_claimed=None, imported_treatments=[]
        )
        self.assertEqual(facts["refund_claim"], "0.00")
        self.assertEqual(facts["unsupported_input_vat"], "0.00")

    def test_facts_are_decimal_strings_never_floats(self):
        facts = rules.declared_facts(
            refund_claimed=Decimal("0.1"), input_vat_claimed=Decimal("0.2"), imported_treatments=[]
        )
        self.assertTrue(all(isinstance(v, str) for v in facts.values()))


if __name__ == "__main__":
    unittest.main()
