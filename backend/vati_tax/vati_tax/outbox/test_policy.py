"""Behaviour tests for outbox rules (no Frappe needed)."""

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from vati_tax.ids.canonical import IdempotencyConflict, InvalidMapping
from vati_tax.outbox import policy
from vati_tax.outbox.policy import (
    EffectOutcomeUnknown,
    Envelope,
    Failure,
    IdempotencyInProgress,
    InvalidTransition,
    PermanentDeliveryError,
    RetryableDeliveryError,
    RetryPolicy,
)

TENANT_A = "00000000-0000-4000-8000-00000000000a"
TENANT_B = "00000000-0000-4000-8000-00000000000b"
CASE_1 = "11111111-1111-4111-8111-111111111111"
CASE_2 = "22222222-2222-4222-8222-222222222222"
CORR = "c0000000-0000-4000-8000-000000000001"
KEY = "synthetic:frappe:submission-0001"
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
OPENAPI = Path(__file__).resolve().parents[4] / "integration" / "api" / "openapi.json"


def env(**overrides: Any) -> Envelope:
    args: dict[str, Any] = dict(
        tenant_id=TENANT_A,
        case_id=CASE_1,
        type="evidence_request.recorded",
        payload={"evidence_request_id": "ER-SYN-0001", "items": 2},
        idempotency_key=KEY,
        correlation_id=CORR,
        occurred_at=NOW,
    )
    args.update(overrides)
    return Envelope.build(**args)


class EnvelopeTests(unittest.TestCase):
    def test_envelope_matches_the_contract_shape(self):
        data = env().as_dict()
        self.assertEqual(data["schema_version"], "1")
        self.assertEqual(data["occurred_at"], "2026-11-04T09:00:00Z")
        self.assertEqual(data["source_service"], "vati")
        self.assertEqual(policy.utc(datetime(2026, 1, 1)).tzinfo, UTC)
        if OPENAPI.is_file():
            schema = json.loads(OPENAPI.read_text())["components"]["schemas"]["EventEnvelope"]
            self.assertEqual(sorted(data), sorted(schema["required"]))
            self.assertEqual(tuple(schema["properties"]["type"]["enum"]), policy.EVENT_TYPES)

    def test_invalid_envelopes_are_rejected(self):
        bad: list[dict[str, Any]] = [
            {"type": "case.deleted"},
            {"source_service": "excel"},
            {"tenant_id": "tenant-a"},
            {"case_id": None},
            {"correlation_id": ""},
            {"idempotency_key": "short"},
            {"payload": ["not", "an", "object"]},
            {"payload": {"amount": 10.5}},  # money must be a decimal string
            {"payload": {"Document Text": "x"}},
            {"payload": {"note": "x" * 256}},
            {"payload": {"nested": {"a": 1}}},
        ]
        for override in bad:
            with self.subTest(override=override), self.assertRaises(InvalidMapping):
                env(**override)

    def test_event_id_is_generated_once_and_canonicalised(self):
        first, second = env(), env()
        self.assertNotEqual(first.event_id, second.event_id)
        upper = "E0000000-0000-4000-8000-0000000000AA"
        self.assertEqual(env(event_id=upper).event_id, upper.lower())

    def test_digest_ignores_event_id_and_time_but_not_the_request(self):
        base = env()
        self.assertEqual(
            base.request_digest, env(occurred_at=NOW + timedelta(hours=1)).request_digest
        )
        self.assertNotEqual(base.request_digest, env(payload={"items": 3}).request_digest)
        self.assertNotEqual(base.request_digest, env(case_id=CASE_2).request_digest)
        self.assertNotEqual(base.request_digest, env(type="finding.recorded").request_digest)

    def test_keys_are_tenant_scoped(self):
        self.assertNotEqual(env().dedupe_key, env(tenant_id=TENANT_B).dedupe_key)
        self.assertNotEqual(env().ordering_key, env(tenant_id=TENANT_B).ordering_key)
        self.assertNotEqual(env().ordering_key, env(case_id=CASE_2).ordering_key)
        self.assertLess(policy.sequence_key("k", 9), policy.sequence_key("k", 10))


class EnqueueDecisionTests(unittest.TestCase):
    def test_same_key_same_request_replays_and_changed_request_conflicts(self):
        digest = env().request_digest
        self.assertEqual(policy.decide_enqueue(digest, None), "create")
        self.assertEqual(policy.decide_enqueue(digest, digest), "replay")
        with self.assertRaises(IdempotencyConflict) as ctx:
            policy.decide_enqueue(env(payload={"items": 3}).request_digest, digest)
        self.assertEqual(ctx.exception.http_status, 422)
        self.assertEqual(ctx.exception.code, "idempotency_conflict")


class RetryTests(unittest.TestCase):
    def test_backoff_is_exponential_jittered_and_capped(self):
        p = RetryPolicy(base_delay=timedelta(seconds=10), max_delay=timedelta(seconds=60))
        low = [policy.backoff(n, p, rand=lambda: 0.0).total_seconds() for n in range(1, 6)]
        high = [policy.backoff(n, p, rand=lambda: 1.0).total_seconds() for n in range(1, 6)]
        self.assertEqual(low, [5, 10, 20, 30, 30])
        self.assertEqual(high, [10, 20, 40, 60, 60])
        self.assertEqual(policy.backoff(100, p, rand=lambda: 1.0).total_seconds(), 60)
        with self.assertRaises(ValueError):
            policy.backoff(0, p)

    def test_retry_after_is_honoured_up_to_the_cap(self):
        p = RetryPolicy(base_delay=timedelta(seconds=10), max_delay=timedelta(seconds=60))
        self.assertEqual(policy.backoff(1, p, lambda: 0.0, retry_after=45).total_seconds(), 45)
        self.assertEqual(policy.backoff(1, p, lambda: 0.0, retry_after=900).total_seconds(), 60)

    def test_budget_and_classification_decide_retry_or_dead_letter(self):
        p = RetryPolicy(max_attempts=3, base_delay=timedelta(seconds=10))
        transient = Failure("upstream_timeout", True)
        first = policy.after_failure(1, transient, NOW, p, lambda: 1.0)
        self.assertEqual(first.status, "retry")
        self.assertEqual(first.next_attempt_at, NOW + timedelta(seconds=10))
        self.assertEqual(policy.after_failure(2, transient, NOW, p).status, "retry")
        self.assertEqual(policy.after_failure(3, transient, NOW, p).status, "dead_letter")
        poison = Failure("invalid_domain_input", False)
        self.assertEqual(policy.after_failure(1, poison, NOW, p).status, "dead_letter")

    def test_policy_rejects_unbounded_settings(self):
        for kwargs in (
            {"max_attempts": 0},
            {"max_attempts": 1000},
            {"base_delay": timedelta(0)},
            {"base_delay": timedelta(minutes=5), "max_delay": timedelta(minutes=1)},
            {"lease": timedelta(0)},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                RetryPolicy(**kwargs)

    def test_failures_keep_a_code_but_never_the_message(self):
        secret = "taxpayer bank statement text"
        cases = [
            (RetryableDeliveryError("rate_limited", retry_after=30), "rate_limited", True),
            (PermanentDeliveryError("invalid_domain_input"), "invalid_domain_input", False),
            (EffectOutcomeUnknown(), "effect_outcome_unknown", False),
            (IdempotencyConflict(secret), "idempotency_conflict", False),
            (TimeoutError(secret), "upstream_timeout", True),
            (ConnectionRefusedError(secret), "upstream_unavailable", True),
            (KeyError(secret), "handler_error", True),
            (RetryableDeliveryError(secret), "handler_error", True),
        ]
        for exc, code, retryable in cases:
            with self.subTest(exc=type(exc).__name__):
                failure = policy.classify(exc)
                self.assertEqual((failure.code, failure.retryable), (code, retryable))
                self.assertNotIn(secret, repr(failure))
        self.assertEqual(policy.classify(cases[0][0]).retry_after, 30)


class ClaimTests(unittest.TestCase):
    def test_due_and_expired_lease_events_are_claimable(self):
        later, earlier = NOW + timedelta(minutes=1), NOW - timedelta(minutes=1)
        self.assertTrue(policy.is_claimable("pending", None, None, NOW))
        self.assertTrue(policy.is_claimable("retry", earlier, None, NOW))
        self.assertFalse(policy.is_claimable("retry", later, None, NOW))
        self.assertFalse(policy.is_claimable("in_progress", None, later, NOW))
        self.assertTrue(policy.is_claimable("in_progress", None, earlier, NOW))
        for final in ("delivered", "dead_letter"):
            self.assertFalse(policy.is_claimable(final, None, None, NOW))

    def test_manual_retry_only_from_dead_letter(self):
        policy.manual_retry_allowed("dead_letter", None, NOW)
        with self.assertRaises(IdempotencyInProgress) as ctx:
            policy.manual_retry_allowed("in_progress", NOW + timedelta(minutes=1), NOW)
        self.assertEqual(ctx.exception.http_status, 409)
        for status in ("pending", "retry", "delivered"):
            with self.subTest(status=status), self.assertRaises(InvalidTransition):
                policy.manual_retry_allowed(status, None, NOW)

    def test_sync_state_covers_every_status(self):
        self.assertEqual(set(policy.SYNC_STATE), set(policy.STATUSES))
        self.assertEqual(policy.SYNC_STATE["dead_letter"], "failed")
        self.assertEqual(policy.SYNC_STATE["delivered"], "synced")


class EffectTests(unittest.TestCase):
    def test_completed_effects_are_reused_never_repeated(self):
        for idempotent in (True, False):
            for reconcile in (True, False):
                self.assertEqual(
                    policy.decide_effect(
                        "completed", idempotent=idempotent, can_reconcile=reconcile
                    ),
                    "reuse",
                )

    def test_unknown_outcome_is_not_blindly_resent(self):
        self.assertEqual(
            policy.decide_effect(None, idempotent=False, can_reconcile=False), "perform"
        )
        self.assertEqual(
            policy.decide_effect("started", idempotent=True, can_reconcile=False), "perform"
        )
        self.assertEqual(
            policy.decide_effect("started", idempotent=False, can_reconcile=True), "reconcile"
        )
        self.assertEqual(
            policy.decide_effect("started", idempotent=False, can_reconcile=False), "unknown"
        )

    def test_effect_keys_are_stable_and_bounded(self):
        self.assertEqual(
            policy.effect_idempotency_key(KEY, "jube.create_case"), KEY + ":jube.create_case"
        )
        long_key = "k" * 255
        derived = policy.effect_idempotency_key(long_key, "jube.create_case")
        self.assertLessEqual(len(derived), 255)
        self.assertEqual(derived, policy.effect_idempotency_key(long_key, "jube.create_case"))
        self.assertNotEqual(
            policy.effect_key(TENANT_A, "e1", "x"), policy.effect_key(TENANT_B, "e1", "x")
        )
        for name in ("", "Upper", "has space", "x" * 65):
            with self.subTest(name=name), self.assertRaises(InvalidMapping):
                policy.validate_effect_name(name)
        for ref in ("", " padded", "x" * 256, "line\nbreak"):
            with self.subTest(ref=ref), self.assertRaises(InvalidMapping):
                policy.validate_result_ref(ref)


if __name__ == "__main__":
    unittest.main()
