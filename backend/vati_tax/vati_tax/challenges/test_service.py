"""Frappe-free tests for objections, appeals and reviewed outcomes (TAX-069).

Decisions are made with the real TAX-046 engine (``decisions.test_engine``
harness), and challenges are sealed into the same in-memory ledger, so the
tests can show the original decision and its ledger entries are untouched.
"""

from __future__ import annotations

import datetime
import unittest
import uuid
from dataclasses import replace
from typing import Any

from vati_tax.audit import ledger
from vati_tax.audit import service as audit
from vati_tax.challenges import rules, service
from vati_tax.decisions.test_engine import (
    AUDITOR,
    NOW,
    SERVICE,
    SPECIALIST,
    SUPERVISOR,
    SUPERVISOR_2,
    T1,
    T2,
    TAXPAYER,
    TAXPAYER_SCOPE,
    Harness,
    scope,
)
from vati_tax.identity import policy
from vati_tax.ids.canonical import IdempotencyConflict, InvalidMapping

SUPERVISOR_3 = scope("supervisor3@example.test", policy.SUPERVISOR)
SUPERVISOR_4 = scope("supervisor4@example.test", policy.SUPERVISOR)
SUPERVISOR_T2 = scope("supervisor3@example.test", policy.SUPERVISOR, T2)
OTHER_TAXPAYER_SCOPE = policy.TenantScope(
    user="other-taxpayer@example.test",
    tenant_id=T1,
    roles=frozenset({policy.TAXPAYER}),
    taxpayer_ids=frozenset({"80015243-dd34-4e97-b085-7356ffdd0430"}),
)
GROUNDS = "The supplier invoices for March were omitted from the reconciliation."
REASON = "The March invoices change the reconciled input VAT for the period."


def idem() -> str:
    return "synthetic-" + uuid.uuid4().hex


class World:
    """An approved decision (auditor proposes, supervisor approves) and its ports."""

    def __init__(self) -> None:
        self.h = Harness()
        proposed = self.h.propose(AUDITOR)
        self.h.review(proposed["decision_id"], who=SUPERVISOR)
        self.decision_id = proposed["decision_id"]
        self.challenges = service.MemoryChallenges()
        self.ports = service.Ports(
            challenges=self.challenges, decisions=self.h.repo, ledger=self.h.repo.ledger
        )
        self.new_evidence = str(uuid.uuid4())
        self.h.repo.evidence[self.new_evidence] = (T1, TAXPAYER)

    def ctx(self, who: policy.TenantScope, now: datetime.datetime = NOW) -> audit.Context:
        return audit.Context.build(who, None, now)

    def object(
        self, who: policy.TenantScope = TAXPAYER_SCOPE, key: str | None = None, **body: Any
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stage": "objection",
            "decision_id": self.decision_id,
            "grounds": GROUNDS,
            "evidence_ids": [self.new_evidence],
        }
        payload.update(body)
        return service.lodge(self.ctx(who), self.ports, body=payload, idempotency_key=key or idem())

    def appeal(
        self, parent: dict[str, Any], who: policy.TenantScope = TAXPAYER_SCOPE, **body: Any
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stage": "appeal",
            "parent_challenge_id": parent["challenge_id"],
            "grounds": "The objection review did not consider the March credit notes.",
        }
        payload.update(body)
        return service.lodge(self.ctx(who), self.ports, body=payload, idempotency_key=idem())

    def review(
        self,
        challenge: dict[str, Any],
        outcome: str = "upheld",
        who: policy.TenantScope = SUPERVISOR_2,
        **kw: Any,
    ) -> dict[str, Any]:
        return service.review(
            self.ctx(who),
            self.ports,
            challenge_id=challenge["challenge_id"],
            expected_version=kw.pop("expected_version", challenge["version"]),
            outcome=outcome,
            reason=kw.pop("reason", REASON),
        )

    def history(self, who: policy.TenantScope = AUDITOR) -> dict[str, Any]:
        return service.history(self.ctx(who), self.ports, decision_id=self.decision_id)

    def actions(self) -> list[tuple[str, str]]:
        return [(e.action, e.outcome) for e in self.h.repo.ledger.entries(T1)]


class LodgeTests(unittest.TestCase):
    def test_taxpayer_objects_to_an_approved_decision_and_it_is_recorded(self):
        w = World()
        view = w.object()
        self.assertEqual((view["stage"], view["status"]), ("objection", "lodged"))
        self.assertEqual(view["decision_id"], w.decision_id)
        self.assertFalse(view["legal_route_verified"])
        self.assertNotIn("lodged_by", view)  # taxpayer view hides staff identities
        self.assertEqual(w.actions()[-1], ("challenge.lodged", "objection"))

    def test_officer_can_record_an_objection_on_the_taxpayers_behalf(self):
        w = World()
        view = w.object(AUDITOR)
        self.assertEqual(view["lodger_role"], "auditor")

    def test_unapproved_decisions_cannot_be_challenged(self):
        w = World()
        proposed = w.h.propose(AUDITOR, case_id=w.h.add_case())
        with self.assertRaises(rules.ChallengeConflict) as raised:
            w.object(decision_id=proposed["decision_id"])
        self.assertEqual(raised.exception.field_errors[0]["code"], "decision_not_final")
        self.assertEqual(w.actions()[-1], ("challenge.lodge.denied", "decision_not_final"))

    def test_another_taxpayer_or_tenant_cannot_see_the_decision(self):
        w = World()
        with self.assertRaises(rules.ChallengeNotFound):
            w.object(OTHER_TAXPAYER_SCOPE)
        with self.assertRaises(rules.ChallengeNotFound):
            w.object(scope("auditor@example.test", policy.AUDITOR, T2))
        with self.assertRaises(rules.ChallengeNotFound):
            w.object(decision_id=str(uuid.uuid4()))

    def test_specialist_and_service_cannot_lodge_and_attempts_are_recorded(self):
        for who in (SPECIALIST, SERVICE):
            w = World()
            with self.subTest(user=who.user), self.assertRaises(policy.ActionDenied):
                w.object(who)
            self.assertEqual(w.actions()[-1], ("challenge.lodge.denied", "forbidden"))

    def test_cited_evidence_must_belong_to_the_decisions_taxpayer(self):
        w = World()
        foreign = str(uuid.uuid4())
        w.h.repo.evidence[foreign] = (T1, "80015243-dd34-4e97-b085-7356ffdd0430")
        with self.assertRaises(rules.ChallengeNotFound):
            w.object(evidence_ids=[foreign])
        self.assertEqual(w.challenges.rows, {})

    def test_one_objection_per_decision_and_none_while_one_is_open(self):
        w = World()
        first = w.object()
        with self.assertRaises(rules.ChallengeConflict) as open_one:
            w.object()
        self.assertEqual(open_one.exception.field_errors[0]["code"], "challenge_open")
        w.review(first)
        with self.assertRaises(rules.ChallengeConflict) as again:
            w.object()
        self.assertEqual(again.exception.field_errors[0]["code"], "objection_exists")

    def test_replay_returns_the_objection_and_a_changed_body_conflicts(self):
        w = World()
        key = idem()
        first = w.object(key=key)
        self.assertTrue(w.object(key=key)["replayed"])
        with self.assertRaises(IdempotencyConflict):
            w.object(key=key, grounds=GROUNDS + " Also the April invoices.")
        self.assertEqual(len(w.challenges.rows), 1)
        self.assertEqual(first["challenge_id"], w.object(key=key)["challenge_id"])

    def test_invalid_requests_are_refused_before_anything_is_written(self):
        w = World()
        before = len(w.h.repo.ledger.rows)
        for body in (
            {"stage": "complaint"},
            {"grounds": "too short"},
            {"decision_id": None},
            {"evidence_ids": ["nope"]},
        ):
            with self.subTest(body=body), self.assertRaises(InvalidMapping):
                w.object(**body)
        self.assertEqual(len(w.h.repo.ledger.rows), before)


class ReviewTests(unittest.TestCase):
    def test_original_proposer_approver_and_lodger_cannot_review(self):
        w = World()
        objection = w.object(SUPERVISOR_3)
        for who in (SUPERVISOR, SUPERVISOR_3):  # approver, lodger
            with self.subTest(user=who.user), self.assertRaises(policy.ActionDenied):
                w.review(objection, who=who)
        proposer_as_supervisor = scope(AUDITOR.user, policy.SUPERVISOR)
        with self.assertRaises(policy.ActionDenied):
            w.review(objection, who=proposer_as_supervisor)
        with self.assertRaises(policy.ActionDenied):
            w.review(objection, who=AUDITOR)
        self.assertEqual(w.challenges.rows[objection["challenge_id"]].status, "lodged")
        denied = [a for a in w.actions() if a[0] == "challenge.review.denied"]
        self.assertEqual(len(denied), 4)

    def test_review_needs_known_outcome_reason_and_current_version(self):
        w = World()
        objection = w.object()
        with self.assertRaises(InvalidMapping):
            w.review(objection, outcome="refund_paid")
        with self.assertRaises(InvalidMapping):
            w.review(objection, reason="short")
        with self.assertRaises(rules.ChallengeConflict) as stale:
            w.review(objection, expected_version=7)
        self.assertEqual(stale.exception.field_errors[0]["code"], "stale_version")
        w.review(objection)
        with self.assertRaises(rules.ChallengeConflict) as twice:
            w.review(objection, who=SUPERVISOR_4)
        self.assertEqual(twice.exception.field_errors[0]["code"], "not_pending")

    def test_another_tenant_cannot_review(self):
        w = World()
        objection = w.object()
        with self.assertRaises(rules.ChallengeNotFound):
            w.review(objection, who=SUPERVISOR_T2)


class ChainTests(unittest.TestCase):
    def test_decision_objection_appeal_and_final_outcome_are_linked(self):
        w = World()
        objection = w.object()
        w.review(objection, "partially_overturned")
        appeal = w.appeal(objection)
        self.assertEqual(appeal["decision_id"], w.decision_id)
        self.assertEqual(appeal["parent_challenge_id"], objection["challenge_id"])
        w.review(appeal, "overturned", who=SUPERVISOR_3)
        view = w.history()
        self.assertEqual([c["stage"] for c in view["challenges"]], ["objection", "appeal"])
        self.assertEqual(view["final_outcome"]["challenge_id"], appeal["challenge_id"])
        self.assertEqual(view["final_outcome"]["outcome"], "overturned")
        self.assertEqual(view["overturned"], [objection["challenge_id"], appeal["challenge_id"]])
        self.assertTrue(view["original_decision_unchanged"])
        self.assertEqual(view["challenges"][1]["follow_up"], "new_decision_required")

    def test_appeal_rules(self):
        w = World()
        objection = w.object()
        with self.assertRaises(rules.ChallengeConflict) as pending:
            w.appeal(objection)  # objection not reviewed yet
        self.assertEqual(pending.exception.field_errors[0]["code"], "challenge_open")
        w.review(objection, "overturned")
        with self.assertRaises(rules.ChallengeConflict) as won:
            w.appeal(objection)
        self.assertEqual(won.exception.field_errors[0]["code"], "not_appealable")
        w2 = World()
        objection2 = w2.object()
        w2.review(objection2, "upheld")
        first = w2.appeal(objection2)
        w2.review(first, "upheld", who=SUPERVISOR_3)
        with self.assertRaises(rules.ChallengeConflict) as twice:
            w2.appeal(objection2)
        self.assertEqual(twice.exception.field_errors[0]["code"], "appeal_exists")
        with self.assertRaises(rules.ChallengeConflict) as of_appeal:
            w2.appeal(first)
        self.assertEqual(of_appeal.exception.field_errors[0]["code"], "not_objection")

    def test_objection_reviewer_cannot_review_the_appeal(self):
        w = World()
        objection = w.object()
        w.review(objection, "upheld", who=SUPERVISOR_2)
        appeal = w.appeal(objection)
        with self.assertRaises(policy.ActionDenied):
            w.review(appeal, who=SUPERVISOR_2)
        self.assertEqual(w.review(appeal, who=SUPERVISOR_3)["status"], "reviewed")


class TraceabilityTests(unittest.TestCase):
    def test_overturning_rewrites_neither_decision_evidence_nor_ledger(self):
        w = World()
        decision_before = replace(w.h.repo.decisions[w.decision_id])
        evidence_before = dict(w.h.repo.evidence)
        ledger_before = list(w.h.repo.ledger.entries(T1))
        objection = w.object()
        w.review(objection, "overturned")
        self.assertEqual(w.h.repo.decisions[w.decision_id], decision_before)
        self.assertEqual(w.h.repo.decisions[w.decision_id].status, "approved")
        self.assertEqual(w.h.repo.evidence, evidence_before)
        after = w.h.repo.ledger.entries(T1)
        self.assertEqual(after[: len(ledger_before)], ledger_before)
        self.assertTrue(ledger.verify_chain(after)["intact"])
        self.assertEqual(w.h.jube.calls[-1][0], "transition")  # only the original approval
        self.assertEqual(len(w.h.jube.calls), 1)

    def test_overturned_outcome_raises_a_review_only_signal_hidden_from_the_decider(self):
        w = World()
        objection = w.object()
        w.review(objection, "overturned")
        entries = w.h.repo.ledger.entries(T1)
        seen = ledger.integrity_signals(entries, now=NOW, viewer=SUPERVISOR_3.user)
        [signal] = [s for s in seen["signals"] if s["kind"] == "decision_overturned"]
        self.assertEqual(signal["status"], "review_only")
        self.assertEqual(signal["subject_actor"], AUDITOR.user)
        self.assertIn("not a finding", signal["note"])
        as_decider = ledger.integrity_signals(entries, now=NOW, viewer=AUDITOR.user)
        self.assertNotIn("decision_overturned", [s["kind"] for s in as_decider["signals"]])
        self.assertEqual(as_decider["withheld_about_viewer"], 1)

    def test_upheld_outcome_raises_no_signal(self):
        w = World()
        w.review(w.object(), "upheld")
        kinds = [
            s["kind"]
            for s in ledger.integrity_signals(
                w.h.repo.ledger.entries(T1), now=NOW, viewer=SUPERVISOR_3.user
            )["signals"]
        ]
        self.assertNotIn("decision_overturned", kinds)

    def test_a_changed_decision_blocks_review_and_shows_in_history(self):
        w = World()
        objection = w.object()
        w.h.repo.decisions[w.decision_id].rationale = "Rewritten after the objection."
        with self.assertRaises(rules.ChallengeConflict) as raised:
            w.review(objection)
        self.assertEqual(raised.exception.field_errors[0]["code"], "decision_changed")
        self.assertFalse(w.history()["original_decision_unchanged"])

    def test_sync_bookkeeping_after_lodging_is_not_treated_as_a_change(self):
        w = World()
        objection = w.object()
        w.h.repo.decisions[w.decision_id].sync_attempts += 1
        self.assertEqual(w.review(objection)["status"], "reviewed")


class ReadTests(unittest.TestCase):
    def test_history_is_recorded_and_scoped(self):
        w = World()
        w.object()
        own = w.history(TAXPAYER_SCOPE)
        self.assertNotIn("lodged_by", own["challenges"][0])
        self.assertIsNone(own["final_outcome"])
        self.assertEqual(w.actions()[-1][0], "challenge.read")
        with self.assertRaises(rules.ChallengeNotFound):
            w.history(OTHER_TAXPAYER_SCOPE)
        self.assertEqual(w.history(SERVICE)["decision"]["decision_id"], w.decision_id)

    def test_policy_matrix(self):
        self.assertIn(policy.CHALLENGE_REVIEW, policy.HUMAN_ONLY_ACTIONS)
        self.assertIn(policy.CHALLENGE_LODGE, policy.HUMAN_ONLY_ACTIONS)
        self.assertEqual(policy.ACTION_ROLES[policy.CHALLENGE_REVIEW], {policy.SUPERVISOR})
        self.assertIn(policy.CHALLENGE_LODGE, policy.TAXPAYER_OWNED_ACTIONS)


if __name__ == "__main__":
    unittest.main()
