"""Restricted policy-gap and integrity aggregates for management review (TAX-077, ADR-036).

Frappe-free, so the Vati service and the fixture API apply the same rules.

Two things are kept apart on purpose:

* **Policy exposure** is about the rules: reviewer-confirmed findings where an
  observed economic pattern has no verified legal route (TAX-052). It says the
  law or guidance does not clearly cover something. It is never a statement
  that a taxpayer failed to comply.
* **Integrity patterns** are about the process: control overrides and review
  signals from the TAX-059 ledger (and TAX-069 overturned decisions). Signals
  are review prompts, never findings against an officer.

Privacy rules (aggregation only; no drill-down to a person):

* Every cell is checked against the people it covers (distinct taxpayers for
  policy exposure; distinct recording supervisors *and* distinct affected
  officers for overrides). A cell covering fewer than ``min_group_size`` people
  is not shown on its own.
* Small cells are combined into one "combined" row. If that row is still too
  small, the next smallest visible cell joins it, so no small cell can be
  worked out by subtracting the shown cells from the total.
* If the whole section covers fewer than ``min_group_size`` people, the section
  is ``insufficient_volume`` and no counts are returned at all.
* No officer or taxpayer identifier, login or name is ever returned.
* Review signals are counted by kind only; their subjects are never returned,
  and signals about the viewer are withheld (TAX-059 rule) and only counted.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from vati_tax.audit import ledger

MIN_GROUP_SIZE = 3
COMBINED = "combined"
GAP_CONFIRMED = "confirmed_by_reviewer"
GAP_PENDING = "pending_review"
OVERRIDE_CONTROLS = ("case_assignment", "evidence_due_date", "risk_rating", "playbook_step")
SIGNAL_KINDS = (
    "repeated_overrides",
    "repeated_separation_of_duties_attempts",
    "decision_overturned",
    "ledger_integrity",
)
POLICY_NOTE = (
    "A policy gap means the law or guidance does not clearly cover an observed economic "
    "pattern. It describes the rules, not the conduct of any taxpayer, and is not a "
    "finding of noncompliance."
)
OVERRIDE_NOTE = (
    "Overrides are recorded exceptions to a named control, each with a reason. Counts "
    "describe how controls are used. They are not a measure of any officer's conduct."
)
PRIVACY_NOTE = (
    "Groups covering fewer than {k} people are combined so no individual can be "
    "identified. Individual review happens only through the restricted signal review, "
    "which is itself recorded in the audit ledger."
)


@dataclass
class Cell:
    """One aggregate row: an event count plus the distinct people it covers."""

    key: str
    label: str
    count: int = 0
    cases: set[str] = field(default_factory=set)
    people: tuple[set[str], ...] = ()

    def smallest_group(self) -> int:
        return min((len(p) for p in self.people), default=0)


def _merge(into: Cell, other: Cell) -> None:
    into.count += other.count
    into.cases |= other.cases
    into.people = tuple(a | b for a, b in zip(into.people, other.people, strict=True))


def suppress(cells: Sequence[Cell], k: int, combined_label: str) -> dict[str, Any]:
    """Apply the small-cell rule. Returns rows, the combined row and the status."""
    if k < 2:
        raise ValueError("min_group_size must be at least 2")
    width = len(cells[0].people) if cells else 0
    total = Cell(COMBINED, combined_label, people=tuple(set() for _ in range(width)))
    for cell in cells:
        _merge(total, cell)
    if not cells or total.smallest_group() < k:
        return {"status": "insufficient_volume", "rows": [], "combined": None}
    visible = sorted((c for c in cells if c.smallest_group() >= k), key=lambda c: (-c.count, c.key))
    small = [c for c in cells if c.smallest_group() < k]
    combined: Cell | None = None
    combined_keys: list[str] = []
    if small:
        combined = Cell(COMBINED, combined_label, people=tuple(set() for _ in range(width)))
        for cell in small:
            _merge(combined, cell)
            combined_keys.append(cell.key)
        # Pull in the smallest visible cells until the combined row is safe on its own.
        while combined.smallest_group() < k and visible:
            weakest = min(visible, key=lambda c: (c.smallest_group(), c.count, c.key))
            visible.remove(weakest)
            _merge(combined, weakest)
            combined_keys.append(weakest.key)
    rows = [
        {"key": c.key, "label": c.label, "count": c.count, "cases": len(c.cases)} for c in visible
    ]
    combined_row = (
        None
        if combined is None
        else {
            "key": COMBINED,
            "label": combined_label,
            "count": combined.count,
            "cases": len(combined.cases),
            "groups_combined": len(combined_keys),
        }
    )
    return {"status": "available", "rows": rows, "combined": combined_row}


def _in_window(
    value: datetime.datetime | None, since: datetime.datetime, until: datetime.datetime
) -> bool:
    return value is not None and since <= value <= until


def policy_exposure(
    gaps: Iterable[Mapping[str, Any]],
    *,
    labels: Mapping[str, str],
    since: datetime.datetime,
    until: datetime.datetime,
    min_group_size: int = MIN_GROUP_SIZE,
) -> dict[str, Any]:
    """Reviewer-confirmed policy gaps by policy area, with small cells combined.

    Each gap needs ``code``, ``case_id``, ``taxpayer_id``, ``gap_review`` and
    ``reviewed_at`` (a datetime). Only ``no_verified_route`` findings belong here.
    """
    cells: dict[str, Cell] = {}
    pending = 0
    unlabelled: set[str] = set()
    for gap in gaps:
        if gap.get("legal_route_status") != "no_verified_route":
            raise ValueError("only findings without a verified legal route are policy gaps")
        if gap["gap_review"] == GAP_PENDING:
            pending += 1
            continue
        if gap["gap_review"] != GAP_CONFIRMED:
            raise ValueError(f"unknown gap_review {gap['gap_review']!r}")
        if not _in_window(gap["reviewed_at"], since, until):
            continue
        code = str(gap["code"])
        if code not in labels:
            unlabelled.add(code)
        cell = cells.setdefault(
            code, Cell(code, labels.get(code, "Policy area with no description"), people=(set(),))
        )
        cell.count += 1
        cell.cases.add(str(gap["case_id"]))
        cell.people[0].add(str(gap["taxpayer_id"]))
    result = suppress(
        sorted(cells.values(), key=lambda c: c.key),
        min_group_size,
        "Other policy areas (combined to protect privacy)",
    )
    available = result["status"] == "available"
    return {
        "status": result["status"],
        "note": POLICY_NOTE,
        "confirmed_gaps": sum(c.count for c in cells.values()) if available else None,
        "pending_review": pending,
        "areas": result["rows"],
        "combined": result["combined"],
        "unlabelled_codes": len(unlabelled),
    }


def override_patterns(
    entries: Sequence[ledger.LedgerEntry],
    *,
    since: datetime.datetime,
    until: datetime.datetime,
    min_group_size: int = MIN_GROUP_SIZE,
) -> dict[str, Any]:
    """Overrides in the window by control. A cell needs enough recorders and affected officers."""
    labels = {
        "case_assignment": "Case assignment",
        "evidence_due_date": "Evidence due date",
        "risk_rating": "Risk rating",
        "playbook_step": "Playbook step",
    }
    cells: dict[str, Cell] = {}
    for entry in entries:
        if entry.category != "override" or not _in_window(entry.occurred_at, since, until):
            continue
        control = entry.action.removeprefix("override.")
        cell = cells.setdefault(
            control, Cell(control, labels.get(control, "Other control"), people=(set(), set()))
        )
        cell.count += 1
        if entry.case_id:
            cell.cases.add(entry.case_id)
        cell.people[0].add(entry.actor.lower())
        # An override without a named officer still counts against the smallest group.
        cell.people[1].add((entry.target_actor or f"unnamed:{entry.event_id}").lower())
    result = suppress(
        sorted(cells.values(), key=lambda c: c.key),
        min_group_size,
        "Other controls (combined to protect privacy)",
    )
    available = result["status"] == "available"
    return {
        "status": result["status"],
        "note": OVERRIDE_NOTE,
        "total_overrides": sum(c.count for c in cells.values()) if available else None,
        "by_control": result["rows"],
        "combined": result["combined"],
    }


def review_signal_counts(
    entries: Sequence[ledger.LedgerEntry],
    *,
    now: datetime.datetime,
    viewer: str,
    window_days: int,
) -> dict[str, Any]:
    """Signal counts by kind, using the TAX-059 rules. Subjects and event IDs are dropped."""
    signals = ledger.integrity_signals(entries, now=now, viewer=viewer, window_days=window_days)
    by_kind = {kind: 0 for kind in SIGNAL_KINDS}
    for signal in signals["signals"]:
        by_kind[signal["kind"]] = by_kind.get(signal["kind"], 0) + 1
    return {
        "status": "review_only",
        "note": ledger.REVIEW_ONLY_NOTE,
        "by_kind": [{"kind": kind, "signals": count} for kind, count in by_kind.items() if count],
        "withheld_about_viewer": signals["withheld_about_viewer"],
    }


def integrity_review(
    *,
    tenant_id: str,
    gaps: Iterable[Mapping[str, Any]] | None,
    gap_labels: Mapping[str, str],
    entries: Sequence[ledger.LedgerEntry] | None,
    viewer: str,
    now: datetime.datetime,
    window_days: int,
    min_group_size: int = MIN_GROUP_SIZE,
) -> dict[str, Any]:
    """The full restricted review body.

    ``gaps`` None means no reviewed-findings store is available (in Frappe until
    TAX-086 lands); ``entries`` None means the ledger could not be read. Either is
    reported as ``unavailable`` with a ``missing_data`` note, never as zero.
    """
    if not 1 <= window_days <= 365:
        raise ValueError("window_days must be between 1 and 365")
    until = now.astimezone(datetime.UTC).replace(microsecond=0)
    since = until - datetime.timedelta(days=window_days)
    missing: list[dict[str, str]] = []
    exposure: dict[str, Any]
    if gaps is None:
        exposure = {"status": "unavailable"}
        missing.append(
            {
                "metric": "policy_exposure",
                "detail": "Reviewed policy-gap findings are not stored in this environment yet.",
            }
        )
    else:
        exposure = policy_exposure(
            gaps, labels=gap_labels, since=since, until=until, min_group_size=min_group_size
        )
        if exposure.pop("unlabelled_codes"):
            missing.append(
                {
                    "metric": "policy_exposure",
                    "detail": "Some policy areas have no description on record and are shown by a generic name.",
                }
            )
    if entries is None:
        overrides: dict[str, Any] = {"status": "unavailable"}
        signals: dict[str, Any] = {"status": "unavailable"}
        ledger_check: dict[str, Any] = {"available": False}
        missing.append(
            {
                "metric": "integrity",
                "detail": "The audit ledger is not available in this environment.",
            }
        )
    else:
        overrides = override_patterns(
            entries, since=since, until=until, min_group_size=min_group_size
        )
        signals = review_signal_counts(entries, now=until, viewer=viewer, window_days=window_days)
        ledger_check = {"available": True, "intact": ledger.verify_chain(entries)["intact"]}
    return {
        "tenant_id": tenant_id,
        "generated_at": ledger.iso(until),
        "window": {"since": ledger.iso(since), "until": ledger.iso(until), "days": window_days},
        "min_group_size": min_group_size,
        "privacy_note": PRIVACY_NOTE.format(k=min_group_size),
        "policy_exposure": exposure,
        "override_patterns": overrides,
        "review_signals": signals,
        "ledger": ledger_check,
        "missing_data": missing,
        "synthetic": True,
    }
