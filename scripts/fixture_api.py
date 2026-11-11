"""Fixture implementation of selected Vati /api/v1 operations for UI development.

Serves listCases, getCase and getTaxpayer from integration/api/fixtures/auditor_queue.json
(TAX-040), listEvidence and listEntities from integration/api/fixtures/case_records.json
(TAX-041), the officer review operations getCaseReview, listFindings,
proposeDecision, reviewDecision and requestEvidence from ``fixture_review`` (TAX-042),
and the supervisor-only getManagementSummary (TAX-060) measured from those cases plus
integration/api/fixtures/management.json, and the supervisor-only getIntegrityReview
(TAX-077) from ``fixture_integrity``.
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
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "integration" / "api"
FIXTURE_PATH = API_DIR / "fixtures" / "auditor_queue.json"
MANAGEMENT_PATH = API_DIR / "fixtures" / "management.json"
BACKEND_DIR = ROOT / "backend" / "vati_tax"
RECORDS_PATH = API_DIR / "fixtures" / "case_records.json"
# listEvidence/listEntities -> list key in case_records.json (TAX-041). listFindings is
# served by fixture_review (TAX-042), the one source of findings.
CASE_RECORD_OPERATIONS = {
    "listEvidence": "evidence",
    "listEntities": "entities",
}
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import contract  # noqa: E402
import fixture_integrity  # noqa: E402  (TAX-077 getIntegrityReview)
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


def _load_synthetic(path: Path) -> dict[str, Any]:
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
# Measured from the fixture records with the same rules the live Vati service uses
# (vati_tax.api_v1.management, TAX-090) over Jube's case projection, Frappe's decisions
# and evidence requests, the findings store and the TAX-059 ledger. Nothing is
# estimated: every ratio carries its denominator and every known gap is listed in
# missing_data.

_FIXTURE_EVENT_NS = uuid.UUID("5a7e0000-0000-4000-8000-000000000060")


def _ts(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def duration_stats(hours: list[float], excluded: int = 0) -> dict[str, Any]:
    """Median, nearest-rank 90th percentile and maximum, in hours (shared rules)."""
    result: dict[str, Any] = management_module().duration_stats(hours, excluded)
    return result


def management_module() -> Any:
    """The Frappe-free summary rules the live Vati service uses (TAX-090)."""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    return importlib.import_module("vati_tax.api_v1.management")


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
    if entries is None and "ledger_events" in mgmt:
        entries = ledger_entries(principal["tenant_id"], mgmt["ledger_events"])
    open_cases = [
        {**c, "created_at": _ts(c.get("created_at"))}
        for c in data["cases"]
        if c["status"] in OPEN_STATUSES
    ]
    closed_cases = [
        {**c, "created_at": _ts(c.get("created_at")), "closed_at": _ts(c["closed_at"])}
        for c in mgmt["closed_cases"]
    ]
    requests = [
        {**r, "due_date": datetime.date.fromisoformat(r["due_date"])}
        for r in mgmt["evidence_requests"]
    ]
    body: dict[str, Any] = management_module().summary(
        tenant_id=principal["tenant_id"],
        until=until,
        window_days=window_days,
        open_cases=open_cases,
        closed_cases=closed_cases,
        evidence_requests=requests,
        findings=mgmt["findings"],
        officers={o["officer_id"]: o["display_name"] for o in mgmt["officers"]},
        viewer_login=viewer_login,
        entries=entries,
        synthetic=True,
    )
    return body


def load_fixture(path: Path = FIXTURE_PATH, records: Path = RECORDS_PATH) -> dict[str, Any]:
    data = _load_synthetic(path)
    data["case_records"] = _load_synthetic(records)["cases"]
    return data


def _list_case_records(
    data: dict[str, Any], case_id: str, kind: str, query: dict[str, str]
) -> Response | str:
    """Evidence or entities of one visible case, paged like listCases."""
    if not any(c["case_id"] == case_id for c in data["cases"]):
        return "not_found"
    records = data.get("case_records", {}).get(case_id)
    if records is None:
        return "not_found"
    items = list(records[kind])
    limit = int(query.get("limit", "25"))
    cursor = query.get("cursor", "0")
    if not cursor.isdigit() or int(cursor) > len(items):
        return "invalid_request"
    start = int(cursor)
    more = start + limit < len(items)
    meta = {"next_cursor": str(start + limit) if more else None, "has_more": more}
    return 200, {}, {"data": items[start : start + limit], "meta": meta}


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


# TAX-146: what the app shell may show. Names are synthetic; the native investigation and
# explore screens are permitted for officers but not connected until TAX-106.
FIXTURE_TENANT_NAME = "Synthetic fixture tenant"
NOT_CONNECTED_APPS = ("investigation", "explore")


def workspace_session(tenant_id: str, acting: dict[str, Any]) -> dict[str, Any]:
    """The fixture officer's workspace scope (getWorkspaceSession)."""
    roles = list(acting["roles"])
    supervisor = "supervisor" in roles
    apps: list[dict[str, Any]] = [{"key": "cases", "availability": "available", "reason": None}]
    apps += [
        {"key": key, "availability": "unavailable", "reason": "not_connected"}
        for key in NOT_CONNECTED_APPS
    ]
    views = ["overview", "case_queue"]
    if supervisor:
        apps.append({"key": "reports", "availability": "available", "reason": None})
        views += ["management", "integrity"]
    return {
        "tenant_id": tenant_id,
        "tenant_name": FIXTURE_TENANT_NAME,
        "officer_id": acting["officer_id"],
        "display_name": f"Synthetic {roles[0]}",
        "roles": roles,
        "apps": apps,
        "views": views,
        "synthetic": True,
    }


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
    # Statuses a fixture approval changed (the Jube projection in fixture mode) and the
    # TAX-147 attention fields computed from the review state.
    review.ensure_seeded(data["cases"])
    data = {**data, "cases": [review.overlay_case(c) for c in data["cases"]]}
    result: Response | str
    if op_id == "listCases":
        result = _list_cases(data, query)
    elif op_id == "getCase":
        item = next((c for c in data["cases"] if c["case_id"] == params["case_id"]), None)
        result = (200, {}, {"data": item}) if item else "not_found"
    elif op_id == "getTaxpayer":
        result = _get_taxpayer(data, params["taxpayer_id"])
    elif op_id in CASE_RECORD_OPERATIONS:
        kind = CASE_RECORD_OPERATIONS[op_id]
        result = _list_case_records(data, params["case_id"], kind, query)
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
    elif op_id == "getWorkspaceSession":
        # TAX-146: the same acting officer as the review operations (X-Vati-Fixture-Role).
        role = headers.get(fixture_review.ROLE_HEADER) or "auditor"
        acting = review.review["principals"].get(role)
        if acting is None:
            return _error("invalid_request", correlation)
        result = (200, {}, {"data": workspace_session(data["principal"]["tenant_id"], acting)})
    elif op_id == "getIntegrityReview":
        # TAX-077: same acting officer and supervisor-only rule as the summary.
        role = headers.get(fixture_review.ROLE_HEADER) or "auditor"
        acting = review.review["principals"].get(role)
        if acting is None:
            return _error("invalid_request", correlation)
        days = int(query.get("window_days", "30"))
        body_or_error = fixture_integrity.integrity_review(
            data["principal"]["tenant_id"],
            fixture_integrity.load_integrity(),
            days,
            viewer=acting,
        )
        if body_or_error == "forbidden":
            return _error("forbidden", correlation, fixture_integrity.FORBIDDEN_MESSAGE)
        result = (
            body_or_error if isinstance(body_or_error, str) else (200, {}, {"data": body_or_error})
        )
    else:
        # Contract operations without fixture data are absent here, never invented.
        return _error("not_found", correlation, "This operation has no fixture data.")
    return _error(result, correlation) if isinstance(result, str) else result
