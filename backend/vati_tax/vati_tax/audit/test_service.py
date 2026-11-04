"""Frappe-free tests for ledger access, overrides and refusals (TAX-059).

Covers the task's verification: an unauthorised override is refused and
recorded, the audit history cannot be changed unnoticed, and only supervisors
of the tenant read the ledger. The decision tests run the TAX-046 engine with
the same in-memory ledger it seals into.
"""

from __future__ import annotations

import datetime
import unittest
import uuid
from dataclasses import replace
from typing import Any

from vati_tax.audit import ledger, service
from vati_tax.audit.ledger import MemoryLedger
from vati_tax.decisions import engine
from vati_tax.decisions.test_engine import NOW, T1, T2, Harness, key
from vati_tax.identity import policy
from vati_tax.ids.canonical import IdempotencyConflict, InvalidMapping


def scope(user: str, role: policy.Role, tenant: str = T1) -> policy.TenantScope:
    taxpayers = frozenset({"80015243-dd34-4e97-b085-7356ffdd042f"}) if role == "taxpayer" else None
    return policy.TenantScope(
        user=user, tenant_id=tenant, roles=frozenset({role}), taxpayer_ids=taxpayers or frozenset()
    )


AUDITOR = scope("auditor@example.test", policy.AUDITOR)
SPECIALIST = scope("specialist@example.test", policy.SPECIALIST)
SUPERVISOR = scope("supervisor@example.test", policy.SUPERVISOR)
SUPERVISOR_2 = scope("supervisor2@example.test", policy.SUPERVISOR)
SERVICE = scope("service@example.test", policy.SERVICE)
TAXPAYER = scope("taxpayer@example.test", policy.TAXPAYER)
SUPERVISOR_T2 = scope("supervisor@example.test", policy.SUPERVISOR, T2)
CASE = "20000000-0000-4000-8000-000000000001"
REASON = "Assigned officer is on leave; reassigned so the evidence deadline is kept."


def ctx(who: policy.TenantScope, now: datetime.datetime = NOW) -> service.Context:
    return service.Context.build(who, None, now)


def override_body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "control": "case_assignment",
        "subject_type": "case",
        "subject_id": CASE,
        "case_id": CASE,
        "reason": REASON,
        "target_actor": "auditor@example.test",
    }
    body.update(changes)
    return body


class OverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryLedger()

    def override(self, who: policy.TenantScope = SUPERVISOR, **changes: Any) -> dict[str, Any]:
        return service.record_override(
            ctx(who), self.store, body=override_body(**changes), idempotency_key=key()
        )

    def test_supervisor_override_is_recorded_with_reason_and_target(self) -> None:
        view = self.override()
        self.assertFalse(view["replayed"])
        self.assertEqual(view["category"], "override")
        self.assertEqual(view["action"], "override.case_assignment")
        self.assertEqual(view["actor"], SUPERVISOR.user)
        self.assertEqual(view["target_actor"], "auditor@example.test")
        self.assertEqual(view["reason"], REASON)
        self.assertEqual(view["chain_seq"], 1)

    def test_unauthorised_roles_are_refused_and_the_attempt_is_recorded(self) -> None:
        for who in (AUDITOR, SPECIALIST, SERVICE, TAXPAYER):
            with self.subTest(role=sorted(who.roles)[0]):
                before = len(self.store.rows)
                with self.assertRaises(policy.ActionDenied):
                    self.override(who)
                [denied] = self.store.rows[before:]
                self.assertEqual(denied.category, "denied")
                self.assertEqual(denied.action, "override.record.denied")
                self.assertEqual(denied.actor, who.user)
        self.assertFalse(any(e.category == "override" for e in self.store.rows))

    def test_nobody_overrides_a_control_on_their_own_work(self) -> None:
        with self.assertRaises(policy.ActionDenied):
            self.override(target_actor="SUPERVISOR@example.test")
        [denied] = self.store.rows
        self.assertEqual(denied.action, "override.record.denied")
        self.assertEqual(denied.target_actor, "SUPERVISOR@example.test")

    def test_invalid_overrides_are_refused_before_anything_is_recorded(self) -> None:
        cases: dict[str, dict[str, Any]] = {
            "unknown_control": {"control": "assessment"},
            "short_reason": {"reason": "because"},
            "blank_reason": {"reason": "   "},
            "bad_subject": {"subject_type": "taxpayer"},
            "bad_subject_id": {"subject_id": "not-a-uuid"},
        }
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(InvalidMapping):
                self.override(SUPERVISOR, **change)
        self.assertEqual(self.store.rows, [])

    def test_replay_returns_the_stored_override_and_a_changed_body_conflicts(self) -> None:
        idem = key()
        first = service.record_override(
            ctx(SUPERVISOR), self.store, body=override_body(), idempotency_key=idem
        )
        again = service.record_override(
            ctx(SUPERVISOR), self.store, body=override_body(), idempotency_key=idem
        )
        self.assertTrue(again["replayed"])
        self.assertEqual(again["event_id"], first["event_id"])
        self.assertEqual(len(self.store.rows), 1)
        with self.assertRaises(IdempotencyConflict):
            service.record_override(
                ctx(SUPERVISOR),
                self.store,
                body=override_body(reason=REASON + " Updated."),
                idempotency_key=idem,
            )


class LedgerAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryLedger()
        service.record_override(
            ctx(SUPERVISOR), self.store, body=override_body(), idempotency_key=key()
        )
        service.record_read(
            ctx(AUDITOR),
            self.store,
            action="case.read",
            subject_type="case",
            subject_id=CASE,
            case_id=CASE,
        )

    def test_only_supervisors_read_the_ledger_and_refusals_are_recorded(self) -> None:
        reads = (
            lambda c: service.ledger_entries(c, self.store),
            lambda c: service.verify_ledger(c, self.store),
            lambda c: service.review_signals(c, self.store),
            lambda c: service.ledger_summary(c, self.store, since=NOW, until=NOW),
        )
        for who in (AUDITOR, SPECIALIST, SERVICE, TAXPAYER):
            for read in reads:
                before = len(self.store.rows)
                with self.assertRaises(policy.ActionDenied):
                    read(ctx(who))
                [denied] = self.store.rows[before:]
                self.assertEqual(denied.action, "ledger.read.denied")

    def test_supervisor_reads_entries_and_the_read_itself_is_recorded(self) -> None:
        result = service.ledger_entries(ctx(SUPERVISOR_2), self.store)
        self.assertEqual(
            [e["action"] for e in result["entries"]], ["override.case_assignment", "case.read"]
        )
        self.assertEqual(result["chain_head"]["chain_seq"], 2)
        last = self.store.rows[-1]
        self.assertEqual((last.action, last.actor), ("ledger.read", SUPERVISOR_2.user))

    def test_filters_and_paging(self) -> None:
        reads = service.ledger_entries(ctx(SUPERVISOR), self.store, category="read")
        self.assertEqual([e["action"] for e in reads["entries"]], ["case.read"])
        page = service.ledger_entries(ctx(SUPERVISOR), self.store, limit=1)
        self.assertEqual(len(page["entries"]), 1)
        self.assertEqual(page["next_since_seq"], 1)
        rest = service.ledger_entries(ctx(SUPERVISOR), self.store, since_seq=1, limit=1)
        self.assertEqual(rest["entries"][0]["chain_seq"], 2)
        with self.assertRaises(InvalidMapping):
            service.ledger_entries(ctx(SUPERVISOR), self.store, category="everything")
        with self.assertRaises(InvalidMapping):
            service.ledger_entries(ctx(SUPERVISOR), self.store, limit=10_000)

    def test_a_supervisor_in_another_tenant_sees_nothing_of_this_tenant(self) -> None:
        result = service.ledger_entries(ctx(SUPERVISOR_T2), self.store)
        self.assertEqual(result["entries"], [])
        self.assertTrue(all(e.tenant_id == T1 for e in self.store.entries(T1)))
        self.assertEqual([e.action for e in self.store.entries(T2)], ["ledger.read"])

    def test_verify_reports_an_edited_history(self) -> None:
        self.assertTrue(service.verify_ledger(ctx(SUPERVISOR), self.store)["intact"])
        self.store.rows[0] = replace(self.store.rows[0], reason="Edited after the fact.")
        report = service.verify_ledger(ctx(SUPERVISOR), self.store)
        self.assertFalse(report["intact"])
        self.assertEqual(report["problems"][0], {"chain_seq": 1, "code": "hash_mismatch"})

    def test_signals_are_review_only_and_withheld_from_their_subject(self) -> None:
        for _ in range(2):
            service.record_override(
                ctx(SUPERVISOR), self.store, body=override_body(), idempotency_key=key()
            )
        other = service.review_signals(ctx(SUPERVISOR_2), self.store)
        self.assertEqual([s["kind"] for s in other["signals"]], ["repeated_overrides"])
        own = service.review_signals(ctx(SUPERVISOR), self.store)
        self.assertEqual(own["signals"], [])
        self.assertEqual(own["withheld_about_viewer"], 1)

    def test_summary_counts_measured_entries(self) -> None:
        result = service.ledger_summary(
            ctx(SUPERVISOR), self.store, since=NOW - datetime.timedelta(hours=1), until=NOW
        )
        self.assertEqual(result["by_category"]["override"], 1)
        self.assertEqual(result["by_category"]["read"], 1)
        self.assertTrue(result["ledger_intact"])


class DecisionLedgerTests(unittest.TestCase):
    """TAX-046 decisions seal every event into the tenant chain."""

    def setUp(self) -> None:
        self.h = Harness()

    def guarded_review(self, decision_id: str, who: policy.TenantScope) -> dict[str, Any]:
        return service.guarded(
            ctx(who),
            self.h.repo.ledger,
            attempted_action=policy.DECISION_APPROVE,
            subject_type="decision",
            subject_id=decision_id,
            fn=lambda: self.h.review(decision_id, who=who),
        )

    def test_decision_events_form_an_intact_chain_with_reviewer_identity(self) -> None:
        proposal = self.h.propose()
        self.h.review(proposal["decision_id"], who=SUPERVISOR)
        entries = self.h.repo.ledger.entries(T1)
        self.assertEqual(
            [(e.action, e.category) for e in entries],
            [
                ("decision.proposed", "write"),
                ("decision.approved", "approval"),
                ("decision.executed", "write"),
            ],
        )
        approval = entries[1]
        self.assertEqual((approval.actor, approval.actor_role), (SUPERVISOR.user, "supervisor"))
        self.assertTrue(ledger.verify_chain(entries)["intact"])

    def test_self_approval_is_refused_recorded_and_signalled_when_repeated(self) -> None:
        for _ in range(2):
            proposal = self.h.propose(who=SUPERVISOR, idem=key())
            with self.assertRaises(policy.ActionDenied):
                self.guarded_review(proposal["decision_id"], SUPERVISOR)
            self.h.review(proposal["decision_id"], action="withdraw", who=SUPERVISOR)
        denied = [e for e in self.h.repo.ledger.entries(T1) if e.category == "denied"]
        self.assertEqual([e.action for e in denied], ["decision.approve.denied"] * 2)
        self.assertFalse(any(d.status == "approved" for d in self.h.repo.decisions.values()))
        signals = service.review_signals(ctx(SUPERVISOR_2), self.h.repo.ledger)
        self.assertEqual(
            [s["kind"] for s in signals["signals"]], ["repeated_separation_of_duties_attempts"]
        )

    def test_a_rolled_back_decision_leaves_no_ledger_entry(self) -> None:
        repo = self.h.repo
        with self.assertRaises(RuntimeError), repo.atomic():
            repo.append_event(
                engine.AuditEvent(
                    event_id=str(uuid.uuid4()),
                    tenant_id=T1,
                    case_id=self.h.case_id,
                    taxpayer_id=None,
                    sequence=None,
                    actor="auditor@example.test",
                    actor_role="auditor",
                    action="decision.executed",
                    subject_type="decision",
                    subject_id=str(uuid.uuid4()),
                    occurred_at=NOW,
                    correlation_id=str(uuid.uuid4()),
                    payload_hash="0" * 64,
                    outcome="changed",
                )
            )
            raise RuntimeError("rollback")
        self.assertEqual(repo.ledger.entries(T1), [])


if __name__ == "__main__":
    unittest.main()
