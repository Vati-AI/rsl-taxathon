"""Frappe-free tests for who may read the TAX-077 integrity review (ADR-036).

Server-side enforcement: only supervisors of the selected tenant read it; every
other role is refused before anything is computed, and the refusal is kept in
the TAX-059 ledger. Successful reads are recorded too, and signals about the
viewing supervisor are withheld.
"""

from __future__ import annotations

import datetime
import json
import unittest
import uuid
from collections.abc import Iterator
from typing import Any

from vati_tax.audit import ledger
from vati_tax.audit import service as audit
from vati_tax.audit.ledger import MemoryLedger
from vati_tax.identity import policy
from vati_tax.ids.canonical import InvalidMapping
from vati_tax.integrity import service
from vati_tax.integrity.test_aggregate import LABELS, gap

UTC = datetime.UTC
NOW = datetime.datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
T1 = "10000000-0000-4000-8000-000000000001"
T2 = "10000000-0000-4000-8000-000000000002"


def scope(user: str, role: policy.Role, tenant: str = T1) -> policy.TenantScope:
    taxpayers = (
        frozenset({"80015243-dd34-4e97-b085-7356ffdd042f"}) if role == "taxpayer" else frozenset()
    )
    return policy.TenantScope(
        user=user, tenant_id=tenant, roles=frozenset({role}), taxpayer_ids=taxpayers
    )


SUPERVISOR = scope("supervisor.c@example.test", policy.SUPERVISOR)
OTHERS = {
    "auditor": scope("auditor@example.test", policy.AUDITOR),
    "specialist": scope("specialist@example.test", policy.SPECIALIST),
    "service": scope("service@example.test", policy.SERVICE),
    "taxpayer": scope("taxpayer@example.test", policy.TAXPAYER),
}


def ctx(who: policy.TenantScope) -> audit.Context:
    return audit.Context.build(who, None, NOW)


def override(store: MemoryLedger, actor: str, target: str, days_ago: int, tenant: str = T1) -> None:
    ledger.append(
        store,
        ledger.Unsealed(
            event_id=str(uuid.uuid4()),
            tenant_id=tenant,
            action="override.case_assignment",
            outcome="ok",
            actor=actor,
            actor_role="supervisor",
            occurred_at=NOW - datetime.timedelta(days=days_ago),
            correlation_id=str(uuid.uuid4()),
            subject_type="case",
            subject_id=str(uuid.uuid4()),
            payload_hash=ledger.digest({"n": days_ago}),
            case_id=str(uuid.uuid4()),
            reason="Synthetic: rebalanced while an officer was on leave.",
            target_actor=target,
        ),
    )


def gaps() -> list[dict[str, object]]:
    return [gap("a", n) for n in range(1, 4)]


class AccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryLedger()
        # Three supervisors each record three overrides on three officers, so the
        # repeated-overrides signal fires for all of them, including the viewer.
        for actor in (
            "supervisor.c@example.test",
            "supervisor.e@example.test",
            "supervisor.f@example.test",
        ):
            for n, target in enumerate(
                ("officer.a@example.test", "officer.b@example.test", "officer.g@example.test")
            ):
                override(self.store, actor, target, days_ago=2 + n)

    def review(self, who: policy.TenantScope, **kwargs: Any) -> dict[str, Any]:
        options: dict[str, Any] = {"gaps": gaps(), "gap_labels": LABELS, "window_days": 30}
        options.update(kwargs)
        return service.integrity_review(ctx(who), self.store, **options)

    def test_supervisor_reads_the_review_and_the_read_is_recorded(self) -> None:
        before = len(self.store.entries(T1))
        body = self.review(SUPERVISOR)
        self.assertEqual(body["tenant_id"], T1)
        entries = self.store.entries(T1)
        self.assertEqual(len(entries), before + 1)
        self.assertEqual(entries[-1].action, "ledger.read")
        self.assertEqual(entries[-1].subject_type, service.SUBJECT_TYPE)
        self.assertEqual(entries[-1].actor, SUPERVISOR.user)
        self.assertTrue(ledger.verify_chain(entries)["intact"])

    def test_every_other_role_is_refused_and_the_attempt_is_kept(self) -> None:
        for name, who in OTHERS.items():
            with self.subTest(role=name):
                before = len(self.store.entries(T1))
                with self.assertRaises(policy.ActionDenied):
                    self.review(who)
                entries = self.store.entries(T1)
                self.assertEqual(len(entries), before + 1)
                self.assertEqual(entries[-1].action, "ledger.read.denied")
                self.assertEqual(entries[-1].category, "denied")
                self.assertEqual(entries[-1].actor, who.user)

    def test_refusal_happens_before_any_aggregate_is_computed(self) -> None:
        calls: list[int] = []

        def counting() -> list[dict[str, object]]:
            calls.append(1)
            return gaps()

        class Gaps:
            def __iter__(self) -> Iterator[dict[str, object]]:
                return iter(counting())

        with self.assertRaises(policy.ActionDenied):
            self.review(OTHERS["auditor"], gaps=Gaps())
        self.assertEqual(calls, [])

    def test_a_supervisor_of_another_tenant_sees_nothing_from_this_one(self) -> None:
        body = self.review(scope("supervisor.c@example.test", policy.SUPERVISOR, T2), gaps=[])
        self.assertEqual(body["tenant_id"], T2)
        self.assertEqual(body["override_patterns"]["status"], "insufficient_volume")
        self.assertEqual(self.store.entries(T2)[-1].action, "ledger.read")

    def test_signals_about_the_viewer_are_withheld_and_no_subject_is_returned(self) -> None:
        body = self.review(SUPERVISOR)
        signals = body["review_signals"]
        self.assertEqual(signals["withheld_about_viewer"], 1)
        kinds = {row["kind"]: row["signals"] for row in signals["by_kind"]}
        self.assertEqual(kinds["repeated_overrides"], 2)
        text = json.dumps(body)
        for person in (
            "supervisor.c",
            "supervisor.e",
            "supervisor.f",
            "officer.a",
            "officer.b",
            "officer.g",
        ):
            self.assertNotIn(person, text)

    def test_unknown_sources_are_unavailable_not_zero(self) -> None:
        body = self.review(SUPERVISOR, gaps=None, ledger_available=False)
        self.assertEqual(body["policy_exposure"], {"status": "unavailable"})
        self.assertEqual(body["override_patterns"], {"status": "unavailable"})
        self.assertEqual(body["review_signals"], {"status": "unavailable"})
        self.assertEqual(body["ledger"], {"available": False})
        self.assertEqual(
            {m["metric"] for m in body["missing_data"]}, {"policy_exposure", "integrity"}
        )

    def test_invalid_window_is_rejected_after_authorisation(self) -> None:
        with self.assertRaises(InvalidMapping):
            self.review(SUPERVISOR, window_days=0)
        with self.assertRaises(policy.ActionDenied):
            self.review(OTHERS["auditor"], window_days=0)


if __name__ == "__main__":
    unittest.main()
