"""Whitelisted evidence-export methods (TAX-062).

``X-Vati-Tenant-ID`` selects the tenant (TAX-029). Mutations take an
``Idempotency-Key`` (argument or header); ``X-Vati-Correlation-ID`` is optional.
Every step, read and refusal is kept in the tenant audit ledger (TAX-059).

- ``request_export`` (POST): auditor, specialist or supervisor; names the case,
  the evidence, a purpose, a recipient and a reason.
- ``approve_export`` / ``reject_export`` (POST): a supervisor who did not make
  the request; ``expected_version`` from ``get_export``. Rejection needs a reason.
- ``get_export`` (GET): status for staff of the tenant, without the manifest.
- ``get_export_manifest`` (GET): the frozen hash/provenance manifest, for the
  requester or approver, up to 24 hours after approval.
- ``get_retention_policy`` (GET): the retention schedule in force (staff).
"""

from __future__ import annotations

from typing import Any, NoReturn

import frappe

from vati_tax.exports import store
from vati_tax.identity import permissions
from vati_tax.ids.canonical import MappingError
from vati_tax.refund_access import store as refund_access


class ExportBadRequest(frappe.ValidationError):
    http_status_code = 400


class ExportConflict(frappe.ValidationError):
    http_status_code = 422


def _raise(exc: MappingError) -> NoReturn:
    if exc.http_status == 404:
        frappe.throw(str(exc), frappe.DoesNotExistError, title=exc.code)
    cls = ExportBadRequest if exc.http_status == 400 else ExportConflict
    reason = exc.field_errors[0]["code"] if exc.field_errors else exc.code
    frappe.throw(str(exc), cls, title=f"{exc.code}:{reason}")
    raise AssertionError("unreachable")


def _tenant() -> str:
    return permissions.request_tenant_header() or ""


def _header(name: str) -> str | None:
    request = getattr(frappe.local, "request", None)
    return request.headers.get(name) if request is not None else None


def _evidence_ids(value: Any) -> Any:
    """Accept a JSON list (form-encoded calls) or a list (JSON body)."""
    if isinstance(value, str):
        try:
            return frappe.parse_json(value)
        except Exception:
            return value
    return value


@frappe.whitelist(methods=["POST"])
def request_export(
    case_id: str,
    evidence_ids: Any,
    purpose: str,
    recipient: str,
    reason: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    refund_access.require_case(case_id, "export.request")  # TAX-142
    body = {
        "case_id": case_id,
        "evidence_ids": _evidence_ids(evidence_ids),
        "purpose": purpose,
        "recipient": recipient,
        "reason": reason,
    }
    try:
        return store.request_export(
            tenant_id=_tenant(),
            body=body,
            idempotency_key=idempotency_key or _header("Idempotency-Key") or "",
            correlation_id=_header("X-Vati-Correlation-ID"),
        )
    except MappingError as exc:
        _raise(exc)


def _decide(export_id: str, expected_version: Any, approve: bool, reason: str | None) -> Any:
    refund_access.require_record("Vati Evidence Export", export_id, "export.approve")  # TAX-142
    try:
        return store.decide_export(
            tenant_id=_tenant(),
            export_id=export_id,
            expected_version=frappe.utils.cint(expected_version)
            if expected_version not in (None, "")
            else None,
            approve=approve,
            reason=reason or None,
            correlation_id=_header("X-Vati-Correlation-ID"),
        )
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["POST"])
def approve_export(
    export_id: str, expected_version: int | str, reason: str | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = _decide(export_id, expected_version, True, reason)
    return result


@frappe.whitelist(methods=["POST"])
def reject_export(export_id: str, expected_version: int | str, reason: str) -> dict[str, Any]:
    result: dict[str, Any] = _decide(export_id, expected_version, False, reason)
    return result


@frappe.whitelist(methods=["GET"])
def get_export(export_id: str) -> dict[str, Any]:
    refund_access.require_record("Vati Evidence Export", export_id, "export.read")  # TAX-142
    try:
        return store.get_export(tenant_id=_tenant(), export_id=export_id)
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["GET"])
def get_export_manifest(export_id: str) -> dict[str, Any]:
    refund_access.require_record("Vati Evidence Export", export_id, "export.read")  # TAX-142
    try:
        return store.get_manifest(tenant_id=_tenant(), export_id=export_id)
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["GET"])
def get_retention_policy() -> dict[str, Any]:
    try:
        return store.retention_policy(tenant_id=_tenant())
    except MappingError as exc:
        _raise(exc)
