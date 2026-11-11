"""Frappe-free ``getManagementSummary`` body (TAX-060 rules, live in TAX-090).

The fixture server (``scripts/fixture_api.py``) and the live service both build the
summary with ``summary`` from the same kinds of records, so the figures follow one
set of rules (ADR-020): nothing is estimated, every ratio carries its denominator,
every known gap is listed in ``missing_data`` and review signals are counted by kind
only. Assurance figures come from the TAX-059 ledger functions.
"""

from __future__ import annotations

import datetime
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from vati_tax.audit import ledger

SIGNAL_KINDS = ("repeated_overrides", "repeated_separation_of_duties_attempts", "ledger_integrity")
OPEN_ORDER = (
    "RECEIVED",
    "VALIDATING",
    "INVESTIGATING",
    "AWAITING_EVIDENCE",
    "AWAITING_HUMAN",
    "FAILED_PROCESSING",
)
OUTSTANDING_REQUESTS = ("recorded", "sent")


def iso(value: datetime.datetime) -> str:
    return value.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hours(value: float) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def duration_stats(hours: list[float], excluded: int = 0) -> dict[str, Any]:
    """Median, nearest-rank 90th percentile and maximum, in hours."""
    # An elapsed time is never negative; a record created within the same second as
    # the summary can read a hair below zero after rounding.
    values = sorted(max(0.0, h) for h in hours)
    if not values:
        return {
            "count": 0,
            "excluded": excluded,
            "median_hours": None,
            "p90_hours": None,
            "max_hours": None,
        }
    n = len(values)
    mid = n // 2
    median = values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2
    p90 = values[max(math.ceil(0.9 * n) - 1, 0)]
    return {
        "count": n,
        "excluded": excluded,
        "median_hours": _hours(median),
        "p90_hours": _hours(p90),
        "max_hours": _hours(values[-1]),
    }


def assurance(
    entries: Sequence[Any] | None,
    viewer_login: str,
    since: datetime.datetime,
    until: datetime.datetime,
    days: int,
) -> dict[str, Any]:
    if entries is None:
        return {
            "available": False,
            "reason": "The audit ledger is not available in this environment.",
        }
    totals = ledger.summary(list(entries), since=since, until=until)
    signals = ledger.integrity_signals(
        list(entries), now=until, viewer=viewer_login, window_days=days
    )
    kinds = Counter(s["kind"] for s in signals["signals"])
    return {
        "available": True,
        "ledger_intact": totals["ledger_intact"],
        "entries": totals["entries"],
        "overrides": totals["by_category"]["override"],
        "refusals": totals["by_category"]["denied"],
        "distinct_actors": totals["distinct_actors"],
        "review_signals": [{"kind": k, "count": kinds[k]} for k in SIGNAL_KINDS if kinds[k]],
        "withheld_about_viewer": signals["withheld_about_viewer"],
    }


def _workload(
    open_cases: Sequence[Mapping[str, Any]],
    officers: Mapping[str, str],
    missing: list[dict[str, str]],
) -> dict[str, Any]:
    status_counts = Counter(c["status"] for c in open_cases)
    rows: dict[str, dict[str, Any]] = {}
    unnamed = 0
    for case in open_cases:
        officer_id = case.get("assigned_officer_id")
        if officer_id is None:
            continue
        if officer_id not in rows:
            name = officers.get(officer_id)
            unnamed += name is None
            rows[officer_id] = {
                "officer_id": officer_id,
                "display_name": name or "Officer with no name on record",
                "open_cases": 0,
                "awaiting_officer_review": 0,
            }
        rows[officer_id]["open_cases"] += 1
        rows[officer_id]["awaiting_officer_review"] += case["status"] == "AWAITING_HUMAN"
    if unnamed:
        detail = f"{_plural(unnamed, 'assigned officer has', 'assigned officers have')} no name on record."
        missing.append({"metric": "workload", "detail": detail})
    return {
        "open_cases": len(open_cases),
        "unassigned_open_cases": sum(c.get("assigned_officer_id") is None for c in open_cases),
        "by_status": [{"status": s, "count": status_counts[s]} for s in OPEN_ORDER],
        "by_officer": sorted(rows.values(), key=lambda r: (r["display_name"], r["officer_id"])),
    }


def summary(
    *,
    tenant_id: str,
    until: datetime.datetime,
    window_days: int,
    open_cases: Sequence[Mapping[str, Any]],
    closed_cases: Sequence[Mapping[str, Any]],
    evidence_requests: Sequence[Mapping[str, Any]],
    findings: Sequence[Mapping[str, Any]],
    officers: Mapping[str, str],
    viewer_login: str,
    entries: Sequence[Any] | None,
    synthetic: bool,
    extra_missing: Sequence[Mapping[str, str]] = (),
) -> dict[str, Any]:
    """The ``ManagementSummary`` body.

    ``open_cases``: ``status``, ``assigned_officer_id``, ``created_at`` (datetime or
    None). ``closed_cases``: ``case_id``, ``created_at``, ``closed_at`` (datetimes or
    None), ``outcome`` (decision outcome or None). ``evidence_requests``: ``case_id``,
    ``status``, ``due_date`` (date). ``findings``: ``case_id``, ``legal_route_status``.
    ``officers`` maps officer IDs to display names. ``entries`` is the tenant's sealed
    ledger, or None when it cannot be read.
    """
    since = until - datetime.timedelta(days=window_days)
    missing: list[dict[str, str]] = [dict(m) for m in extra_missing]
    workload = _workload(open_cases, officers, missing)

    ages = [
        (until - c["created_at"]).total_seconds() / 3600
        for c in open_cases
        if c.get("created_at") is not None
    ]
    if len(ages) < len(open_cases):
        n = len(open_cases) - len(ages)
        detail = f"{_plural(n, 'open case has', 'open cases have')} no creation time and is not measured."
        missing.append({"metric": "open_case_age", "detail": detail})

    closed = [
        c
        for c in closed_cases
        if c.get("closed_at") is not None and since <= c["closed_at"] <= until
    ]
    to_close = [
        (c["closed_at"] - c["created_at"]).total_seconds() / 3600
        for c in closed
        if c.get("created_at") is not None
    ]
    if len(to_close) < len(closed):
        n = len(closed) - len(to_close)
        detail = (
            f"{_plural(n, 'closed case has', 'closed cases have')} no creation time "
            "and is not in the time to close."
        )
        missing.append({"metric": "time_to_close", "detail": detail})
    with_outcome = [c for c in closed if c.get("outcome")]
    if len(with_outcome) < len(closed):
        n = len(closed) - len(with_outcome)
        detail = (
            f"{_plural(n, 'closed case has', 'closed cases have')} no recorded decision outcome "
            "and is excluded from both ratios."
        )
        missing.append({"metric": "closure_outcomes", "detail": detail})
    outcomes = Counter(c["outcome"] for c in with_outcome)

    outstanding = [r for r in evidence_requests if r["status"] in OUTSTANDING_REQUESTS]
    overdue = [r for r in outstanding if r["due_date"] < until.date()]

    in_scope = {c["case_id"] for c in open_cases} | {c["case_id"] for c in closed}
    scoped = [f for f in findings if f["case_id"] in in_scope]
    routes = Counter(f["legal_route_status"] for f in scoped)

    checks = assurance(entries, viewer_login, since, until, window_days)
    if not checks["available"]:
        missing.append({"metric": "assurance", "detail": checks["reason"]})

    return {
        "tenant_id": tenant_id,
        "generated_at": iso(until),
        "window": {"since": iso(since), "until": iso(until), "days": window_days},
        "workload": workload,
        "elapsed": {
            "open_case_age": duration_stats(ages, len(open_cases) - len(ages)),
            "time_to_close": duration_stats(to_close, len(closed) - len(to_close)),
        },
        "unresolved_evidence": {
            "outstanding_requests": len(outstanding),
            "overdue_requests": len(overdue),
            "cases_with_outstanding_requests": len({r["case_id"] for r in outstanding}),
            "open_cases_awaiting_evidence": sum(
                c["status"] == "AWAITING_EVIDENCE" for c in open_cases
            ),
        },
        "closure_outcomes": {
            "closed_in_window": len(closed),
            "without_recorded_outcome": len(closed) - len(with_outcome),
            "no_further_action": {
                "numerator": outcomes["close_no_further_action"],
                "denominator": len(with_outcome),
            },
            "after_correction": {
                "numerator": outcomes["close_after_correction"],
                "denominator": len(with_outcome),
            },
        },
        "legal_route_coverage": {
            "verified_by_reviewer": {
                "numerator": routes["verified_by_reviewer"],
                "denominator": len(scoped),
            },
            "unverified": routes["unverified"],
            "no_verified_route": routes["no_verified_route"],
        },
        "assurance": checks,
        "missing_data": missing,
        "synthetic": synthetic,
    }
