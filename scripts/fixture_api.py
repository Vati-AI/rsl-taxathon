"""Read-only fixture implementation of the Vati /api/v1 queue operations (TAX-040).

Serves listCases, getCase and getTaxpayer from integration/api/fixtures/auditor_queue.json
for UI development. Requests are checked with the contract's own rules
(integration/api/contract.py), so missing or malformed tenant headers, bad IDs and
unknown routes fail exactly as the contract specifies. The fixture principal may see one
tenant; any other tenant is 403 tenant_scope_denied. Responses carry
``X-Vati-Mode: fixture`` and every record is synthetic. There are no mutations and no
fallback to or from live services.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "integration" / "api"
FIXTURE_PATH = API_DIR / "fixtures" / "auditor_queue.json"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import contract  # noqa: E402

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
    "not_found": "The requested resource was not found.",
}

Response = tuple[int, dict[str, str], dict[str, Any]]


def load_fixture(path: Path = FIXTURE_PATH) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if data.get("synthetic") is not True:
        raise ValueError("fixture data must be marked synthetic")
    return data


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
) -> Response:
    """Return (status, extra headers, JSON body) for a fixture /api/v1 request."""
    data = data if data is not None else load_fixture()
    spec = spec if spec is not None else contract.load_spec()
    lowered = {k.lower(): v for k, v in headers.items()}
    correlation = _correlation(lowered)
    status, extra, body = _dispatch(method, path, lowered, query, data, spec, correlation)
    return status, {**extra, "X-Correlation-ID": correlation, MODE_HEADER: "fixture"}, body


def _dispatch(
    method: str,
    path: str,
    headers: dict[str, str],
    query: dict[str, str],
    data: dict[str, Any],
    spec: dict[str, Any],
    correlation: str,
) -> Response:
    match = contract.match_operation(spec, method, path)
    if match is None or method.lower() != "get":
        return _error("not_found", correlation)
    violations = contract.check_request(spec, method, path, headers, None, query)
    if violations:
        return _error(violations[0].code, correlation)
    if headers.get(contract.TENANT_HEADER) != data["principal"]["tenant_id"]:
        return _error("tenant_scope_denied", correlation)
    _, op, params = match
    result: Response | str
    if op["operationId"] == "listCases":
        result = _list_cases(data, query)
    elif op["operationId"] == "getCase":
        item = next((c for c in data["cases"] if c["case_id"] == params["case_id"]), None)
        result = (200, {}, {"data": item}) if item else "not_found"
    elif op["operationId"] == "getTaxpayer":
        result = _get_taxpayer(data, params["taxpayer_id"])
    else:
        # Contract operations without fixture data are absent here, never invented.
        return _error("not_found", correlation, "This operation has no fixture data.")
    return _error(result, correlation) if isinstance(result, str) else result
