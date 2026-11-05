"""Frappe-free tests for learned-workflow approval, versioning and rollback (TAX-065).

Covers the task's verification: self-approval and execution of an unapproved
version are refused, and rollback restores the previous approved version.
Proposals are produced by the real TAX-064 observer where the ``ai`` package is
importable, so the intake is tested against what that module actually emits.
"""

from __future__ import annotations

import datetime
import unittest
import uuid
from typing import Any

from vati_tax.audit import ledger
from vati_tax.audit import service as audit
from vati_tax.audit.ledger import MemoryLedger
from vati_tax.identity import policy
from vati_tax.ids.canonical import IdempotencyConflict, InvalidMapping
from vati_tax.workflows import rules, service

T1 = "10000000-0000-4000-8000-000000000001"
T2 = "10000000-0000-4000-8000-000000000002"
NOW = datetime.datetime(2026, 11, 5, 9, 0, tzinfo=datetime.UTC)
REASON = "Held-out comparison reviewed with the team; sequence matches current practice."
WF = "refund_evidence_then_close"


def scope(user: str, role: policy.Role, tenant: str = T1) -> policy.TenantScope:
    return policy.TenantScope(
        user=user,
        tenant_id=tenant,
        roles=frozenset({role}),
        taxpayer_ids=frozenset({str(uuid.uuid4())}) if role == policy.TAXPAYER else frozenset(),
    )


AUDITOR = scope("auditor@example.test", policy.AUDITOR)
SPECIALIST = scope("specialist@example.test", policy.SPECIALIST)
SUPERVISOR = scope("supervisor@example.test", policy.SUPERVISOR)
SUPERVISOR_2 = scope("supervisor2@example.test", policy.SUPERVISOR)
SERVICE = scope("observer@example.test", policy.SERVICE)
TAXPAYER = scope("taxpayer@example.test", policy.TAXPAYER)
SUPERVISOR_T2 = scope("supervisor@example.test", policy.SUPERVISOR, T2)


def proposal(steps: list[str] | None = None, **changes: Any) -> dict[str, Any]:
    """A TAX-064 ``workflow_proposal`` in the shape ``propose_workflows`` returns."""
    body: dict[str, Any] = {
        "kind": "workflow_proposal",
        "proposal_id": "wfp-" + uuid.uuid4().hex[:16],
        "version": "0.1.0",
        "observer_version": "tax064-observer-v1",
        "status": "proposed",
        "auto_activate": False,
        "approved_by": None,
        "steps": steps or ["request_evidence", "close_no_further_action"],
        "support": {"training_cases": 4, "distinct_approvers": 2, "case_ids": []},
        "evidence_event_ids": [],
        "holdout_evaluation": {"status": "evaluated", "holdout_cases": 3, "coverage": "2/3"},
    }
    body.update(changes)
    return body


def key() -> str:
    return "synthetic-" + uuid.uuid4().hex


class Harness:
    def __init__(self) -> None:
        self.store = service.MemoryWorkflows()
        self.ledger = MemoryLedger()
        self.ports = service.Ports(workflows=self.store, ledger=self.ledger)

    def ctx(self, who: policy.TenantScope) -> audit.Context:
        return audit.Context.build(who, None, NOW)

    def propose(
        self, version: str = "1.0.0", who=SERVICE, idem: str | None = None, **prop: Any
    ) -> dict[str, Any]:
        return service.propose_workflow(
            self.ctx(who),
            self.ports,
            body={
                "workflow": WF,
                "version": version,
                "proposal": prop.pop("body", None) or proposal(**prop),
            },
            idempotency_key=idem or key(),
        )

    def decide(
        self,
        version: str = "1.0.0",
        who=SUPERVISOR,
        approve: bool = True,
        reviewed: object = True,
        expected: int | None = None,
        reason: object = REASON,
    ) -> dict[str, Any]:
        record = self.store.get_version(T1, WF, version)
        return service.decide_workflow(
            self.ctx(who),
            self.ports,
            workflow=WF,
            version=version,
            expected_version=expected
            if expected is not None
            else (record.record_version if record else 1),
            approve=approve,
            reason=reason,
            holdout_reviewed=reviewed,
        )

    def seq(self) -> int:
        current = rules.active(self.store.history(T1, WF))
        return current.seq if current else 0

    def activate(self, version: str, who=SUPERVISOR, expected: int | None = None) -> dict[str, Any]:
        return service.activate_workflow(
            self.ctx(who),
            self.ports,
            workflow=WF,
            version=version,
            expected_seq=self.seq() if expected is None else expected,
            reason=REASON,
        )

    def rollback(self, who=SUPERVISOR, expected: int | None = None) -> dict[str, Any]:
        return service.rollback_workflow(
            self.ctx(who),
            self.ports,
            workflow=WF,
            expected_seq=self.seq() if expected is None else expected,
            reason=REASON,
        )

    def check(self, version: str, who=SERVICE) -> dict[str, Any]:
        return service.check_execution(self.ctx(who), self.ports, workflow=WF, version=version)

    def approved(self, *versions: str) -> None:
        for v in versions:
            self.propose(v)
            self.decide(v)

    def stored(self, version: str) -> rules.WorkflowVersion:
        record = self.store.get_version(T1, WF, version)
        if record is None:
            raise AssertionError("version not stored")
        return record

    def active_version(self) -> str | None:
        current = rules.active(self.store.history(T1, WF))
        return current.version if current else None

    def actions(self) -> list[tuple[str, str, str]]:
        return [(e.action, e.outcome, e.actor) for e in self.ledger.entries(T1)]


class ProposalTests(unittest.TestCase):
    def test_observer_proposal_is_recorded_as_a_proposed_version(self):
        h = Harness()
        view = h.propose()
        self.assertEqual(
            (view["status"], view["active"], view["auto_activate"]), ("proposed", False, False)
        )
        self.assertEqual(view["steps"], ["request_evidence", "close_no_further_action"])
        self.assertEqual(len(view["proposal_digest"]), 64)
        self.assertEqual(view["holdout_evaluation"]["status"], "evaluated")
        self.assertEqual(h.actions(), [("workflow.proposed", "proposed", SERVICE.user)])

    def test_real_tax064_output_is_accepted(self):
        try:
            from ai.workflows.workflow_observation import propose_workflows
        except ImportError:  # pragma: no cover - backend-only environment
            self.skipTest("ai package not importable from here")
        entries = []
        seq = 0
        for n in range(12):
            case = f"20000000-0000-4000-8000-{n:012d}"
            for action, actor in (
                ("evidence_request.recorded", f"syn-auditor-{n % 3}"),
                ("decision.approved", f"syn-supervisor-{n % 2}"),
            ):
                seq += 1
                entries.append(
                    {
                        "event_id": str(uuid.uuid4()),
                        "chain_seq": seq,
                        "action": action,
                        "outcome": "approved",
                        "actor": actor,
                        "case_id": case,
                        "category": "approval",
                        "detail": "close_no_further_action",
                    }
                )
        proposals: list[dict[str, Any]] = propose_workflows(entries, tenant_id=T1)["proposals"]  # type: ignore[assignment]
        self.assertTrue(proposals)
        h = Harness()
        view = h.propose(body=proposals[0])
        self.assertEqual(view["proposal_id"], proposals[0]["proposal_id"])

    def test_activated_or_self_approved_proposals_and_unknown_steps_are_refused(self):
        h = Harness()
        bad: list[dict[str, Any]] = [
            {"status": "approved"},
            {"auto_activate": True},
            {"approved_by": "someone@example.test"},
            {"kind": "something_else"},
            {"proposal_id": "../etc"},
            {"steps": ["request_evidence", "deny_refund"]},
            {"steps": ["request_evidence"]},
            {"holdout_evaluation": None},
        ]
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(InvalidMapping):
                h.propose(**changes)
        for body in ({"workflow": "Bad Name"}, {"version": "1.0"}):
            with self.subTest(body=body), self.assertRaises(InvalidMapping):
                service.propose_workflow(
                    h.ctx(SERVICE),
                    h.ports,
                    body={"workflow": WF, "version": "1.0.0", "proposal": proposal(), **body},
                    idempotency_key=key(),
                )
        self.assertEqual(h.store.rows, {})

    def test_taxpayer_cannot_propose_and_the_attempt_is_recorded(self):
        h = Harness()
        with self.assertRaises(policy.ActionDenied):
            h.propose(who=TAXPAYER)
        self.assertEqual(h.actions(), [("workflow.propose.denied", "forbidden", TAXPAYER.user)])

    def test_replay_returns_the_version_and_different_content_conflicts(self):
        h = Harness()
        body = proposal()
        first = service.propose_workflow(
            h.ctx(SERVICE),
            h.ports,
            body={"workflow": WF, "version": "1.0.0", "proposal": body},
            idempotency_key=key(),
        )
        again = service.propose_workflow(
            h.ctx(SERVICE),
            h.ports,
            body={"workflow": WF, "version": "1.0.0", "proposal": body},
            idempotency_key=key(),
        )
        self.assertTrue(again["replayed"])
        self.assertEqual(again["proposal_digest"], first["proposal_digest"])
        with self.assertRaises(IdempotencyConflict):
            h.propose("1.0.0")


class ApprovalTests(unittest.TestCase):
    def test_a_different_supervisor_approves_with_reason_and_holdout_review(self):
        h = Harness()
        h.propose()
        view = h.decide()
        self.assertEqual(
            (view["status"], view["decided_by"], view["active"]),
            ("approved", SUPERVISOR.user, False),
        )
        self.assertEqual(view["decision_reason"], REASON)
        self.assertEqual(h.ledger.entries(T1)[-1].category, "approval")

    def test_self_approval_is_refused_recorded_and_signalled(self):
        h = Harness()
        h.propose(who=SUPERVISOR)
        for _ in range(2):
            with self.assertRaises(policy.ActionDenied):
                h.decide(who=SUPERVISOR)
        self.assertEqual(h.stored("1.0.0").status, "proposed")
        denied = [a for a in h.actions() if a[0] == "playbook.approve.denied"]
        self.assertEqual(len(denied), 2)
        signals = ledger.integrity_signals(h.ledger.entries(T1), now=NOW, viewer=SUPERVISOR_2.user)[
            "signals"
        ]
        self.assertIn("repeated_separation_of_duties_attempts", [s["kind"] for s in signals])
        self.assertEqual(h.decide(who=SUPERVISOR_2)["status"], "approved")

    def test_officers_and_service_cannot_approve(self):
        for who in (AUDITOR, SPECIALIST, SERVICE):
            h = Harness()
            h.propose(who=AUDITOR if who is not AUDITOR else SPECIALIST)
            with self.subTest(user=who.user), self.assertRaises(policy.ActionDenied):
                h.decide(who=who)

    def test_approval_needs_holdout_review_and_reason_rejection_needs_reason(self):
        h = Harness()
        h.propose()
        with self.assertRaises(InvalidMapping):
            h.decide(reviewed=False)
        with self.assertRaises(InvalidMapping):
            h.decide(reason="ok")
        with self.assertRaises(InvalidMapping):
            h.decide(approve=False, reason=None)
        self.assertEqual(h.decide(approve=False, reviewed=False)["status"], "rejected")

    def test_stale_and_already_decided_versions_are_refused(self):
        h = Harness()
        h.propose()
        with self.assertRaises(rules.WorkflowConflict) as stale:
            h.decide(expected=0)
        self.assertEqual(stale.exception.field_errors[0]["code"], "stale_version")
        h.decide()
        with self.assertRaises(rules.WorkflowConflict) as decided:
            h.decide(who=SUPERVISOR_2)
        self.assertEqual(decided.exception.field_errors[0]["code"], "not_pending")


class ActivationTests(unittest.TestCase):
    def test_unapproved_versions_cannot_be_activated(self):
        h = Harness()
        h.propose("1.0.0")
        h.propose("1.1.0")
        h.decide("1.1.0", approve=False, reviewed=False)
        for version in ("1.0.0", "1.1.0"):
            with self.subTest(version=version), self.assertRaises(rules.WorkflowConflict):
                h.activate(version)
        self.assertEqual(h.store.activations, [])
        self.assertEqual(h.actions()[-1][:2], ("workflow.activate.denied", "not_approved"))

    def test_only_supervisors_activate_never_the_service(self):
        h = Harness()
        h.approved("1.0.0")
        for who in (SERVICE, AUDITOR):
            with self.subTest(user=who.user), self.assertRaises(policy.ActionDenied):
                h.activate("1.0.0", who=who)
        self.assertNotIn(policy.SERVICE, policy.ACTION_ROLES[policy.WORKFLOW_ACTIVATE])
        self.assertIn(policy.WORKFLOW_ACTIVATE, policy.HUMAN_ONLY_ACTIONS)

    def test_activation_history_keeps_rollback_references(self):
        h = Harness()
        h.approved("1.0.0", "1.1.0")
        first = h.activate("1.0.0")
        second = h.activate("1.1.0", who=SUPERVISOR_2)
        self.assertEqual((first["seq"], first["rollback_to"]), (1, None))
        self.assertEqual(
            (second["seq"], second["replaced_version"], second["rollback_to"]),
            (2, "1.0.0", "1.0.0"),
        )
        with self.assertRaises(rules.WorkflowConflict) as again:
            h.activate("1.1.0")
        self.assertEqual(again.exception.field_errors[0]["code"], "already_active")

    def test_stale_activation_sequence_is_refused(self):
        h = Harness()
        h.approved("1.0.0", "1.1.0")
        h.activate("1.0.0")
        with self.assertRaises(rules.WorkflowConflict) as stale:
            h.activate("1.1.0", expected=0)
        self.assertEqual(stale.exception.field_errors[0]["code"], "stale_activation")


class ExecutionAndRollbackTests(unittest.TestCase):
    def test_only_the_active_approved_version_may_execute(self):
        h = Harness()
        h.approved("1.0.0", "1.1.0")
        h.propose("2.0.0")
        h.activate("1.1.0")
        self.assertTrue(h.check("1.1.0")["executable"])
        expected = {
            "2.0.0": "unapproved_version",
            "1.0.0": "not_active",
            "9.9.9": "unknown_version",
        }
        for version, code in expected.items():
            with self.subTest(version=version), self.assertRaises(rules.ExecutionRefused) as r:
                h.check(version)
            self.assertEqual(r.exception.field_errors[0]["code"], code)
        refused = [a for a in h.actions() if a[0] == "workflow.execute.denied"]
        self.assertEqual(len(refused), 3)

    def test_nothing_executes_before_any_activation(self):
        h = Harness()
        h.approved("1.0.0")
        with self.assertRaises(rules.ExecutionRefused) as r:
            h.check("1.0.0")
        self.assertEqual(r.exception.field_errors[0]["code"], "not_active")

    def test_rollback_restores_the_previous_approved_version(self):
        h = Harness()
        h.approved("1.0.0", "1.1.0")
        h.activate("1.0.0")
        h.activate("1.1.0")
        back = h.rollback()
        self.assertEqual(
            (back["kind"], back["version"], back["replaced_version"]),
            ("rollback", "1.0.0", "1.1.0"),
        )
        self.assertTrue(h.check("1.0.0")["executable"])
        with self.assertRaises(rules.ExecutionRefused):
            h.check("1.1.0")
        state = service.get_workflow(h.ctx(AUDITOR), h.ports, workflow=WF)
        self.assertEqual(state["active_version"], "1.0.0")
        self.assertEqual(
            [a["kind"] for a in state["history"]], ["activate", "activate", "rollback"]
        )
        self.assertEqual(h.stored("1.1.0").status, "approved")
        self.assertEqual(h.actions()[-2][0], "workflow.rolled_back")

    def test_repeated_rollbacks_walk_back_through_history(self):
        h = Harness()
        h.approved("1.0.0", "1.1.0", "1.2.0")
        for v in ("1.0.0", "1.1.0", "1.2.0"):
            h.activate(v)
        self.assertEqual(h.rollback()["version"], "1.1.0")
        self.assertEqual(h.rollback()["version"], "1.0.0")
        with self.assertRaises(rules.WorkflowConflict) as none_left:
            h.rollback()
        self.assertEqual(none_left.exception.field_errors[0]["code"], "no_previous")
        self.assertEqual(h.actions()[-1][:2], ("workflow.activate.denied", "no_previous"))

    def test_rollback_needs_a_supervisor_a_reason_and_a_fresh_view(self):
        h = Harness()
        h.approved("1.0.0", "1.1.0")
        h.activate("1.0.0")
        h.activate("1.1.0")
        with self.assertRaises(policy.ActionDenied):
            h.rollback(who=SERVICE)
        with self.assertRaises(rules.WorkflowConflict):
            h.rollback(expected=1)
        with self.assertRaises(InvalidMapping):
            service.rollback_workflow(
                h.ctx(SUPERVISOR), h.ports, workflow=WF, expected_seq=h.seq(), reason=""
            )
        self.assertEqual(h.active_version(), "1.1.0")

    def test_other_tenants_see_nothing_and_ledger_stays_intact(self):
        h = Harness()
        h.approved("1.0.0")
        h.activate("1.0.0")
        with self.assertRaises(rules.WorkflowNotFound):
            service.get_workflow(h.ctx(SUPERVISOR_T2), h.ports, workflow=WF)
        with self.assertRaises(rules.ExecutionRefused):
            service.check_execution(h.ctx(SUPERVISOR_T2), h.ports, workflow=WF, version="1.0.0")
        self.assertTrue(ledger.verify_chain(h.ledger.entries(T1))["intact"])
        # propose, approve, activate; the other tenant sees no such workflow, and its
        # refused execution check is recorded in its own chain only.
        self.assertEqual([e.subject_type for e in h.ledger.entries(T1)], ["workflow"] * 3)
        self.assertEqual(
            [e.action for e in h.ledger.entries(T2)],
            ["workflow.execute.denied"],
        )


if __name__ == "__main__":
    unittest.main()
