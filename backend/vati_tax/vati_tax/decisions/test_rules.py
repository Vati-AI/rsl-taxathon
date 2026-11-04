"""Frappe-free tests for decision and evidence-request input rules (TAX-046)."""

from __future__ import annotations

import datetime
import unittest
import uuid
from typing import Any

from vati_tax.decisions import rules
from vati_tax.decisions.rules import DecisionRefused, VersionConflict
from vati_tax.ids.canonical import InvalidMapping

TENANT = "10000000-0000-4000-8000-000000000001"
CASE = "60000000-0000-4000-8000-000000000001"
EVIDENCE = "70000000-0000-4000-8000-000000000001"
TODAY = datetime.date(2026, 11, 4)
KEY = "synthetic-" + "a" * 32


def proposal(**overrides: Any) -> rules.Proposal:
    args: dict[str, Any] = {
        "tenant_id": TENANT,
        "case_id": CASE,
        "outcome": "close_no_further_action",
        "rationale": "Invoices reconcile with the declared input VAT.",
        "evidence_ids": [EVIDENCE],
        "finding_ids": [],
        "rating": 3,
        "expected_case_status": "AWAITING_HUMAN",
        "expected_version": 1,
        "idempotency_key": KEY,
    }
    args.update(overrides)
    return rules.Proposal.build(**args)


def reason(ctx: Any) -> str:
    return ctx.exception.field_errors[0]["code"]


class ProposalRuleTests(unittest.TestCase):
    def test_valid_proposal_and_stable_digest(self) -> None:
        self.assertEqual(proposal().digest(), proposal(case_id=CASE.upper()).digest())
        self.assertNotEqual(proposal().digest(), proposal(rating=4).digest())

    def test_rationale_limits(self) -> None:
        with self.assertRaises(InvalidMapping) as ctx:
            proposal(rationale="  too short  ")
        self.assertEqual(reason(ctx), "too_short")
        with self.assertRaises(InvalidMapping):
            proposal(rationale="x" * (rules.RATIONALE_MAX + 1))

    def test_rating_and_version_must_be_real_integers(self) -> None:
        for bad in (0, 6, True, "3", 2.5):
            with self.assertRaises(InvalidMapping, msg=repr(bad)):
                proposal(rating=bad)
        for bad in (0, -1, True, "1"):
            with self.assertRaises(InvalidMapping, msg=repr(bad)):
                proposal(expected_version=bad)

    def test_citations(self) -> None:
        with self.assertRaises(InvalidMapping) as ctx:
            proposal(evidence_ids=[EVIDENCE, EVIDENCE.upper()])
        self.assertEqual(reason(ctx), "duplicate")
        with self.assertRaises(InvalidMapping):
            proposal(evidence_ids=["not-a-uuid"])
        with self.assertRaises(InvalidMapping):
            proposal(evidence_ids=[str(uuid.uuid4()) for _ in range(rules.MAX_CITATIONS + 1)])
        # Non-closing outcomes may cite nothing.
        proposal(outcome="continue_investigation", evidence_ids=[])

    def test_every_outcome_maps_to_a_contract_status(self) -> None:
        for rule in rules.OUTCOMES.values():
            self.assertIn(rule.target_status, rules.CASE_STATUSES)
            self.assertIsNone(rules.PROHIBITED_OUTCOME.search(rule.operation))
        for name in rules.OUTCOMES:
            self.assertIsNone(rules.PROHIBITED_OUTCOME.search(name), name)

    def test_unknown_status_is_refused(self) -> None:
        with self.assertRaises(InvalidMapping):
            proposal(expected_case_status="CLOSED")


class ReviewRuleTests(unittest.TestCase):
    def test_reject_needs_a_reason(self) -> None:
        with self.assertRaises(InvalidMapping):
            rules.Review.build(
                action="reject", decision_id=CASE, expected_version=2, idempotency_key=KEY
            )

    def test_reviewable(self) -> None:
        rules.check_reviewable(
            status="proposed", origin="officer", proposer_role="auditor", action="approve"
        )
        with self.assertRaises(DecisionRefused):
            rules.check_reviewable(
                status="draft", origin="agent_draft", proposer_role="service", action="approve"
            )
        with self.assertRaises(VersionConflict):
            rules.check_reviewable(
                status="rejected", origin="officer", proposer_role="auditor", action="approve"
            )

    def test_age_limits(self) -> None:
        start = datetime.datetime(2026, 11, 4, tzinfo=datetime.UTC)
        rules.check_fresh_proposal(start, start + rules.MAX_PROPOSAL_AGE)
        with self.assertRaises(VersionConflict):
            rules.check_fresh_proposal(start, start + rules.MAX_PROPOSAL_AGE * 2)
        self.assertFalse(rules.approval_expired(start, start + rules.MAX_APPROVAL_AGE))
        self.assertTrue(rules.approval_expired(start, start + rules.MAX_APPROVAL_AGE * 2))


class EvidenceRequestRuleTests(unittest.TestCase):
    def build(self, **overrides: Any) -> rules.EvidenceRequestInput:
        body: dict[str, Any] = {
            "expected_version": 1,
            "finding_ids": [EVIDENCE],
            "items": [{"document_type": "bank_statement", "description": "September."}],
            "due_date": "2026-12-01",
            "message_to_taxpayer": "Please upload your September bank statement.",
        }
        body.update(overrides)
        return rules.EvidenceRequestInput.build(
            tenant_id=TENANT, case_id=CASE, body=body, idempotency_key=KEY, today=TODAY
        )

    def test_contract_shape(self) -> None:
        self.assertEqual(self.build().items[0].document_type, "bank_statement")
        with self.assertRaises(InvalidMapping) as ctx:
            self.build(extra="x")
        self.assertEqual(reason(ctx), "unknown_field")
        with self.assertRaises(InvalidMapping):
            self.build(items=[])
        with self.assertRaises(InvalidMapping):
            self.build(items=[{"document_type": "invoice"}])
        with self.assertRaises(InvalidMapping):
            self.build(message_to_taxpayer="")

    def test_due_date_window(self) -> None:
        with self.assertRaises(DecisionRefused):
            self.build(due_date=TODAY.isoformat())
        with self.assertRaises(DecisionRefused):
            self.build(due_date=(TODAY + datetime.timedelta(days=181)).isoformat())
        with self.assertRaises(InvalidMapping):
            self.build(due_date="2026-02-30")


class UpstreamClassificationTests(unittest.TestCase):
    def test_classification(self) -> None:
        class Err(Exception):
            def __init__(self, code: str, retryable: bool) -> None:
                self.code, self.retryable = code, retryable

        self.assertEqual(
            rules.classify_upstream_error(Err("version_conflict", True)),
            ("version_conflict", False),
        )
        self.assertEqual(
            rules.classify_upstream_error(Err("upstream_timeout", True)), ("upstream_timeout", True)
        )
        self.assertEqual(
            rules.classify_upstream_error(OSError("down")), ("upstream_unavailable", True)
        )


if __name__ == "__main__":
    unittest.main()
