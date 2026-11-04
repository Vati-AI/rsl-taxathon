"""Frappe-free tests for decisions, approvals and evidence requests (TAX-046).

``MemoryRepository`` has the same unique keys as the Frappe DocTypes; ``FakeLifecycle``
behaves like ``JubeCaseAdapter`` (replay when Jube already holds the target,
``version_conflict`` when the case moved). tests/integration/test_decision_execution.py
runs the same flow against the real adapter and its Jube fake.
"""

from __future__ import annotations

import datetime
import unittest
import uuid
from dataclasses import dataclass, field
from typing import Any

from vati_tax.decisions import engine, rules
from vati_tax.decisions.rules import DecisionNotFound, DecisionRefused, VersionConflict
from vati_tax.identity import policy
from vati_tax.ids.canonical import CaseNotFound, IdempotencyConflict, InvalidMapping

T1 = "10000000-0000-4000-8000-000000000001"
T2 = "10000000-0000-4000-8000-000000000002"
TAXPAYER = "80015243-dd34-4e97-b085-7356ffdd042f"
OTHER_TAXPAYER = "80015243-dd34-4e97-b085-7356ffdd0430"
FINDING = "50000000-0000-4000-8000-000000000001"
NOW = datetime.datetime(2026, 11, 4, 10, 0, tzinfo=datetime.UTC)


def scope(user: str, role: policy.Role, tenant: str = T1) -> policy.TenantScope:
    return policy.TenantScope(
        user=user, tenant_id=tenant, roles=frozenset({role}), taxpayer_ids=frozenset()
    )


AUDITOR = scope("auditor@example.test", policy.AUDITOR)
SPECIALIST = scope("specialist@example.test", policy.SPECIALIST)
SUPERVISOR = scope("supervisor@example.test", policy.SUPERVISOR)
SUPERVISOR_2 = scope("supervisor2@example.test", policy.SUPERVISOR)
SERVICE = scope("service@example.test", policy.SERVICE)
TAXPAYER_SCOPE = policy.TenantScope(
    user="taxpayer@example.test",
    tenant_id=T1,
    roles=frozenset({policy.TAXPAYER}),
    taxpayer_ids=frozenset({TAXPAYER}),
)
AUDITOR_T2 = scope("auditor@example.test", policy.AUDITOR, T2)


class UpstreamError(Exception):
    def __init__(self, code: str, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass
class FakeCase:
    status: str
    escalated: bool = False


@dataclass
class FakeOutcome:
    case: FakeCase
    changed: bool


@dataclass
class FakeLifecycle:
    cases: dict[str, FakeCase] = field(default_factory=dict)
    calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    fail: list[Exception] = field(default_factory=list)

    def get_case(self, tenant_id: str, case_id: str, *, correlation_id: str) -> FakeCase:
        if case_id not in self.cases:
            raise UpstreamError("not_found", False)
        return FakeCase(self.cases[case_id].status, self.cases[case_id].escalated)

    def _move(self, case_id: str, target: str, escalated: bool, expected: str) -> FakeOutcome:
        if self.fail:
            raise self.fail.pop(0)
        case = self.cases[case_id]
        if case.status == target and case.escalated == escalated:
            return FakeOutcome(FakeCase(case.status, case.escalated), changed=False)
        if case.status != expected:
            raise UpstreamError("version_conflict", False)
        case.status, case.escalated = target, escalated
        return FakeOutcome(FakeCase(case.status, case.escalated), changed=True)

    def transition(
        self,
        tenant_id,
        case_id,
        target_status,
        *,
        expected_status,
        correlation_id,
        decision_id=None,
        rating=None,
    ):
        self.calls.append(
            (
                "transition",
                case_id,
                {
                    "target": target_status,
                    "expected": expected_status,
                    "decision_id": decision_id,
                    "rating": rating,
                },
            )
        )
        if target_status == "RESOLVED" and decision_id is None:
            raise AssertionError("RESOLVED needs a decision_id")
        return self._move(case_id, target_status, False, expected_status)

    def escalate(self, tenant_id, case_id, *, expected_status, correlation_id, rating=None):
        self.calls.append(("escalate", case_id, {"expected": expected_status, "rating": rating}))
        return self._move(case_id, "AWAITING_HUMAN", True, expected_status)


def key() -> str:
    return "synthetic-" + uuid.uuid4().hex


class Harness:
    def __init__(self) -> None:
        self.repo = engine.MemoryRepository()
        self.jube = FakeLifecycle()
        self.now = NOW
        self.case_id = self.add_case()
        self.evidence_id = str(uuid.uuid4())
        self.repo.evidence[self.evidence_id] = (T1, TAXPAYER)

    def add_case(
        self, tenant: str = T1, status: str = "AWAITING_HUMAN", taxpayer: str | None = TAXPAYER
    ) -> str:
        case_id = str(uuid.uuid4())
        self.repo.cases[(tenant, case_id)] = engine.CaseRef(tenant, case_id, taxpayer)
        self.jube.cases[case_id] = FakeCase(status)
        return case_id

    def ctx(self, who: policy.TenantScope) -> engine.Context:
        return engine.Context.build(who, None, self.now)

    def version(self, case_id: str | None = None) -> int:
        return self.repo.case_version(T1, case_id or self.case_id)

    def propose(
        self,
        who: policy.TenantScope = AUDITOR,
        idem: str | None = None,
        case_id: str | None = None,
        **overrides: Any,
    ) -> dict[str, Any]:
        case_id = case_id or self.case_id
        body = {
            "outcome": "close_no_further_action",
            "rationale": "Invoices reconcile with the declared input VAT for the period.",
            "evidence_ids": [self.evidence_id],
            "finding_ids": [],
            "rating": 3,
            "expected_case_status": self.jube.cases[case_id].status,
            "expected_version": self.version(case_id),
        }
        body.update(overrides)
        return engine.propose(
            self.ctx(who),
            self.repo,
            self.jube,
            case_id=case_id,
            body=body,
            idempotency_key=idem or key(),
        )

    def review(
        self,
        decision_id: str,
        action: str = "approve",
        who: policy.TenantScope = SUPERVISOR,
        idem: str | None = None,
        **overrides: Any,
    ) -> dict[str, Any]:
        record = self.repo.decisions[decision_id]
        body: dict[str, Any] = {"expected_version": self.version(record.case_id)}
        if action == "reject":
            body["reason"] = "The cited invoices do not cover the whole period."
        body.update(overrides)
        return engine.review(
            self.ctx(who),
            self.repo,
            self.jube,
            action=action,  # type: ignore[arg-type]
            decision_id=decision_id,
            body=body,
            idempotency_key=idem or key(),
        )


class ProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()

    def test_officer_proposal_is_recorded_with_an_audit_event(self) -> None:
        view = self.h.propose()
        self.assertEqual(view["status"], "proposed")
        self.assertEqual(view["origin"], "officer")
        self.assertEqual(view["case_status_at_proposal"], "AWAITING_HUMAN")
        self.assertEqual(view["jube_sync"]["state"], "not_started")
        self.assertEqual(self.h.jube.calls, [])  # nothing outward before approval
        [event] = self.h.repo.case_events(T1, self.h.case_id)
        self.assertEqual(
            (event.action, event.sequence, event.actor_role), ("decision.proposed", 1, "auditor")
        )
        self.assertEqual(self.h.version(), 2)

    def test_replay_returns_the_same_decision_and_changed_payload_conflicts(self) -> None:
        idem = key()
        first = self.h.propose(idem=idem)
        again = self.h.propose(idem=idem, expected_version=1)
        self.assertEqual(again["decision_id"], first["decision_id"])
        self.assertTrue(again["replayed"])
        self.assertEqual(len(self.h.repo.decisions), 1)
        with self.assertRaises(IdempotencyConflict):
            self.h.propose(idem=idem, expected_version=1, rating=4)

    def test_service_identity_cannot_propose(self) -> None:
        with self.assertRaises(policy.ActionDenied):
            self.h.propose(who=SERVICE)
        self.assertEqual(self.h.repo.decisions, {})

    def test_taxpayer_cannot_propose(self) -> None:
        with self.assertRaises(policy.ActionDenied):
            self.h.propose(who=TAXPAYER_SCOPE)

    def test_other_tenant_case_is_not_found(self) -> None:
        other = self.h.add_case(tenant=T2)
        with self.assertRaises(CaseNotFound):
            self.h.propose(case_id=other, expected_version=1)
        with self.assertRaises(CaseNotFound):
            engine.propose(
                self.h.ctx(AUDITOR_T2),
                self.h.repo,
                self.h.jube,
                case_id=self.h.case_id,
                body={
                    "outcome": "continue_investigation",
                    "rationale": "Need to compare the import declarations first.",
                    "rating": 2,
                    "expected_case_status": "AWAITING_HUMAN",
                    "expected_version": 1,
                },
                idempotency_key=key(),
            )

    def test_evidence_from_another_taxpayer_cannot_be_cited(self) -> None:
        foreign = str(uuid.uuid4())
        self.h.repo.evidence[foreign] = (T1, OTHER_TAXPAYER)
        with self.assertRaises(CaseNotFound):
            self.h.propose(evidence_ids=[foreign])

    def test_closing_needs_a_citation(self) -> None:
        with self.assertRaises(DecisionRefused) as ctx:
            self.h.propose(evidence_ids=[], finding_ids=[])
        self.assertEqual(ctx.exception.field_errors[0]["code"], "citation_required")

    def test_prohibited_and_unknown_outcomes_are_refused(self) -> None:
        for outcome in (
            "approve_refund",
            "issue_assessment",
            "refer_for_prosecution",
            "fraud_finding",
            "pay_refund",
        ):
            with self.assertRaises(DecisionRefused, msg=outcome) as ctx:
                self.h.propose(outcome=outcome)
            self.assertEqual(ctx.exception.field_errors[0]["code"], "prohibited_outcome")
        with self.assertRaises(InvalidMapping):
            self.h.propose(outcome="something_else")

    def test_status_seen_by_officer_must_match_jube(self) -> None:
        with self.assertRaises(VersionConflict) as ctx:
            self.h.propose(expected_case_status="INVESTIGATING")
        self.assertEqual(ctx.exception.field_errors[0]["code"], "case_state_changed")

    def test_wrong_expected_version_is_stale(self) -> None:
        with self.assertRaises(VersionConflict) as ctx:
            self.h.propose(expected_version=5)
        self.assertEqual(ctx.exception.current_version, 1)

    def test_one_open_decision_per_case(self) -> None:
        self.h.propose()
        with self.assertRaises(VersionConflict) as ctx:
            self.h.propose(who=SPECIALIST)
        self.assertEqual(ctx.exception.field_errors[0]["code"], "decision_pending")

    def test_no_change_and_closed_cases_are_refused(self) -> None:
        investigating = self.h.add_case(status="INVESTIGATING")
        with self.assertRaises(DecisionRefused):
            self.h.propose(case_id=investigating, outcome="continue_investigation")
        resolved = self.h.add_case(status="RESOLVED")
        with self.assertRaises(DecisionRefused) as ctx:
            self.h.propose(case_id=resolved)
        self.assertEqual(ctx.exception.field_errors[0]["code"], "case_closed")

    def test_jube_unreachable_records_nothing(self) -> None:
        def down(*_a: Any, **_k: Any) -> Any:
            raise ConnectionRefusedError("synthetic outage")

        self.h.jube.get_case = down  # type: ignore[method-assign]
        with self.assertRaises(rules.UpstreamUnavailable):
            self.h.propose()
        self.assertEqual(self.h.repo.decisions, {})
        self.assertEqual(self.h.repo.events, [])


class ApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()

    def test_approval_executes_the_transition_once(self) -> None:
        proposed = self.h.propose()
        approved = self.h.review(proposed["decision_id"])
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["jube_sync"]["state"], "synced")
        self.assertTrue(approved["jube_sync"]["changed"])
        self.assertEqual(approved["jube_sync"]["status_after"], "RESOLVED")
        [call] = self.h.jube.calls
        self.assertEqual(
            call[2],
            {
                "target": "RESOLVED",
                "expected": "AWAITING_HUMAN",
                "decision_id": proposed["decision_id"],
                "rating": 3,
            },
        )
        actions = [e.action for e in self.h.repo.case_events(T1, self.h.case_id)]
        self.assertEqual(actions, ["decision.proposed", "decision.approved", "decision.executed"])
        self.assertIsNone(self.h.repo.open_decision(T1, self.h.case_id))

    def test_replayed_approval_sends_nothing_again(self) -> None:
        proposed = self.h.propose()
        idem = key()
        first = self.h.review(proposed["decision_id"], idem=idem)
        again = self.h.review(proposed["decision_id"], idem=idem, expected_version=2)
        self.assertTrue(again["replayed"])
        self.assertEqual(again["jube_sync"], first["jube_sync"])
        self.assertEqual(len(self.h.jube.calls), 1)
        with self.assertRaises(IdempotencyConflict):
            self.h.review(proposed["decision_id"], idem=idem, expected_version=3)

    def test_second_approval_is_refused(self) -> None:
        proposed = self.h.propose()
        self.h.review(proposed["decision_id"])
        with self.assertRaises(VersionConflict) as ctx:
            self.h.review(proposed["decision_id"], who=SUPERVISOR_2)
        self.assertEqual(ctx.exception.field_errors[0]["code"], "not_pending")
        self.assertEqual(len(self.h.jube.calls), 1)

    def test_proposer_cannot_approve_own_decision(self) -> None:
        proposed = self.h.propose(who=SUPERVISOR)
        with self.assertRaises(policy.ActionDenied):
            self.h.review(proposed["decision_id"], who=SUPERVISOR)
        self.assertEqual(self.h.jube.calls, [])

    def test_auditor_and_service_cannot_approve(self) -> None:
        proposed = self.h.propose(who=SPECIALIST)
        for who in (AUDITOR, SERVICE):
            with self.assertRaises(policy.ActionDenied):
                self.h.review(proposed["decision_id"], who=who)

    def test_other_tenant_supervisor_gets_not_found(self) -> None:
        proposed = self.h.propose()
        with self.assertRaises(DecisionNotFound):
            self.h.review(proposed["decision_id"], who=scope("s@example.test", "supervisor", T2))

    def test_agent_draft_is_never_approved_or_executed(self) -> None:
        draft = engine.record_draft(
            self.h.ctx(SERVICE),
            self.h.repo,
            case_id=self.h.case_id,
            body={
                "outcome": "close_no_further_action",
                "rationale": "Agent summary: declared and invoiced VAT agree for the period.",
                "evidence_ids": [self.h.evidence_id],
            },
            idempotency_key=key(),
        )
        self.assertEqual((draft["status"], draft["origin"]), ("draft", "agent_draft"))
        with self.assertRaises(DecisionRefused) as ctx:
            self.h.review(draft["decision_id"])
        self.assertEqual(ctx.exception.field_errors[0]["code"], "agent_only_decision")
        with self.assertRaises(DecisionRefused):
            engine.retry_execution(
                self.h.ctx(SUPERVISOR), self.h.repo, self.h.jube, decision_id=draft["decision_id"]
            )
        self.assertEqual(self.h.jube.calls, [])
        # The draft does not move the case version; an officer can still propose.
        self.assertEqual(self.h.version(), 1)
        adopted = self.h.propose(draft_id=draft["decision_id"])
        self.assertEqual(adopted["draft_id"], draft["decision_id"])
        self.assertEqual(adopted["origin"], "officer")

    def test_a_record_claiming_a_service_proposer_is_refused(self) -> None:
        proposed = self.h.propose()
        self.h.repo.decisions[proposed["decision_id"]].proposer_role = "service"
        with self.assertRaises(DecisionRefused):
            self.h.review(proposed["decision_id"])

    def test_stale_version_approval_is_refused(self) -> None:
        proposed = self.h.propose()
        with self.assertRaises(VersionConflict) as ctx:
            self.h.review(proposed["decision_id"], expected_version=1)
        self.assertEqual(ctx.exception.field_errors[0]["code"], "stale_case_version")
        self.assertEqual(self.h.jube.calls, [])

    def test_expired_proposal_is_refused(self) -> None:
        proposed = self.h.propose()
        self.h.now = NOW + rules.MAX_PROPOSAL_AGE + datetime.timedelta(minutes=1)
        with self.assertRaises(VersionConflict) as ctx:
            self.h.review(proposed["decision_id"])
        self.assertEqual(ctx.exception.field_errors[0]["code"], "proposal_expired")

    def test_case_moved_in_jube_since_proposal_is_refused(self) -> None:
        proposed = self.h.propose()
        self.h.jube.cases[self.h.case_id].status = "INVESTIGATING"
        with self.assertRaises(VersionConflict) as ctx:
            self.h.review(proposed["decision_id"])
        self.assertEqual(ctx.exception.field_errors[0]["code"], "case_state_changed")
        self.assertEqual(self.h.repo.decisions[proposed["decision_id"]].status, "proposed")

    def test_reject_and_withdraw_close_the_proposal(self) -> None:
        proposed = self.h.propose()
        with self.assertRaises(InvalidMapping):
            self.h.review(proposed["decision_id"], action="reject", reason="")
        rejected = self.h.review(proposed["decision_id"], action="reject")
        self.assertEqual(rejected["status"], "rejected")
        self.assertIsNone(self.h.repo.open_decision(T1, self.h.case_id))
        again = self.h.propose()
        with self.assertRaises(policy.ActionDenied):
            self.h.review(again["decision_id"], action="withdraw", who=SPECIALIST)
        withdrawn = self.h.review(again["decision_id"], action="withdraw", who=AUDITOR)
        self.assertEqual(withdrawn["status"], "withdrawn")
        self.assertEqual(self.h.jube.calls, [])

    def test_escalation_uses_the_escalate_operation(self) -> None:
        proposed = self.h.propose(outcome="escalate_to_supervisor", evidence_ids=[])
        approved = self.h.review(proposed["decision_id"])
        self.assertEqual(self.h.jube.calls[0][0], "escalate")
        self.assertEqual(approved["jube_sync"]["state"], "synced")


class ExecutionFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.proposed = self.h.propose()

    def test_retryable_failure_keeps_the_approval_and_retry_completes_it(self) -> None:
        self.h.jube.fail.append(UpstreamError("upstream_timeout", True))
        approved = self.h.review(self.proposed["decision_id"])
        sync = approved["jube_sync"]
        self.assertEqual(
            (approved["status"], sync["state"], sync["error_code"], sync["retryable"]),
            ("approved", "failed", "upstream_timeout", True),
        )
        # Still the open decision: nobody can propose a competing one meanwhile.
        self.assertIsNotNone(self.h.repo.open_decision(T1, self.h.case_id))
        with self.assertRaises(policy.ActionDenied):
            engine.retry_execution(
                self.h.ctx(AUDITOR),
                self.h.repo,
                self.h.jube,
                decision_id=self.proposed["decision_id"],
            )
        retried = engine.retry_execution(
            self.h.ctx(SERVICE), self.h.repo, self.h.jube, decision_id=self.proposed["decision_id"]
        )
        self.assertEqual(retried["jube_sync"]["state"], "synced")
        self.assertEqual(retried["jube_sync"]["attempts"], 2)
        again = engine.retry_execution(
            self.h.ctx(SUPERVISOR),
            self.h.repo,
            self.h.jube,
            decision_id=self.proposed["decision_id"],
        )
        self.assertTrue(again["replayed"])
        self.assertEqual(len(self.h.jube.calls), 2)

    def test_lost_response_retry_does_not_apply_twice(self) -> None:
        approved = self.h.review(self.proposed["decision_id"])
        record = self.h.repo.decisions[approved["decision_id"]]
        record.sync_state, record.sync_retryable = (
            "failed",
            True,
        )  # response lost after Jube applied
        record.open_key = rules.open_decision_key(T1, self.h.case_id)
        retried = engine.retry_execution(
            self.h.ctx(SUPERVISOR), self.h.repo, self.h.jube, decision_id=approved["decision_id"]
        )
        self.assertEqual(retried["jube_sync"]["state"], "synced")
        self.assertFalse(retried["jube_sync"]["changed"])

    def test_case_moved_before_execution_is_a_final_failure(self) -> None:
        self.h.jube.fail.append(UpstreamError("version_conflict", False))
        approved = self.h.review(self.proposed["decision_id"])
        self.assertEqual(approved["jube_sync"]["error_code"], "version_conflict")
        self.assertFalse(approved["jube_sync"]["retryable"])
        self.assertIsNone(self.h.repo.open_decision(T1, self.h.case_id))
        with self.assertRaises(VersionConflict):
            engine.retry_execution(
                self.h.ctx(SUPERVISOR),
                self.h.repo,
                self.h.jube,
                decision_id=self.proposed["decision_id"],
            )

    def test_old_approval_is_not_retried(self) -> None:
        self.h.jube.fail.append(UpstreamError("upstream_unavailable", True))
        self.h.review(self.proposed["decision_id"])
        self.h.now = NOW + rules.MAX_APPROVAL_AGE + datetime.timedelta(minutes=1)
        retried = engine.retry_execution(
            self.h.ctx(SUPERVISOR),
            self.h.repo,
            self.h.jube,
            decision_id=self.proposed["decision_id"],
        )
        self.assertEqual(retried["jube_sync"]["error_code"], "approval_expired")
        self.assertEqual(len(self.h.jube.calls), 1)

    def test_programming_errors_are_not_swallowed(self) -> None:
        self.h.jube.fail.append(KeyError("bug"))
        with self.assertRaises(KeyError):
            self.h.review(self.proposed["decision_id"])


class EvidenceRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()

    def body(self, **overrides: Any) -> dict[str, Any]:
        body = {
            "expected_version": self.h.version(),
            "finding_ids": [FINDING],
            "items": [{"document_type": "invoice", "description": "Supplier invoices for Q3."}],
            "due_date": "2026-11-25",
            "message_to_taxpayer": "Please upload the supplier invoices for the period.",
        }
        body.update(overrides)
        return body

    def authorise(
        self,
        who: policy.TenantScope = AUDITOR,
        idem: str | None = None,
        case_id: str | None = None,
        **overrides: Any,
    ) -> dict[str, Any]:
        return engine.authorise_evidence_request(
            self.h.ctx(who),
            self.h.repo,
            case_id=case_id or self.h.case_id,
            body=self.body(**overrides),
            idempotency_key=idem or key(),
        )

    def test_officer_request_is_recorded_and_advances_the_case_version(self) -> None:
        view = self.authorise()
        self.assertEqual(
            (view["status"], view["authorised_by"], view["case_version"]),
            ("recorded", "auditor@example.test", 1),
        )
        self.assertEqual(self.h.version(), 2)
        [event] = self.h.repo.case_events(T1, self.h.case_id)
        self.assertEqual(event.action, "evidence_request.recorded")

    def test_replay_and_conflict(self) -> None:
        idem = key()
        first = self.authorise(idem=idem)
        again = self.authorise(idem=idem, expected_version=1)
        self.assertEqual(again["request_id"], first["request_id"])
        self.assertTrue(again["replayed"])
        with self.assertRaises(IdempotencyConflict):
            self.authorise(idem=idem, expected_version=1, due_date="2026-11-26")

    def test_only_auditors_and_supervisors_authorise(self) -> None:
        self.authorise(who=SUPERVISOR)
        for who in (SPECIALIST, SERVICE, TAXPAYER_SCOPE):
            with self.assertRaises(policy.ActionDenied):
                self.authorise(who=who)

    def test_stale_version_and_bad_input_are_refused(self) -> None:
        self.authorise()
        with self.assertRaises(VersionConflict):
            self.authorise(expected_version=1)
        with self.assertRaises(DecisionRefused):
            self.authorise(due_date="2026-11-04")
        with self.assertRaises(InvalidMapping):
            self.authorise(items=[{"document_type": "selfie", "description": "x"}])
        with self.assertRaises(InvalidMapping):
            self.authorise(finding_ids=[])
        self.assertEqual(len(self.h.repo.requests), 1)

    def test_case_without_taxpayer_or_in_other_tenant(self) -> None:
        orphan = self.h.add_case(taxpayer=None)
        with self.assertRaises(DecisionRefused):
            self.authorise(case_id=orphan, expected_version=1)
        foreign = self.h.add_case(tenant=T2)
        with self.assertRaises(CaseNotFound):
            self.authorise(case_id=foreign, expected_version=1)


class ReviewStateTests(unittest.TestCase):
    def test_trail_lists_every_step_and_needs_case_read(self) -> None:
        h = Harness()
        proposed = h.propose()
        h.review(proposed["decision_id"])
        state = engine.case_review_state(h.ctx(SPECIALIST), h.repo, case_id=h.case_id)
        self.assertEqual(state["case_version"], 3)
        self.assertIsNone(state["open_decision"])
        self.assertEqual([e["sequence"] for e in state["audit_events"]], [1, 2, None])
        with self.assertRaises(policy.ActionDenied):
            engine.case_review_state(h.ctx(TAXPAYER_SCOPE), h.repo, case_id=h.case_id)


if __name__ == "__main__":
    unittest.main()
