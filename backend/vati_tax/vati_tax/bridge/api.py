"""Whitelisted bridge methods (TAX-039).

``receive_case_status`` accepts one ``case.status_projected`` EventEnvelope from
a service identity holding ``integration.callback`` in the tenant named by
``X-Vati-Tenant-ID`` (TAX-029). Taxpayers, staff and other tenants are refused
before anything is read. Errors keep the ADR-003 code and reason in the
exception title and never echo the request.

``get_review_status`` is the taxpayer's view: one status word and when it last
changed, for the caller's own submission. Nothing else about the case is
returned, and another taxpayer's submission is not found.
"""

from __future__ import annotations

import json
from typing import Any, NoReturn

import frappe
from frappe import _

from vati_tax.bridge import store
from vati_tax.identity import permissions, policy
from vati_tax.ids.canonical import MappingError
from vati_tax.refund_access import store as refund_access


class BridgeBadRequest(frappe.ValidationError):
    http_status_code = 400


class BridgeConflict(frappe.ValidationError):
    http_status_code = 409


class BridgeUnprocessable(frappe.ValidationError):
    http_status_code = 422


_BY_STATUS: dict[int, type[Exception]] = {
    400: BridgeBadRequest,
    409: BridgeConflict,
    422: BridgeUnprocessable,
}


def _raise(exc: MappingError) -> NoReturn:
    if exc.http_status == 404:
        frappe.throw(_("Case not found."), frappe.DoesNotExistError, title=exc.code)
    reason = exc.field_errors[0]["code"] if exc.field_errors else exc.code
    frappe.throw(
        str(exc), _BY_STATUS.get(exc.http_status, BridgeBadRequest), title=f"{exc.code}:{reason}"
    )
    raise AssertionError("unreachable")


@frappe.whitelist(methods=["POST"])
def receive_case_status(event: Any = None) -> dict[str, Any]:
    """Mirror one observed Jube case state. Replays are no-ops; older states are refused."""
    scope = permissions.require_action(policy.INTEGRATION_CALLBACK)
    if isinstance(event, str):
        try:
            event = json.loads(event)
        except json.JSONDecodeError:
            frappe.throw(_("event must be JSON."), BridgeBadRequest, title="invalid_request")
    try:
        return store.receive_observation(event, tenant_id=scope.tenant_id, actor=scope.user)
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["GET"])
def get_review_status(submission_id: str) -> dict[str, Any]:
    """The caller's own submission: ``review_status`` and ``review_status_at`` only."""
    refund_access.require_submission(submission_id, "submission.read")  # TAX-142
    user = frappe.session.user
    if not user or user == "Guest":
        raise frappe.PermissionError(_("Log in to see your submission."))
    row = frappe.db.get_value(
        store.SUBMISSION, submission_id, ["tenant_id", "taxpayer_id"], as_dict=True
    )
    owned = row is not None and any(
        m.role == policy.TAXPAYER
        and m.tenant_id == row.tenant_id
        and m.taxpayer_id == row.taxpayer_id
        for m in permissions.get_principal(user).memberships
    )
    if not owned:
        raise frappe.DoesNotExistError(_("Submission not found."))
    return {"mode": "live", "data": store.taxpayer_view(submission_id)}
