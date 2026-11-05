"""Frappe-free tests for correction reminders (TAX-078).

Decisions are made with the real TAX-046 engine (``decisions.test_engine``
harness) and challenged with the real TAX-069 service, so reminders are tested
against the same records and ledger the rest of the workflow uses.
"""

from __future__ import annotations

import datetime
import unittest
import uuid
from typing import Any

from vati_tax.audit import service as audit
from vati_tax.challenges import service as challenges
from vati_tax.decisions.test_engine import (
    AUDITOR,
    AUDITOR_T2,
    NOW,
    OTHER_TAXPAYER,
    SERVICE,
    SPECIALIST,
    SUPERVISOR,
    SUPERVISOR_2,
    T1,
    T2,
    TAXPAYER,
    TAXPAYER_SCOPE,
    Harness,
)
from vati_tax.identity import policy
from vati_tax.ids.canonical import IdempotencyConflict, InvalidMapping, MappingError
from vati_tax.reminders import rules, service

EXPLANATION = (
    "Your March claim listed fewer purchase invoices than the VAT it claimed. "
    "List every purchase invoice for the period."
)
REASON = "The earlier wording named the wrong month for the missing invoices."
TAXPAYER_FIELDS = {
    "reminder_id",
    "topic",
    "title",
    "explanation",
    "prompt_codes",
    "recorded_on",
    "updated_on",
}


def idem() -> str:
    return "synthetic-" + uuid.uuid4().hex


class World:
    """An approved ``close_after_correction`` decision for TAXPAYER and the ports."""

    def __init__(self) -> None:
        self.h = Harness()
        self.decision_id = self.approved("close_after_correction")
        self.reminders = service.MemoryReminders()
        self.challenges = challenges.MemoryChallenges()
        self.ports = service.Ports(
            reminders=self.reminders,
            decisions=self.h.repo,
            challenges=self.challenges,
            ledger=self.h.repo.ledger,
        )
        self.challenge_ports = challenges.Ports(
            challenges=self.challenges, decisions=self.h.repo, ledger=self.h.repo.ledger
        )

    def approved(self, outcome: str, case_id: str | None = None, **overrides: Any) -> str:
        case_id = case_id or self.h.add_case()
        proposed = self.h.propose(AUDITOR, case_id=case_id, outcome=outcome, **overrides)
        self.h.review(proposed["decision_id"], who=SUPERVISOR)
        return str(proposed["decision_id"])

    def ctx(self, who: policy.TenantScope, minutes: int = 0) -> audit.Context:
        return audit.Context.build(who, None, NOW + datetime.timedelta(minutes=minutes))

    def record(
        self, who: policy.TenantScope = AUDITOR, key: str | None = None, **body: Any
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "decision_id": self.decision_id,
            "topic": "missing_invoices",
            "explanation": EXPLANATION,
        }
        payload.update(body)
        return service.record(
            self.ctx(who), self.ports, body=payload, idempotency_key=key or idem()
        )

    def correct(
        self, reminder: dict[str, Any], who: policy.TenantScope = AUDITOR, **kw: Any
    ) -> dict[str, Any]:
        return service.correct(
            self.ctx(who, minutes=kw.pop("minutes", 5)),
            self.ports,
            reminder_id=reminder["reminder_id"],
            expected_version=kw.pop("expected_version", reminder["version"]),
            action=kw.pop("action", "correct"),
            reason=kw.pop("reason", REASON),
            **kw,
        )

    def portal(self, scope: list[tuple[str, str]] | None = None) -> list[dict[str, Any]]:
        return service.taxpayer_list(self.ports, scope or [(T1, TAXPAYER)])

    def object(self) -> dict[str, Any]:
        return challenges.lodge(
            self.ctx(TAXPAYER_SCOPE),
            self.challenge_ports,
            body={
                "stage": "objection",
                "decision_id": self.decision_id,
                "grounds": "The March invoices were in the second upload, not missing.",
            },
            idempotency_key=idem(),
        )

    def review_challenge(self, challenge: dict[str, Any], outcome: str) -> dict[str, Any]:
        return challenges.review(
            self.ctx(SUPERVISOR_2),
            self.challenge_ports,
            challenge_id=challenge["challenge_id"],
            expected_version=challenge["version"],
            outcome=outcome,
            reason="The second upload did contain the March purchase invoices.",
        )

    def actions(self) -> list[tuple[str, str]]:
        return [(e.action, e.outcome) for e in self.h.repo.ledger.entries(T1)]

    def assert_code(self, exc: MappingError, code: str) -> None:
        assert exc.field_errors and exc.field_errors[0]["code"] == code, exc.field_errors


class RecordTests(unittest.TestCase):
    def test_officer_records_a_reminder_the_taxpayer_then_sees(self):
        w = World()
        view = w.record()
        self.assertEqual(view["status"], "active")
        self.assertEqual(view["taxpayer_id"], TAXPAYER)
        self.assertTrue(view["visible_to_taxpayer"])
        self.assertFalse(view["replayed"])
        self.assertIn(("reminder.recorded", "missing_invoices"), w.actions())
        [shown] = w.portal()
        self.assertEqual(set(shown), TAXPAYER_FIELDS)
        self.assertEqual(shown["explanation"], EXPLANATION)
        self.assertEqual(shown["title"], "List every purchase invoice")
        self.assertEqual(shown["prompt_codes"], ["refund_claim_exceeds_documented_input_vat"])
        self.assertIsNone(shown["updated_on"])

    def test_supervisor_may_record_too(self):
        w = World()
        self.assertEqual(w.record(SUPERVISOR_2)["status"], "active")

    def test_explanation_whitespace_is_normalised(self):
        w = World()
        view = w.record(explanation="  Attach the contract   for each\n large purchase invoice.  ")
        self.assertEqual(
            view["explanation"], "Attach the contract for each large purchase invoice."
        )

    def test_replay_returns_the_stored_reminder_and_changes_conflict(self):
        w = World()
        key = idem()
        first = w.record(key=key)
        again = w.record(key=key)
        self.assertTrue(again["replayed"])
        self.assertEqual(again["reminder_id"], first["reminder_id"])
        with self.assertRaises(IdempotencyConflict):
            w.record(key=key, topic="payment_details")
        with self.assertRaises(IdempotencyConflict):
            w.record(SUPERVISOR_2, key=key)
        self.assertEqual(len(w.reminders.rows), 1)

    def test_roles_without_record_permission_are_refused_and_recorded(self):
        w = World()
        for who in (TAXPAYER_SCOPE, SPECIALIST, SERVICE):
            with self.subTest(who=who.user), self.assertRaises(policy.ActionDenied):
                w.record(who)
        self.assertEqual(w.reminders.rows, {})
        denied = [a for a, _ in w.actions() if a == "reminder.record.denied"]
        self.assertEqual(len(denied), 3)

    def test_only_an_approved_omission_decision_can_be_the_source(self):
        w = World()
        cases = {
            "decision_not_final": w.h.propose(
                AUDITOR, case_id=w.h.add_case(), outcome="request_further_evidence"
            )["decision_id"],
            "not_an_omission": w.approved("continue_investigation"),
        }
        for code, decision_id in cases.items():
            with self.subTest(code=code), self.assertRaises(rules.ReminderConflict) as caught:
                w.record(decision_id=decision_id)
            w.assert_code(caught.exception, code)
        self.assertEqual(w.record(decision_id=w.approved("request_further_evidence"))["status"],
                         "active")  # fmt: skip
        self.assertIn(("reminder.record.denied", "not_an_omission"), w.actions())

    def test_decision_in_another_tenant_is_not_found(self):
        w = World()
        with self.assertRaises(rules.ReminderNotFound):
            w.record(AUDITOR_T2)
        with self.assertRaises(rules.ReminderNotFound):
            w.record(decision_id=str(uuid.uuid4()))

    def test_one_reminder_per_decision_and_topic(self):
        w = World()
        w.record()
        with self.assertRaises(rules.ReminderConflict) as caught:
            w.record()
        w.assert_code(caught.exception, "reminder_exists")
        self.assertEqual(w.record(topic="contract_documents")["topic"], "contract_documents")

    def test_invalid_input_is_refused_before_anything_is_written(self):
        w = World()
        for body, code in (
            ({"topic": "penalty_warning"}, "unknown_topic"),
            ({"explanation": "Too short."}, "invalid_length"),
            ({"explanation": "x" * (rules.EXPLANATION_MAX + 1)}, "invalid_length"),
            ({"decision_id": "not-a-uuid"}, "invalid_uuid"),
        ):
            with self.subTest(body=body), self.assertRaises(InvalidMapping) as caught:
                w.record(AUDITOR, **body)
            self.assertIn(caught.exception.field_errors[0]["code"], {code, "invalid"})
        self.assertEqual(w.reminders.rows, {})


class TaxpayerScopeTests(unittest.TestCase):
    def test_no_cross_taxpayer_or_cross_tenant_history(self):
        w = World()
        w.record()
        other_case = w.h.add_case(taxpayer=OTHER_TAXPAYER)
        other_evidence = str(uuid.uuid4())
        w.h.repo.evidence[other_evidence] = (T1, OTHER_TAXPAYER)
        other = w.approved(
            "close_after_correction", case_id=other_case, evidence_ids=[other_evidence]
        )
        w.record(decision_id=other,
                 topic="payment_details",
                 explanation="Add the amount you paid for every purchase invoice you list.")  # fmt: skip
        mine = w.portal([(T1, TAXPAYER)])
        theirs = w.portal([(T1, OTHER_TAXPAYER)])
        self.assertEqual([r["topic"] for r in mine], ["missing_invoices"])
        self.assertEqual([r["topic"] for r in theirs], ["payment_details"])
        self.assertEqual(w.portal([(T2, TAXPAYER)]), [])
        self.assertNotIn(theirs[0]["reminder_id"], {r["reminder_id"] for r in mine})

    def test_taxpayer_cannot_read_the_staff_history(self):
        w = World()
        w.record()
        with self.assertRaises(policy.ActionDenied):
            service.staff_list(w.ctx(TAXPAYER_SCOPE), w.ports, taxpayer_id=TAXPAYER)
        self.assertIn(("reminder.read.denied", "forbidden"), w.actions())

    def test_one_reminder_per_topic_newest_wording_first(self):
        w = World()
        w.record()
        second = w.approved("request_further_evidence")
        service.record(
            w.ctx(AUDITOR, minutes=10),
            w.ports,
            body={
                "decision_id": second,
                "topic": "missing_invoices",
                "explanation": "Your June claim also left out purchase invoices; list them all.",
            },
            idempotency_key=idem(),
        )
        shown = w.portal()
        self.assertEqual(len(shown), 1)
        self.assertIn("June", shown[0]["explanation"])


class CorrectionTests(unittest.TestCase):
    def test_correcting_keeps_the_earlier_wording_for_staff_only(self):
        w = World()
        first = w.record()
        corrected = w.correct(
            first, explanation="Your February claim listed fewer purchase invoices than it claimed."
        )
        self.assertEqual(corrected["version"], 2)
        self.assertEqual(corrected["history"][0]["explanation"], EXPLANATION)
        self.assertEqual(corrected["history"][0]["reason"], REASON)
        self.assertEqual(corrected["updated_by"], AUDITOR.user)
        [shown] = w.portal()
        self.assertIn("February", shown["explanation"])
        self.assertEqual(shown["updated_on"], NOW.date().isoformat())
        self.assertNotIn("history", shown)
        self.assertIn(("reminder.corrected", "missing_invoices"), w.actions())

    def test_topic_can_be_corrected_unless_it_clashes(self):
        w = World()
        first = w.record()
        w.record(topic="contract_documents")
        with self.assertRaises(rules.ReminderConflict) as caught:
            w.correct(first, topic="contract_documents")
        w.assert_code(caught.exception, "reminder_exists")
        moved = w.correct(first, topic="invoice_totals")
        self.assertEqual(moved["topic"], "invoice_totals")
        self.assertEqual(moved["prompt_codes"], ["invoice_total_mismatch"])

    def test_withdrawn_reminder_is_hidden_and_final(self):
        w = World()
        first = w.record()
        withdrawn = w.correct(first, action="withdraw")
        self.assertEqual(withdrawn["status"], "withdrawn")
        self.assertFalse(withdrawn["visible_to_taxpayer"])
        self.assertEqual(w.portal(), [])
        self.assertIn(("reminder.withdrawn", "withdrawn"), w.actions())
        with self.assertRaises(rules.ReminderConflict) as caught:
            w.correct(withdrawn, explanation="Reopen this reminder with new wording please.")
        w.assert_code(caught.exception, "not_active")

    def test_refusals_are_recorded_and_change_nothing(self):
        w = World()
        first = w.record()
        for kw, code in (
            ({"expected_version": 7}, "stale_version"),
            ({"explanation": EXPLANATION}, "no_change"),
            ({"reason": "too short"}, "invalid_length"),
            ({"action": "delete"}, "unknown_action"),
            ({"expected_version": "1"}, "required"),
        ):
            with self.subTest(kw=kw), self.assertRaises(MappingError) as caught:
                w.correct(first, **kw)
            w.assert_code(caught.exception, code)
        self.assertEqual(w.reminders.rows[first["reminder_id"]].version, 1)
        denied = [o for a, o in w.actions() if a == "reminder.correct.denied"]
        self.assertEqual(len(denied), 5)

    def test_only_auditor_or_supervisor_corrects(self):
        w = World()
        first = w.record()
        for who in (TAXPAYER_SCOPE, SPECIALIST, SERVICE):
            with self.subTest(who=who.user), self.assertRaises(policy.ActionDenied):
                w.correct(first, who=who, action="withdraw")
        self.assertEqual(w.correct(first, who=SUPERVISOR_2, action="withdraw")["status"],
                         "withdrawn")  # fmt: skip

    def test_reminder_in_another_tenant_is_not_found(self):
        w = World()
        first = w.record()
        with self.assertRaises(rules.ReminderNotFound):
            w.correct(first, who=AUDITOR_T2, action="withdraw")


class ChallengedSourceTests(unittest.TestCase):
    def test_hidden_while_challenged_and_shown_again_when_upheld(self):
        w = World()
        w.record()
        objection = w.object()
        self.assertEqual(w.portal(), [])
        staff = service.staff_list(w.ctx(AUDITOR), w.ports, taxpayer_id=TAXPAYER)
        self.assertEqual(staff["reminders"][0]["source_state"], "under_review")
        w.review_challenge(objection, "upheld")
        self.assertEqual(len(w.portal()), 1)

    def test_hidden_after_overturned_and_cannot_be_recorded_from_it(self):
        w = World()
        w.record()
        w.review_challenge(w.object(), "partially_overturned")
        self.assertEqual(w.portal(), [])
        with self.assertRaises(rules.ReminderConflict) as caught:
            w.record(topic="payment_details")
        w.assert_code(caught.exception, "source_overturned")

    def test_cannot_record_while_the_decision_is_challenged(self):
        w = World()
        w.object()
        with self.assertRaises(rules.ReminderConflict) as caught:
            w.record()
        w.assert_code(caught.exception, "source_under_review")


class StaffListTests(unittest.TestCase):
    def test_staff_see_history_and_visibility_and_the_read_is_recorded(self):
        w = World()
        first = w.record()
        w.correct(first, action="withdraw")
        w.record(topic="payment_details", explanation="Add what you paid for each invoice listed.")
        view = service.staff_list(w.ctx(SPECIALIST), w.ports, taxpayer_id=TAXPAYER)
        by_topic = {r["topic"]: r for r in view["reminders"]}
        self.assertFalse(by_topic["missing_invoices"]["visible_to_taxpayer"])
        self.assertEqual(len(by_topic["missing_invoices"]["history"]), 1)
        self.assertTrue(by_topic["payment_details"]["visible_to_taxpayer"])
        self.assertIn(("reminder.read", "read"), w.actions())

    def test_other_tenant_staff_see_nothing(self):
        w = World()
        w.record()
        view = service.staff_list(w.ctx(AUDITOR_T2), w.ports, taxpayer_id=TAXPAYER)
        self.assertEqual(view["reminders"], [])


class PromptTests(unittest.TestCase):
    def test_annotate_marks_only_prompts_a_reminder_covers(self):
        w = World()
        w.record(topic="payment_details", explanation="Add what you paid for each invoice listed.")
        reminders = w.portal()
        prompts = [
            {"code": "payment_evidence_missing", "invoice": 0, "message": "a"},
            {"code": "payment_total_differs_from_invoice", "invoice": 1, "message": "b"},
            {"code": "contract_evidence_missing", "invoice": 0, "message": "c"},
        ]
        marked = rules.annotate_prompts(prompts, reminders)
        rid = reminders[0]["reminder_id"]
        self.assertEqual([p["reminder_id"] for p in marked], [rid, rid, None])
        self.assertEqual([p["message"] for p in marked], ["a", "b", "c"])
        self.assertEqual(
            rules.annotate_prompts(prompts, []), [{**p, "reminder_id": None} for p in prompts]
        )

    def test_every_topic_maps_to_a_known_presubmission_prompt(self):
        from vati_tax.portal import presubmission

        known = set(presubmission.CLAIM_PROMPTS) | set(presubmission.INVOICE_PROMPTS)
        for name, topic in rules.TOPICS.items():
            with self.subTest(topic=name):
                self.assertTrue(topic.prompt_codes)
                self.assertLessEqual(set(topic.prompt_codes), known)


if __name__ == "__main__":
    unittest.main()
