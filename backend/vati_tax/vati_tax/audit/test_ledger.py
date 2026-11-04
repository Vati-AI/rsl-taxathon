"""Frappe-free tests for the hash-chained audit ledger and its review signals (TAX-059)."""

from __future__ import annotations

import datetime
import unittest
import uuid
from dataclasses import replace

from vati_tax.audit import ledger
from vati_tax.audit.ledger import LedgerEntry, MemoryLedger, Unsealed

T1 = "10000000-0000-4000-8000-000000000001"
T2 = "10000000-0000-4000-8000-000000000002"
CASE = "20000000-0000-4000-8000-000000000001"
NOW = datetime.datetime(2026, 11, 4, 12, 0, tzinfo=datetime.UTC)


def record(
    action: str = "decision.proposed",
    *,
    tenant: str = T1,
    actor: str = "auditor@example.test",
    at: datetime.datetime = NOW,
    subject_type: str = "decision",
    **extra: object,
) -> Unsealed:
    return Unsealed(
        event_id=str(uuid.uuid4()),
        tenant_id=tenant,
        action=action,
        outcome="recorded",
        actor=actor,
        actor_role="auditor",
        occurred_at=at,
        correlation_id=str(uuid.uuid4()),
        subject_type=subject_type,
        subject_id=str(uuid.uuid4()),
        payload_hash=ledger.digest({"action": action}),
        case_id=CASE,
        **extra,  # type: ignore[arg-type]
    )


def chain(n: int, tenant: str = T1) -> list[LedgerEntry]:
    store = MemoryLedger()
    for i in range(n):
        ledger.append(store, record(at=NOW + datetime.timedelta(minutes=i), tenant=tenant))
    return store.entries(tenant)


class SealTests(unittest.TestCase):
    def test_first_entry_links_to_genesis_and_each_next_to_its_predecessor(self) -> None:
        entries = chain(3)
        self.assertEqual([e.chain_seq for e in entries], [1, 2, 3])
        self.assertEqual(entries[0].prev_hash, ledger.GENESIS)
        self.assertEqual(entries[1].prev_hash, entries[0].entry_hash)
        self.assertEqual(entries[2].prev_hash, entries[1].entry_hash)

    def test_hash_is_stable_and_ignores_sub_second_precision(self) -> None:
        base = record(at=NOW.replace(microsecond=123456))
        first = ledger.seal(base, None)
        self.assertEqual(first.occurred_at, NOW)
        self.assertEqual(ledger.compute_hash(first), first.entry_hash)
        self.assertEqual(ledger.seal(replace(base, occurred_at=NOW), None), first)

    def test_a_chain_never_crosses_tenants(self) -> None:
        head = chain(1, T1)[-1]
        with self.assertRaises(ValueError):
            ledger.seal(record(tenant=T2), head)

    def test_unknown_subject_type_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            ledger.seal(record(subject_type="taxpayer_profile"), None)

    def test_categories_follow_the_action(self) -> None:
        expected = {
            "decision.proposed": "write",
            "decision.approved": "approval",
            "decision.rejected": "approval",
            "decision.executed": "write",
            "evidence_request.recorded": "write",
            "case.read": "read",
            "ledger.read": "read",
            "decision.approve.denied": "denied",
            "override.case_assignment": "override",
        }
        for action, category in expected.items():
            self.assertEqual(ledger.category_for(action), category, action)


class VerifyTests(unittest.TestCase):
    def test_untouched_chain_is_intact(self) -> None:
        report = ledger.verify_chain(chain(5))
        self.assertTrue(report["intact"])
        self.assertEqual(report["entries_checked"], 5)
        self.assertEqual(report["head_seq"], 5)

    def test_edited_field_is_detected(self) -> None:
        entries = chain(4)
        entries[1] = replace(entries[1], outcome="approved")
        report = ledger.verify_chain(entries)
        self.assertFalse(report["intact"])
        self.assertEqual(report["problems"], [{"chain_seq": 2, "code": "hash_mismatch"}])

    def test_edited_and_rehashed_row_breaks_the_next_link(self) -> None:
        entries = chain(4)
        forged = replace(entries[1], actor="someone-else@example.test")
        entries[1] = replace(forged, entry_hash=ledger.compute_hash(forged))
        report = ledger.verify_chain(entries)
        self.assertEqual(report["problems"], [{"chain_seq": 3, "code": "broken_link"}])

    def test_removed_row_is_detected(self) -> None:
        entries = chain(4)
        del entries[2]
        codes = {p["code"] for p in ledger.verify_chain(entries)["problems"]}
        self.assertIn("missing_entry", codes)

    def test_duplicate_position_is_detected(self) -> None:
        entries = chain(3)
        entries.append(replace(entries[1], event_id=str(uuid.uuid4())))
        codes = {p["code"] for p in ledger.verify_chain(entries)["problems"]}
        self.assertIn("duplicate_position", codes)

    def test_problems_never_echo_entry_content(self) -> None:
        entries = chain(2)
        entries[0] = replace(entries[0], reason="secret rationale text")
        for problem in ledger.verify_chain(entries)["problems"]:
            self.assertEqual(set(problem), {"chain_seq", "code"})


class AppendTests(unittest.TestCase):
    def test_tenants_have_independent_chains(self) -> None:
        store = MemoryLedger()
        ledger.append(store, record(tenant=T1))
        ledger.append(store, record(tenant=T2))
        ledger.append(store, record(tenant=T1))
        self.assertEqual([e.chain_seq for e in store.entries(T1)], [1, 2])
        self.assertEqual([e.chain_seq for e in store.entries(T2)], [1])
        self.assertTrue(ledger.verify_chain(store.entries(T1))["intact"])

    def test_a_writer_that_loses_a_position_retries_at_the_new_head(self) -> None:
        class Racing(MemoryLedger):
            raced = False

            def insert(self, entry: LedgerEntry) -> None:
                if not self.raced:
                    self.raced = True
                    super().insert(ledger.seal(record(), self.head(entry.tenant_id)))
                    raise ledger.Duplicate("chain_key")
                super().insert(entry)

        store = Racing()
        entry = ledger.append(store, record())
        self.assertEqual(entry.chain_seq, 2)
        self.assertTrue(ledger.verify_chain(store.entries(T1))["intact"])

    def test_a_clash_with_an_unchanged_head_is_raised(self) -> None:
        store = MemoryLedger()
        ledger.append(store, record(sequence=1))
        with self.assertRaises(ledger.Duplicate):
            ledger.append(store, record(sequence=1))
        self.assertEqual(len(store.entries(T1)), 1)

    def test_an_event_id_is_stored_once(self) -> None:
        store = MemoryLedger()
        first = record()
        ledger.append(store, first)
        with self.assertRaises(ledger.Duplicate):
            ledger.append(store, first)


class SignalTests(unittest.TestCase):
    def build(self, *items: Unsealed) -> list[LedgerEntry]:
        store = MemoryLedger()
        for item in items:
            ledger.append(store, item)
        return store.entries(T1)

    def overrides(self, actor: str, n: int, start: datetime.datetime = NOW) -> list[Unsealed]:
        return [
            record(
                "override.case_assignment",
                actor=actor,
                at=start - datetime.timedelta(days=i),
                subject_type="case",
                reason="Officer on leave; reassigned to keep the deadline.",
            )
            for i in range(n)
        ]

    def test_repeated_overrides_raise_a_review_only_signal(self) -> None:
        entries = self.build(*self.overrides("sup-a@example.test", 3))
        result = ledger.integrity_signals(entries, now=NOW, viewer="sup-b@example.test")
        self.assertEqual(result["status"], "review_only")
        [signal] = result["signals"]
        self.assertEqual(signal["kind"], "repeated_overrides")
        self.assertEqual(signal["subject_actor"], "sup-a@example.test")
        self.assertEqual(signal["count"], 3)
        self.assertEqual(signal["status"], "review_only")
        self.assertIn("not a finding", signal["note"])

    def test_fewer_overrides_than_the_threshold_raise_nothing(self) -> None:
        entries = self.build(*self.overrides("sup-a@example.test", 2))
        result = ledger.integrity_signals(entries, now=NOW, viewer="sup-b@example.test")
        self.assertEqual(result["signals"], [])

    def test_overrides_outside_the_window_are_not_counted(self) -> None:
        old = NOW - datetime.timedelta(days=40)
        entries = self.build(*self.overrides("sup-a@example.test", 3, start=old))
        result = ledger.integrity_signals(entries, now=NOW, viewer="sup-b@example.test")
        self.assertEqual(result["signals"], [])

    def test_repeated_refused_self_approvals_raise_a_signal(self) -> None:
        attempts = [
            record("decision.approve.denied", actor="sup-a@example.test", category="denied"),
            record("decision.approve.denied", actor="sup-a@example.test", category="denied"),
        ]
        entries = self.build(*attempts)
        result = ledger.integrity_signals(entries, now=NOW, viewer="sup-b@example.test")
        self.assertEqual(
            [s["kind"] for s in result["signals"]], ["repeated_separation_of_duties_attempts"]
        )

    def test_a_supervisor_never_sees_signals_about_themselves(self) -> None:
        entries = self.build(*self.overrides("Sup-A@example.test", 3))
        result = ledger.integrity_signals(entries, now=NOW, viewer="sup-a@example.test")
        self.assertEqual(result["signals"], [])
        self.assertEqual(result["withheld_about_viewer"], 1)

    def test_a_broken_chain_raises_a_ledger_integrity_signal(self) -> None:
        entries = self.build(record(), record())
        entries[0] = replace(entries[0], outcome="changed")
        result = ledger.integrity_signals(entries, now=NOW, viewer="sup-b@example.test")
        [signal] = result["signals"]
        self.assertEqual(signal["kind"], "ledger_integrity")
        self.assertIsNone(signal["subject_actor"])

    def test_window_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            ledger.integrity_signals([], now=NOW, viewer="x", window_days=0)


class SummaryTests(unittest.TestCase):
    def test_counts_by_category_inside_the_window(self) -> None:
        store = MemoryLedger()
        ledger.append(store, record("decision.proposed"))
        ledger.append(store, record("decision.approved", actor="sup@example.test"))
        ledger.append(store, record("case.read", subject_type="case"))
        ledger.append(store, record("case.read", at=NOW - datetime.timedelta(days=10)))
        result = ledger.summary(
            store.entries(T1), since=NOW - datetime.timedelta(days=1), until=NOW
        )
        self.assertEqual(result["entries"], 3)
        self.assertEqual(result["by_category"]["write"], 1)
        self.assertEqual(result["by_category"]["approval"], 1)
        self.assertEqual(result["by_category"]["read"], 1)
        self.assertEqual(result["by_category"]["override"], 0)
        self.assertEqual(result["distinct_actors"], 2)
        self.assertTrue(result["ledger_intact"])
        self.assertEqual(result["head_seq"], 4)

    def test_since_after_until_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            ledger.summary([], since=NOW, until=NOW - datetime.timedelta(days=1))


if __name__ == "__main__":
    unittest.main()
