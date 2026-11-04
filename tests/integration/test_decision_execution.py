"""Approved decisions run through the real Jube case lifecycle adapter (TAX-046 + TAX-034).

Uses ``JubeCaseAdapter`` with the Jube fake from test_jube_case_lifecycle.py, so the
decision engine is checked against the adapter's actual signatures, replay behaviour
and stale-status refusal, not a stand-in.
"""

from __future__ import annotations

import datetime
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "vati_tax"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_jube_case_lifecycle as jube_fixture  # noqa: E402
from vati_tax.decisions import engine  # noqa: E402
from vati_tax.decisions.rules import VersionConflict  # noqa: E402
from vati_tax.identity import policy  # noqa: E402

TENANT = jube_fixture.TENANT
TAXPAYER = "80015243-dd34-4e97-b085-7356ffdd042f"
NOW = datetime.datetime(2026, 11, 4, 10, 0, tzinfo=datetime.UTC)


def scope(user: str, role: policy.Role) -> policy.TenantScope:
    return policy.TenantScope(
        user=user, tenant_id=TENANT, roles=frozenset({role}), taxpayer_ids=frozenset()
    )


AUDITOR = scope("auditor@example.test", policy.AUDITOR)
SUPERVISOR = scope("supervisor@example.test", policy.SUPERVISOR)


def key() -> str:
    return "synthetic-" + uuid.uuid4().hex


class DecisionThroughJubeAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter, self.jube, _ = jube_fixture.make()
        self.case_id = str(uuid.uuid4())
        self.jube.archived.add(self.case_id)
        corr = jube_fixture.CORR
        self.adapter.ensure_case(TENANT, self.case_id, correlation_id=corr)
        # Jube needs a rating on every update; the fixture's first move sets one.
        self.adapter.transition(
            TENANT,
            self.case_id,
            "AWAITING_HUMAN",
            expected_status="RECEIVED",
            correlation_id=corr,
            rating=2,
        )
        self.repo = engine.MemoryRepository()
        self.repo.cases[(TENANT, self.case_id)] = engine.CaseRef(TENANT, self.case_id, TAXPAYER)
        self.evidence_id = str(uuid.uuid4())
        self.repo.evidence[self.evidence_id] = (TENANT, TAXPAYER)

    def ctx(self, who: policy.TenantScope) -> engine.Context:
        return engine.Context.build(who, None, NOW)

    def propose(self, outcome: str = "close_no_further_action") -> dict:
        return engine.propose(
            self.ctx(AUDITOR),
            self.repo,
            self.adapter,
            case_id=self.case_id,
            body={
                "outcome": outcome,
                "rationale": "Invoices reconcile with the declared input VAT for the period.",
                "evidence_ids": [self.evidence_id],
                "rating": 3,
                "expected_case_status": "AWAITING_HUMAN",
                "expected_version": 1,
            },
            idempotency_key=key(),
        )

    def approve(self, decision_id: str) -> dict:
        return engine.review(
            self.ctx(SUPERVISOR),
            self.repo,
            self.adapter,
            action="approve",
            decision_id=decision_id,
            body={"expected_version": 2},
            idempotency_key=key(),
        )

    def test_approved_closure_resolves_and_closes_the_jube_case(self) -> None:
        approved = self.approve(self.propose()["decision_id"])
        self.assertEqual(approved["jube_sync"]["state"], "synced")
        self.assertEqual(approved["jube_sync"]["status_after"], "RESOLVED")
        case = self.adapter.get_case(TENANT, self.case_id, correlation_id=jube_fixture.CORR)
        self.assertEqual((case.status, case.closed_state, case.rating), ("RESOLVED", "closed", 3))

    def test_retry_after_a_lost_response_sends_no_second_update(self) -> None:
        approved = self.approve(self.propose()["decision_id"])
        puts = len(self.jube.puts)
        record = self.repo.decisions[approved["decision_id"]]
        record.sync_state, record.sync_retryable = "failed", True
        retried = engine.retry_execution(
            self.ctx(SUPERVISOR), self.repo, self.adapter, decision_id=approved["decision_id"]
        )
        self.assertEqual(retried["jube_sync"]["state"], "synced")
        self.assertFalse(retried["jube_sync"]["changed"])
        self.assertEqual(len(self.jube.puts), puts)

    def test_case_moved_by_a_jube_user_before_approval_blocks_it(self) -> None:
        proposed = self.propose()
        approved_status = self.jube.cases[1]["caseWorkflowStatusGuid"]
        self.jube.cases[1]["caseWorkflowStatusGuid"] = jube_fixture.STATUS_GUIDS["Investigating"]
        with self.assertRaises(VersionConflict):
            self.approve(proposed["decision_id"])
        self.assertNotEqual(self.jube.cases[1]["caseWorkflowStatusGuid"], approved_status)
        self.assertEqual(
            self.jube.cases[1]["caseWorkflowStatusGuid"],
            jube_fixture.STATUS_GUIDS["Investigating"],
        )

    def test_escalation_reaches_the_escalated_jube_status(self) -> None:
        approved = self.approve(self.propose("escalate_to_supervisor")["decision_id"])
        self.assertEqual(approved["jube_sync"]["state"], "synced")
        case = self.adapter.get_case(TENANT, self.case_id, correlation_id=jube_fixture.CORR)
        self.assertTrue(case.escalated)
        self.assertEqual(case.status, "AWAITING_HUMAN")

    def test_jube_outage_during_execution_is_a_retryable_failure(self) -> None:
        proposed = self.propose()
        self.jube.fail.extend([jube_fixture.cl.TransportError("synthetic outage")] * 10)
        approved = self.approve_after_status_read(proposed["decision_id"])
        self.assertEqual(approved["jube_sync"]["state"], "failed")
        self.assertTrue(approved["jube_sync"]["retryable"])
        self.jube.fail.clear()
        retried = engine.retry_execution(
            self.ctx(SUPERVISOR), self.repo, self.adapter, decision_id=proposed["decision_id"]
        )
        self.assertEqual(retried["jube_sync"]["state"], "synced")

    def approve_after_status_read(self, decision_id: str) -> dict:
        """Let the approval's status read through, then fail every Jube call after it."""
        queued = list(self.jube.fail)
        self.jube.fail.clear()
        original = self.adapter.get_case

        def read_then_fail(*args, **kwargs):
            case = original(*args, **kwargs)
            self.jube.fail.extend(queued)
            return case

        self.adapter.get_case = read_then_fail  # type: ignore[method-assign]
        try:
            return self.approve(decision_id)
        finally:
            self.adapter.get_case = original  # type: ignore[method-assign]


if __name__ == "__main__":
    unittest.main()
