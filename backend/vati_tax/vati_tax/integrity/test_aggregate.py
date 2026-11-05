"""Frappe-free tests for the TAX-077 restricted aggregates (ADR-031).

Covers the task's verification: policy exposure is kept apart from
noncompliance, signals from accusations, and aggregation privacy holds (small
cells combined, no derivable small cell, no identities, signals about the
viewer withheld).
"""

from __future__ import annotations

import datetime
import itertools
import json
import unittest
import uuid
from dataclasses import replace
from typing import Any

from vati_tax.audit import ledger
from vati_tax.integrity import aggregate

UTC = datetime.UTC
NOW = datetime.datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
TENANT = "10000000-0000-4000-8000-000000000001"
VIEWER = "supervisor.c@example.test"
LABELS = {"a": "Area A", "b": "Area B", "c": "Area C", "d": "Area D"}
# Words that would turn an aggregate into an accusation or a compliance verdict.
ACCUSATORY = (
    "fraud",
    "noncompliant",
    "non-compliant",
    "violation",
    "misconduct",
    "guilty",
    "suspicious",
    "offender",
    "wrongdoing",
    "evasion",
)


def gap(code: str, taxpayer: int, *, review: str = "confirmed_by_reviewer", days_ago: int = 3) -> dict[str, Any]:
    return {
        "code": code,
        "case_id": str(uuid.uuid4()),
        "taxpayer_id": f"27000000-0000-4000-8000-{taxpayer:012d}",
        "legal_route_status": "no_verified_route",
        "gap_review": review,
        "reviewed_at": NOW - datetime.timedelta(days=days_ago) if review != "pending_review" else None,
    }


def seal(events: list[dict[str, Any]]) -> list[ledger.LedgerEntry]:
    store = ledger.MemoryLedger()
    for index, event in enumerate(events):
        ledger.append(
            store,
            ledger.Unsealed(
                event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"tax077/{index}")),
                tenant_id=TENANT,
                action=event["action"],
                outcome=event.get("outcome", "ok"),
                actor=event["actor"],
                actor_role=event.get("actor_role", "supervisor"),
                occurred_at=event.get("at", NOW - datetime.timedelta(days=2, minutes=index)),
                correlation_id=str(uuid.uuid4()),
                subject_type="control",
                subject_id="control",
                payload_hash=ledger.digest(index),
                case_id=event.get("case_id"),
                target_actor=event.get("target"),
                reason=event.get("reason", "Synthetic reason for a recorded exception."),
            ),
        )
    return list(store.entries(TENANT))


def override(control: str, actor: str, target: str, **kw: Any) -> dict[str, Any]:
    return {"action": f"override.{control}", "actor": actor, "target": target, **kw}


class SuppressionTests(unittest.TestCase):
    def cells(self, sizes: dict[str, int]) -> list[aggregate.Cell]:
        out = []
        people = itertools.count()
        for key, size in sizes.items():
            members = {f"p{next(people)}" for _ in range(size)}
            out.append(aggregate.Cell(key, key, count=size, people=(members,)))
        return out

    def test_cells_at_or_above_the_minimum_are_shown(self):
        result = aggregate.suppress(self.cells({"a": 3, "b": 5}), 3, "combined")
        self.assertEqual([r["key"] for r in result["rows"]], ["b", "a"])
        self.assertIsNone(result["combined"])

    def test_small_cells_are_combined(self):
        result = aggregate.suppress(self.cells({"a": 5, "b": 4, "c": 2, "d": 1}), 3, "combined")
        self.assertEqual([r["key"] for r in result["rows"]], ["a", "b"])
        self.assertEqual(result["combined"]["count"], 3)
        self.assertEqual(result["combined"]["groups_combined"], 2)

    def test_a_too_small_combined_row_pulls_in_the_smallest_visible_cell(self):
        # c alone (1 person) cannot be shown, and showing a and b with the total
        # would reveal it, so b joins the combined row.
        result = aggregate.suppress(self.cells({"a": 5, "b": 3, "c": 1}), 3, "combined")
        self.assertEqual([r["key"] for r in result["rows"]], ["a"])
        self.assertEqual(result["combined"]["count"], 4)

    def test_no_small_cell_can_be_derived_from_shown_rows(self):
        for sizes in ({"a": 4, "b": 1}, {"a": 3, "b": 2, "c": 1}, {"a": 7, "b": 3, "c": 2}):
            with self.subTest(sizes=sizes):
                result = aggregate.suppress(self.cells(sizes), 3, "combined")
                shown = {r["key"] for r in result["rows"]}
                for key, size in sizes.items():
                    if size < 3:
                        self.assertNotIn(key, shown)
                if result["combined"] is not None:
                    self.assertGreaterEqual(result["combined"]["count"], 3)

    def test_a_section_covering_too_few_people_returns_no_counts(self):
        result = aggregate.suppress(self.cells({"a": 1, "b": 1}), 3, "combined")
        self.assertEqual(result, {"status": "insufficient_volume", "rows": [], "combined": None})
        self.assertEqual(aggregate.suppress([], 3, "combined")["status"], "insufficient_volume")

    def test_every_group_of_people_in_a_cell_must_meet_the_minimum(self):
        many = {"s1", "s2", "s3"}
        cell = aggregate.Cell("x", "x", count=3, people=(many, {"only-one-officer"}))
        result = aggregate.suppress([cell], 3, "combined")
        self.assertEqual(result["status"], "insufficient_volume")

    def test_minimum_below_two_is_rejected(self):
        with self.assertRaises(ValueError):
            aggregate.suppress([], 1, "combined")


class PolicyExposureTests(unittest.TestCase):
    def exposure(self, gaps, **kw):
        return aggregate.policy_exposure(
            gaps, labels=LABELS, since=NOW - datetime.timedelta(days=30), until=NOW, **kw
        )

    def test_counts_reviewer_confirmed_gaps_by_area(self):
        gaps = [gap("a", n) for n in range(4)] + [gap("b", n) for n in range(10, 13)]
        result = self.exposure(gaps)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["confirmed_gaps"], 7)
        self.assertEqual({r["key"]: r["count"] for r in result["areas"]}, {"a": 4, "b": 3})

    def test_pending_gaps_are_counted_separately_and_not_aggregated(self):
        gaps = [gap("a", n) for n in range(3)] + [gap("a", 9, review="pending_review")]
        result = self.exposure(gaps)
        self.assertEqual((result["confirmed_gaps"], result["pending_review"]), (3, 1))

    def test_gaps_outside_the_window_are_left_out(self):
        gaps = [gap("a", n) for n in range(3)] + [gap("a", 9, days_ago=60)]
        self.assertEqual(self.exposure(gaps)["confirmed_gaps"], 3)

    def test_only_findings_without_a_verified_route_are_policy_gaps(self):
        verified = {**gap("a", 1), "legal_route_status": "verified_by_reviewer"}
        with self.assertRaises(ValueError):
            self.exposure([verified])

    def test_one_taxpayer_with_many_gaps_is_still_one_person(self):
        result = self.exposure([gap("a", 1) for _ in range(5)])
        self.assertEqual(result["status"], "insufficient_volume")
        self.assertIsNone(result["confirmed_gaps"])

    def test_no_taxpayer_or_case_identifier_is_returned(self):
        gaps = [gap("a", n) for n in range(4)]
        text = json.dumps(self.exposure(gaps))
        for item in gaps:
            self.assertNotIn(item["taxpayer_id"], text)
            self.assertNotIn(item["case_id"], text)

    def test_wording_describes_the_rules_not_taxpayer_conduct(self):
        note = self.exposure([gap("a", n) for n in range(3)])["note"].lower()
        self.assertIn("not a finding of noncompliance", note)
        self.assertIn("describes the rules", note)


class IntegrityTests(unittest.TestCase):
    def events(self) -> list[dict[str, Any]]:
        sups = ["s1@example.test", "s2@example.test", VIEWER]
        officers = ["o1@example.test", "o2@example.test", "o3@example.test"]
        events = [override("case_assignment", s, o) for s, o in zip(sups, officers, strict=True)]
        events += [override("risk_rating", "s1@example.test", "o1@example.test")]
        # The viewer has three overrides: a repeated_overrides signal about the viewer.
        events += [override("evidence_due_date", VIEWER, "o2@example.test") for _ in range(2)]
        events += [
            {"action": "decision.approve.denied", "actor": "o9@example.test", "actor_role": "auditor"},
            {"action": "decision.approve.denied", "actor": "o9@example.test", "actor_role": "auditor"},
            {
                "action": "challenge.reviewed",
                "outcome": "overturned",
                "actor": "s2@example.test",
                "target": "o1@example.test",
            },
        ]
        return events

    def review(self, entries=None, **kw):
        return aggregate.integrity_review(
            tenant_id=TENANT,
            gaps=[gap("a", n) for n in range(3)],
            gap_labels=LABELS,
            entries=seal(self.events()) if entries is None else entries,
            viewer=VIEWER,
            now=NOW,
            window_days=30,
            **kw,
        )

    def test_overrides_are_shown_only_where_enough_recorders_and_officers(self):
        extra = [
            override("playbook_step", s, o)
            for s, o in (
                ("s1@example.test", "o4@example.test"),
                ("s2@example.test", "o5@example.test"),
                ("s3@example.test", "o6@example.test"),
            )
        ]
        result = self.review(seal(self.events() + extra))["override_patterns"]
        self.assertEqual(result["total_overrides"], 9)
        shown = {r["key"] for r in result["by_control"]}
        # risk_rating (one recorder, one officer) and evidence_due_date (one
        # recorder) are too small; together they still cover only two recorders,
        # so the smallest visible control (case_assignment) joins them.
        self.assertEqual(shown, {"playbook_step"})
        self.assertEqual(result["combined"]["count"], 6)
        self.assertEqual(result["combined"]["groups_combined"], 3)
        self.assertEqual(sum(r["count"] for r in result["by_control"]) + 6, 9)

    def test_signals_are_counted_by_kind_without_subjects(self):
        result = self.review()
        signals = result["review_signals"]
        self.assertEqual(signals["status"], "review_only")
        kinds = {s["kind"]: s["signals"] for s in signals["by_kind"]}
        self.assertEqual(kinds.get("repeated_separation_of_duties_attempts"), 1)
        self.assertEqual(kinds.get("decision_overturned"), 1)
        # The viewer's own repeated_overrides signal is withheld and only counted.
        self.assertNotIn("repeated_overrides", kinds)
        self.assertEqual(signals["withheld_about_viewer"], 1)

    def test_no_login_officer_name_or_event_id_is_returned(self):
        entries = seal(self.events())
        text = json.dumps(self.review(entries))
        for entry in entries:
            self.assertNotIn(entry.actor, text)
            self.assertNotIn(entry.event_id, text)
            if entry.target_actor:
                self.assertNotIn(entry.target_actor, text)
        self.assertNotIn("subject_actor", text)

    def test_no_accusatory_or_compliance_verdict_wording(self):
        text = json.dumps(self.review()).lower()
        for word in ACCUSATORY:
            self.assertNotIn(word, text)

    def test_a_tampered_ledger_is_reported_and_signalled(self):
        entries = seal(self.events())
        entries[1] = replace(entries[1], reason="changed after the fact")
        result = self.review(entries)
        self.assertEqual(result["ledger"], {"available": True, "intact": False})
        kinds = {s["kind"] for s in result["review_signals"]["by_kind"]}
        self.assertIn("ledger_integrity", kinds)

    def test_an_unreadable_ledger_is_a_listed_gap_not_zeros(self):
        result = aggregate.integrity_review(
            tenant_id=TENANT,
            gaps=[],
            gap_labels=LABELS,
            entries=None,
            viewer=VIEWER,
            now=NOW,
            window_days=30,
        )
        self.assertEqual(result["override_patterns"], {"status": "unavailable"})
        self.assertEqual(result["review_signals"], {"status": "unavailable"})
        self.assertEqual(result["ledger"], {"available": False})
        self.assertEqual([m["metric"] for m in result["missing_data"]], ["integrity"])

    def test_unknown_policy_area_is_listed_as_missing_description(self):
        result = aggregate.integrity_review(
            tenant_id=TENANT,
            gaps=[gap("zz", n) for n in range(3)],
            gap_labels=LABELS,
            entries=[],
            viewer=VIEWER,
            now=NOW,
            window_days=30,
        )
        self.assertEqual([m["metric"] for m in result["missing_data"]], ["policy_exposure"])
        self.assertNotIn("unlabelled_codes", result["policy_exposure"])

    def test_window_must_be_1_to_365_days(self):
        for days in (0, 366):
            with self.subTest(days=days), self.assertRaises(ValueError):
                aggregate.integrity_review(
                    tenant_id=TENANT,
                    gaps=[],
                    gap_labels=LABELS,
                    entries=[],
                    viewer=VIEWER,
                    now=NOW,
                    window_days=days,
                )


if __name__ == "__main__":
    unittest.main()
