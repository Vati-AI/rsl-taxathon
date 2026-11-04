"""Fixture implementation of selected Vati /api/v1 operations for UI development.

Serves listCases, getCase and getTaxpayer from integration/api/fixtures/auditor_queue.json
(TAX-040), the officer review operations getCaseReview, listFindings,
proposeDecision, reviewDecision and requestEvidence from ``fixture_review`` (TAX-042),
and the supervisor-only getManagementSummary (TAX-060) measured from those cases plus
integration/api/fixtures/management.json.
Requests are checked with the contract's own rules (integration/api/contract.py), so
missing or malformed tenant headers, bad IDs, invalid bodies and unknown routes fail
exactly as the contract specifies. The fixture principal may see one tenant; any other
tenant is 403 tenant_scope_denied. The acting officer comes from the fixture-only
``X-Vati-Fixture-Role`` header (see ``fixture_review``). Responses carry
``X-Vati-Mode: fixture`` and every record is synthetic. Review actions change in-memory
fixture state only; there is no fallback to or from live services.
"""

from __future__ import annotations

import datetime
import importlib
import json
import math
import sys
import uuid
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "integration" / "api"
FIXTURE_PATH = API_DIR / "fixtures" / "auditor_queue.json"
MANAGEMENT_PATH = API_DIR / "fixtures" / "management.json"
BACKEND_DIR = ROOT / "backend" / "vati_tax"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import contract  # noqa: E402
import fixture_review  # noqa: E402

MODE_HEADER = "X-Vati-Mode"
OPEN_STATUSES = {
    "RECEIVED",
    "VALIDATING",
    "INVESTIGATING",
    "AWAITING_EVIDENCE",
    "AWAITING_HUMAN",
    "FAILED_PROCESSING",
}
MESSAGES = {
    "invalid_request": "The request is not valid for this operation.",
    "missing_tenant_context": "A valid X-Vati-Tenant-ID header is required.",
    "invalid_case_id": "The case identifier is missing or malformed.",
    "tenant_scope_denied": "The requested tenant is outside your permitted scope.",
    "forbidden": "Only supervisors can view management figures.",
    "not_found": "The requested resource was not found.",
}

Response = tuple[int, dict[str, str], dict[str, Any]]


def load_fixture(path: Path = FIXTURE_PATH) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if data.get("synthetic") is not True:
        raise ValueError("fixture data must be marked synthetic")
    return data


def load_management(path: Path = MANAGEMENT_PATH) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if data.get("synthetic") is not True:
        raise ValueError("management fixture data must be marked synthetic")
    return data


# --- management summary (TAX-060) ----------------------------------------------------
#
# Measured from the fixture records, as the Vati service will measure them from Jube
# (open cases), Frappe (decisions, evidence requests), the findings store and the
# TAX-059 ledger. Nothing is estimated: every ratio carries its denominator and every
# known gap is listed in missing_data.

_FIXTURE_EVENT_NS = uuid.UUID("5a7e0000-0000-4000-8000-000000000060")
_SIGNAL_KINDS = ("repeated_overrides", "repeated_separation_of_duties_attempts", "ledger_integrity")
_OPEN_ORDER = (
    "RECEIVED",
    "VALIDATING",
    "INVESTIGATING",
    "AWAITING_EVIDENCE",
    "AWAITING_HUMAN",
    "FAILED_PROCESSING",
)


def _ts(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(value: datetime.datetime) -> str:
    return value.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hours(value: float) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def duration_stats(hours: list[float], excluded: int = 0) -> dict[str, Any]:
    """Median, nearest-rank 90th percentile and maximum, in hours."""
    values = sorted(hours)
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


def ledger_module() -> Any:
    """The Frappe-free TAX-059 ledger functions, so fixture figures follow the real rules."""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    return importlib.import_module("vati_tax.audit.ledger")


def ledger_entries(tenant_id: str, events: list[dict[str, Any]]) -> list[Any]:
    """Seal the fixture ledger events into one tenant chain with the TAX-059 code."""
    ledger = ledger_module()
    store = ledger.MemoryLedger()
    for index, event in enumerate(events):
        key = f"{index}|{event['action']}|{event['occurred_at']}"
        ledger.append(
            store,
            ledger.Unsealed(
                event_id=str(uuid.uuid5(_FIXTURE_EVENT_NS, key)),
                tenant_id=tenant_id,
                action=event["action"],
                outcome="denied" if event["action"].endswith(".denied") else "ok",
                actor=event["actor"],
                actor_role=event["actor_role"],
                occurred_at=_ts(event["occurred_at"]),
                correlation_id=str(uuid.uuid5(_FIXTURE_EVENT_NS, "correlation|" + key)),
                subject_type=event["subject_type"],
                subject_id=event.get("case_id") or event["subject_type"],
                payload_hash=ledger.digest(event),
                case_id=event.get("case_id"),
                target_actor=event.get("target_actor"),
                reason=event.get("reason"),
            ),
        )
    return list(store.entries(tenant_id))


def _assurance(
    entries: list[Any] | None,
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
    ledger = ledger_module()
    summary = ledger.summary(entries, since=since, until=until)
    signals = ledger.integrity_signals(entries, now=until, viewer=viewer_login, window_days=days)
    kinds = Counter(s["kind"] for s in signals["signals"])
    return {
        "available": True,
        "ledger_intact": summary["ledger_intact"],
        "entries": summary["entries"],
        "overrides": summary["by_category"]["override"],
        "refusals": summary["by_category"]["denied"],
        "distinct_actors": summary["distinct_actors"],
        "review_signals": [{"kind": k, "count": kinds[k]} for k in _SIGNAL_KINDS if kinds[k]],
        "withheld_about_viewer": signals["withheld_about_viewer"],
    }


def _workload(
    open_cases: list[dict[str, Any]],
    officers: dict[str, dict[str, Any]],
    missing: list[dict[str, str]],
) -> dict[str, Any]:
    status_counts = Counter(c["status"] for c in open_cases)
    rows: dict[str, dict[str, Any]] = {}
    unnamed = 0
    for case in open_cases:
        officer_id = case["assigned_officer_id"]
        if officer_id is None:
            continue
        if officer_id not in rows:
            known = officers.get(officer_id)
            unnamed += known is None
            rows[officer_id] = {
                "officer_id": officer_id,
                "display_name": known["display_name"]
                if known
                else "Officer with no name on record",
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
        "unassigned_open_cases": sum(c["assigned_officer_id"] is None for c in open_cases),
        "by_status": [{"status": s, "count": status_counts[s]} for s in _OPEN_ORDER],
        "by_officer": sorted(rows.values(), key=lambda r: (r["display_name"], r["officer_id"])),
    }


def management_summary(
    data: dict[str, Any],
    mgmt: dict[str, Any],
    window_days: int,
    *,
    viewer: dict[str, Any],
    entries: list[Any] | None = None,
) -> dict[str, Any] | str:
    """The getManagementSummary body for the acting officer, or an error code.

    ``viewer`` is the acting fixture principal (``officer_id`` and ``roles``); only the
    supervisor role may read the summary. ``entries`` replaces the sealed fixture ledger
    (tests use it to tamper with the chain).
    """
    principal = data["principal"]
    officers = {o["officer_id"]: o for o in mgmt["officers"]}
    if "supervisor" not in viewer["roles"]:
        return "forbidden"
    record = officers.get(viewer["officer_id"])
    if record is None:
        # Without a ledger identity the viewer's own signals could not be withheld.
        return "forbidden"
    viewer_login = record["login"]
    until = _ts(mgmt["as_of"])
    if until is None:
        raise ValueError("management fixture needs as_of")
    since = until - datetime.timedelta(days=window_days)
    missing: list[dict[str, str]] = []

    open_cases = [c for c in data["cases"] if c["status"] in OPEN_STATUSES]
    workload = _workload(open_cases, officers, missing)

    ages: list[float] = []
    for case in open_cases:
        created = _ts(case.get("created_at"))
        if created is not None:
            ages.append((until - created).total_seconds() / 3600)
    if len(ages) < len(open_cases):
        n = len(open_cases) - len(ages)
        detail = f"{_plural(n, 'open case has', 'open cases have')} no creation time and is not measured."
        missing.append({"metric": "open_case_age", "detail": detail})

    closed = []
    for case in mgmt["closed_cases"]:
        closed_at = _ts(case["closed_at"])
        if closed_at is not None and since <= closed_at <= until:
            closed.append((case, closed_at))
    to_close: list[float] = []
    for case, closed_at in closed:
        created = _ts(case.get("created_at"))
        if created is not None:
            to_close.append((closed_at - created).total_seconds() / 3600)
    if len(to_close) < len(closed):
        n = len(closed) - len(to_close)
        detail = (
            f"{_plural(n, 'closed case has', 'closed cases have')} no creation time "
            "and is not in the time to close."
        )
        missing.append({"metric": "time_to_close", "detail": detail})
    with_outcome = [case for case, _ in closed if case.get("outcome")]
    if len(with_outcome) < len(closed):
        n = len(closed) - len(with_outcome)
        detail = (
            f"{_plural(n, 'closed case has', 'closed cases have')} no recorded decision outcome "
            "and is excluded from both ratios."
        )
        missing.append({"metric": "closure_outcomes", "detail": detail})
    outcomes = Counter(case["outcome"] for case in with_outcome)

    outstanding = [r for r in mgmt["evidence_requests"] if r["status"] in ("recorded", "sent")]
    overdue = [r for r in outstanding if datetime.date.fromisoformat(r["due_date"]) < until.date()]

    in_scope = {c["case_id"] for c in open_cases} | {case["case_id"] for case, _ in closed}
    findings = [f for f in mgmt["findings"] if f["case_id"] in in_scope]
    routes = Counter(f["legal_route_status"] for f in findings)

    if entries is None and "ledger_events" in mgmt:
        entries = ledger_entries(principal["tenant_id"], mgmt["ledger_events"])
    assurance = _assurance(entries, viewer_login, since, until, window_days)
    if not assurance["available"]:
        missing.append({"metric": "assurance", "detail": assurance["reason"]})

    return {
        "tenant_id": principal["tenant_id"],
        "generated_at": _iso(until),
        "window": {"since": _iso(since), "until": _iso(until), "days": window_days},
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
                "denominator": len(findings),
            },
            "unverified": routes["unverified"],
            "no_verified_route": routes["no_verified_route"],
        },
        "assurance": assurance,
        "missing_data": missing,
        "synthetic": True,
    }


def _error(code: str, correlation: str, message: str | None = None) -> Response:
    status = int(contract.ERROR_STATUS[code])
    body = {
        "error": {
            "code": code,
            "message": message or MESSAGES.get(code, "The request could not be completed."),
            "retryable": False,
            "correlation_id": correlation,
            "service": "vati",
        }
    }
    return status, {}, body


def _correlation(headers: dict[str, str]) -> str:
    value = headers.get(contract.CORRELATION_HEADER, "")
    return value if contract.UUID_RE.match(value) else str(uuid.uuid4())


def _list_cases(data: dict[str, Any], query: dict[str, str]) -> Response | str:
    officer = data["principal"]["officer_id"]
    cases = list(data["cases"])
    assigned = query.get("assigned_to")
    if assigned == "me":
        cases = [c for c in cases if c["assigned_officer_id"] == officer]
    elif assigned == "unassigned":
        cases = [c for c in cases if c["assigned_officer_id"] is None]
    if "status" in query:
        cases = [c for c in cases if c["status"] == query["status"]]
    if "refund_lane" in query:
        cases = [c for c in cases if c["refund_lane"] == query["refund_lane"]]
    cases.sort(key=lambda c: c["display_id"])
    limit = int(query.get("limit", "25"))
    cursor = query.get("cursor", "0")
    if not cursor.isdigit() or int(cursor) > len(cases):
        return "invalid_request"
    start = int(cursor)
    page = cases[start : start + limit]
    more = start + limit < len(cases)
    meta = {"next_cursor": str(start + limit) if more else None, "has_more": more}
    return 200, {}, {"data": page, "meta": meta}


def _get_taxpayer(data: dict[str, Any], taxpayer_id: str) -> Response | str:
    record = next((t for t in data["taxpayers"] if t["taxpayer_id"] == taxpayer_id), None)
    visible = [c for c in data["cases"] if c["taxpayer_id"] == taxpayer_id]
    if record is None or not visible:
        return "not_found"
    open_cases = [
        {"case_id": c["case_id"], "display_id": c["display_id"], "status": c["status"]}
        for c in sorted(visible, key=lambda c: c["display_id"])
        if c["status"] in OPEN_STATUSES
    ]
    return 200, {}, {"data": {**record, "open_cases": open_cases}}


def handle(
    method: str,
    path: str,
    headers: dict[str, str],
    query: dict[str, str],
    data: dict[str, Any] | None = None,
    spec: dict[str, Any] | None = None,
    body: Any = None,
    review: fixture_review.ReviewStore | None = None,
    management: dict[str, Any] | None = None,
) -> Response:
    """Return (status, extra headers, JSON body) for a fixture /api/v1 request."""
    data = data if data is not None else load_fixture()
    spec = spec if spec is not None else contract.load_spec()
    review = review if review is not None else fixture_review.STORE
    lowered = {k.lower(): v for k, v in headers.items()}
    correlation = _correlation(lowered)
    status, extra, payload = _dispatch(
        method, path, lowered, query, data, spec, correlation, body, review, management
    )
    return status, {**extra, "X-Correlation-ID": correlation, MODE_HEADER: "fixture"}, payload


def _dispatch(
    method: str,
    path: str,
    headers: dict[str, str],
    query: dict[str, str],
    data: dict[str, Any],
    spec: dict[str, Any],
    correlation: str,
    body: Any,
    review: fixture_review.ReviewStore,
    management: dict[str, Any] | None = None,
) -> Response:
    match = contract.match_operation(spec, method, path)
    if match is None:
        return _error("not_found", correlation)
    _, op, params = match
    op_id = op["operationId"]
    if method.lower() != "get" and op_id not in fixture_review.OPERATIONS:
        return _error("not_found", correlation, "This operation has no fixture data.")
    violations = contract.check_request(spec, method, path, headers, body, query)
    if violations:
        return _error(violations[0].code, correlation)
    if headers.get(contract.TENANT_HEADER) != data["principal"]["tenant_id"]:
        return _error("tenant_scope_denied", correlation)
    if op_id in fixture_review.OPERATIONS:
        return review.handle(op_id, params, headers, body, data["cases"], correlation)
    # Statuses a fixture approval changed (the Jube projection in fixture mode).
    data = {**data, "cases": [review.overlay_case(c) for c in data["cases"]]}
    result: Response | str
    if op_id == "listCases":
        result = _list_cases(data, query)
    elif op_id == "getCase":
        item = next((c for c in data["cases"] if c["case_id"] == params["case_id"]), None)
        result = (200, {}, {"data": item}) if item else "not_found"
    elif op_id == "getTaxpayer":
        result = _get_taxpayer(data, params["taxpayer_id"])
    elif op_id == "getManagementSummary":
        # The same acting officer as the review operations (X-Vati-Fixture-Role).
        role = headers.get(fixture_review.ROLE_HEADER) or "auditor"
        acting = review.review["principals"].get(role)
        if acting is None:
            return _error("invalid_request", correlation)
        mgmt = management if management is not None else load_management()
        days = int(query.get("window_days", "30"))
        summary = management_summary(data, mgmt, days, viewer=acting)
        result = summary if isinstance(summary, str) else (200, {}, {"data": summary})
    else:
        # Contract operations without fixture data are absent here, never invented.
        return _error("not_found", correlation, "This operation has no fixture data.")
    return _error(result, correlation) if isinstance(result, str) else result
