"""Frappe-free router for the Vati /api/v1 contract (TAX-090, ADR-046).

``handle`` takes one request for a contract path (``/api/v1/...``) and returns the
exact contract response: status, headers and JSON body. It applies the contract's
own request rules (``integration/api/contract.py``: tenant header, IDs, query,
Idempotency-Key and body schemas), so the live service and the fixture server
reject the same input with the same code. Operation handlers come from the caller
(``backend.HANDLERS`` in Frappe, fakes in the tests); a contract operation without
a handler is ``404 not_found``, never invented data.

Every error uses the ADR-003 envelope and echoes ``X-Correlation-ID``. Handlers raise
``ApiError`` (or anything ``translate`` turns into one). As defence in depth, a 2xx
body whose items name another tenant, case or taxpayer than the request is never
sent: it becomes ``503 upstream_unavailable`` and is reported to ``on_error``.
"""

from __future__ import annotations

import base64
import binascii
import importlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

MODE_HEADER = "X-Vati-Mode"
MODE = "live"
REPLAYED_HEADER = "Idempotent-Replayed"
CORRELATION_HEADER = "X-Correlation-ID"
DEFAULT_LIMIT = 25

# Officer IDs: the contract types officers as UUIDs; Frappe identifies them by user
# login (ADR-013). One stable UUID per login, the same in every tenant (ADR-046).
OFFICER_NAMESPACE = uuid.UUID("5a7e0090-0000-4000-8000-0000000000a1")

MESSAGES = {
    "invalid_request": "The request is not valid for this operation.",
    "missing_tenant_context": "A valid X-Vati-Tenant-ID header is required.",
    "invalid_case_id": "The case identifier is missing or malformed.",
    "unauthenticated": "Sign in to use the case service.",
    "forbidden": "You are not allowed to perform this operation.",
    "tenant_scope_denied": "The requested tenant is outside your permitted scope.",
    "not_found": "The requested resource was not found.",
    "version_conflict": "The record changed since you loaded it.",
    "idempotency_in_progress": "The original request with this Idempotency-Key is still running.",
    "idempotency_conflict": "This Idempotency-Key was used for a different request.",
    "invalid_domain_input": "The request cannot be processed.",
    "rate_limited": "Too many requests.",
    "upstream_unavailable": "A service the case service depends on is unavailable.",
    "upstream_timeout": "A service the case service depends on did not answer in time.",
}
RETRYABLE = frozenset(
    {"idempotency_in_progress", "rate_limited", "upstream_unavailable", "upstream_timeout"}
)


class ApiError(Exception):
    """One contract error (``code`` from ``contract.ERROR_STATUS``)."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        field_errors: Sequence[Mapping[str, Any]] = (),
        retryable: bool | None = None,
        current_version: int | None = None,
    ) -> None:
        super().__init__(message or MESSAGES.get(code, "The request could not be completed."))
        self.code = code
        self.field_errors = [dict(f) for f in field_errors]
        self.retryable = code in RETRYABLE if retryable is None else retryable
        self.current_version = current_version


@dataclass(frozen=True)
class ApiRequest:
    method: str
    path: str
    headers: Mapping[str, str]
    query: Mapping[str, str] = field(default_factory=dict)
    body: Any = None
    # The body was present but was not a JSON value.
    body_invalid: bool = False


@dataclass(frozen=True)
class Call:
    """What a handler receives: the matched operation and its checked inputs."""

    operation_id: str
    params: Mapping[str, str]
    query: Mapping[str, str]
    body: Any
    tenant_id: str
    correlation_id: str
    idempotency_key: str | None


@dataclass(frozen=True)
class Result:
    """A handler's success body (``{"data": ...}``, plus ``meta`` for lists)."""

    body: dict[str, Any]
    replayed: bool = False


@dataclass(frozen=True)
class ApiResponse:
    status: int
    headers: dict[str, str]
    body: dict[str, Any]


Handler = Callable[[Call], Result]


def officer_id(user: str) -> str:
    """The contract officer UUID for a Frappe user login (stable, case-insensitive)."""
    return str(uuid.uuid5(OFFICER_NAMESPACE, user.strip().lower()))


def _contract() -> Any:
    # integration/api/contract.py: on the bench path through vati_shared (ADR-035) and on
    # the repository path in tests. Imported by name so type checking sees one module.
    return importlib.import_module("integration.api.contract")


def load_spec() -> dict[str, Any]:
    spec: dict[str, Any] = _contract().load_spec()
    return spec


def error_response(error: ApiError, correlation_id: str) -> ApiResponse:
    contract = _contract()
    payload: dict[str, Any] = {
        "code": error.code,
        "message": str(error),
        "retryable": error.retryable,
        "correlation_id": correlation_id,
        "service": "vati",
    }
    if error.field_errors:
        payload["field_errors"] = error.field_errors
    if error.current_version is not None:
        payload["current_version"] = error.current_version
    return ApiResponse(
        int(contract.ERROR_STATUS[error.code]),
        {CORRELATION_HEADER: correlation_id, MODE_HEADER: MODE},
        {"error": payload},
    )


def _correlation(headers: Mapping[str, str]) -> str:
    value = headers.get("x-correlation-id", "")
    return value if _contract().UUID_RE.match(value) else str(uuid.uuid4())


def _success_status(op: Mapping[str, Any]) -> int:
    return int(next(code for code in op["responses"] if code.startswith("2")))


def _items(body: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    data = body.get("data")
    if isinstance(data, Mapping):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, Mapping)]
    return []


def scope_mismatch(body: Mapping[str, Any], call: Call) -> str | None:
    """Why a success body must not be sent for this request, or None."""
    for item in _items(body):
        if "tenant_id" in item and item["tenant_id"] != call.tenant_id:
            return "tenant_id"
        for key in ("case_id", "taxpayer_id"):
            if key in call.params and key in item and item[key] != call.params[key]:
                return key
    return None


def handle(
    request: ApiRequest,
    handlers: Mapping[str, Handler],
    *,
    authenticated: bool,
    spec: dict[str, Any] | None = None,
    translate: Callable[[Exception], ApiError | None] | None = None,
    on_error: Callable[[str, Exception | None], None] | None = None,
) -> ApiResponse:
    """The contract response for ``request`` (never raises for request problems)."""
    contract = _contract()
    spec = spec if spec is not None else load_spec()
    headers = {k.lower(): v for k, v in request.headers.items()}
    correlation = _correlation(headers)
    if not authenticated:
        return error_response(ApiError("unauthenticated"), correlation)
    path = request.path.split("?", 1)[0]
    match = contract.match_operation(spec, request.method, path)
    if match is None or not path.startswith("/api/v1/"):
        return error_response(ApiError("not_found"), correlation)
    _, op, params = match
    handler = handlers.get(op["operationId"])
    if handler is None:
        return error_response(
            ApiError("not_found", "This operation is not served by the case service."),
            correlation,
        )
    if request.body_invalid:
        return error_response(ApiError("invalid_request", "The body is not JSON."), correlation)
    body = request.body if request.method.lower() in contract.MUTATING_METHODS else None
    violations = contract.check_request(
        spec, request.method, path, headers, body, dict(request.query)
    )
    if request.method.lower() not in contract.MUTATING_METHODS and request.body is not None:
        violations.append(contract.Violation("invalid_request", "a GET takes no body"))
    if violations:
        return error_response(ApiError(violations[0].code), correlation)
    call = Call(
        operation_id=op["operationId"],
        params=params,
        query=dict(request.query),
        body=body,
        tenant_id=headers[contract.TENANT_HEADER],
        correlation_id=correlation,
        idempotency_key=headers.get(contract.IDEMPOTENCY_HEADER),
    )
    try:
        result = handler(call)
    except ApiError as exc:
        return error_response(exc, correlation)
    except Exception as exc:  # translated below; anything else is reported, never echoed
        translated = translate(exc) if translate else None
        if translated is None:
            if on_error:
                on_error("unhandled", exc)
            translated = ApiError(
                "upstream_unavailable", "The case service could not complete the request."
            )
        return error_response(translated, correlation)
    mismatch = scope_mismatch(result.body, call)
    if mismatch:
        if on_error:
            on_error(f"scope_mismatch:{mismatch}", None)
        return error_response(
            ApiError("upstream_unavailable", "The case service could not complete the request."),
            correlation,
        )
    out_headers = {CORRELATION_HEADER: correlation, MODE_HEADER: MODE}
    if result.replayed:
        out_headers[REPLAYED_HEADER] = "true"
    return ApiResponse(_success_status(op), out_headers, result.body)


# --- paging -------------------------------------------------------------------------


def encode_cursor(key: Sequence[str]) -> str:
    raw = json.dumps(list(key), separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, ...]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise ApiError("invalid_request", "The cursor is not valid.") from None
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        raise ApiError("invalid_request", "The cursor is not valid.")
    return tuple(value)


def page(
    items: Sequence[dict[str, Any]],
    key: Callable[[dict[str, Any]], tuple[str, ...]],
    query: Mapping[str, str],
) -> dict[str, Any]:
    """One page of ``items`` in ``key`` order (a unique key: stable keyset paging)."""
    limit = int(query.get("limit") or DEFAULT_LIMIT)
    ordered = sorted(items, key=key)
    if query.get("cursor"):
        after = decode_cursor(query["cursor"])
        ordered = [item for item in ordered if key(item) > after]
    chunk = ordered[:limit]
    more = len(ordered) > limit
    return {
        "data": chunk,
        "meta": {"next_cursor": encode_cursor(key(chunk[-1])) if more else None, "has_more": more},
    }
