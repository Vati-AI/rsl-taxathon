"""The whitelisted entry point of the Vati /api/v1 service in Frappe (TAX-090, ADR-046).

Frappe v16 owns every ``/api/v1/*`` URL (its ``/api/v1/method`` and
``/api/v1/resource`` routes) and offers apps no hook to add routes there. So the
contract is served by one whitelisted method with the contract path in ``path``::

    GET  /api/method/vati_tax.api_v1.api.route?path=/api/v1/cases&status=AWAITING_HUMAN
    POST /api/method/vati_tax.api_v1.api.route?path=/api/v1/cases/<id>/decisions

The response is the exact contract response (status, headers, JSON body), not
Frappe's ``{"message": ...}`` wrapper, so a client or a reverse proxy (TAX-091,
TAX-117) can map ``/api/v1/...`` onto it one to one. Sign-in is Frappe's session
(or API key) and POSTs need Frappe's CSRF token like every other session POST.
Guests get ``401 unauthenticated``. Contract query parameters travel next to
``path``; the JSON request body is read as sent.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from werkzeug.wrappers import Response

from vati_tax.api_v1 import backend, core

PATH_PARAM = "path"


def _logger() -> Any:
    return frappe.logger("vati_tax.api_v1")


def _report(kind: str, exc: Exception | None) -> None:
    # IDs and kinds only: never request bodies, document text or taxpayer details.
    _logger().error({"event": "api_v1_error", "kind": kind, "error": type(exc).__name__})
    if exc is not None:
        frappe.log_error(title=f"vati api_v1 {kind}")


def _request() -> core.ApiRequest:
    request = frappe.request
    query = {k: v for k, v in request.args.items() if k != PATH_PARAM}
    raw = request.get_data(cache=True)
    body: Any = None
    invalid = False
    if raw:
        try:
            body = json.loads(raw)
        except ValueError:
            invalid = True
    return core.ApiRequest(
        method=request.method,
        path=request.args.get(PATH_PARAM, ""),
        headers=dict(request.headers.items()),
        query=query,
        body=body,
        body_invalid=invalid,
    )


def _respond(result: core.ApiResponse) -> Response:
    return Response(
        json.dumps(result.body, separators=(",", ":"), sort_keys=True),
        status=result.status,
        headers=result.headers,
        mimetype="application/json",
    )


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def route(**_ignored: Any) -> Response:
    """Serve one /api/v1 contract request (see the module docstring)."""
    result = core.handle(
        _request(),
        backend.HANDLERS,
        authenticated=frappe.session.user not in (None, "", "Guest"),
        translate=backend.translate,
        on_error=_report,
    )
    if result.status >= 400:
        # Nothing a refused or failed request wrote may be committed by Frappe after
        # this returns. Refusals the ledger must keep were committed where recorded.
        frappe.db.rollback()
    return _respond(result)
