"""Whitelisted learned-workflow methods (TAX-065).

``X-Vati-Tenant-ID`` selects the tenant (TAX-029). Every step, check and refusal
is kept in the tenant audit ledger (TAX-059).

- ``propose_workflow`` (POST): record a TAX-064 ``workflow_proposal`` (JSON) as
  ``workflow``@``version``. Staff or the observer's service identity.
- ``approve_workflow`` / ``reject_workflow`` (POST): a supervisor who did not
  record it; ``expected_version`` from ``get_workflow``; a reason; approval also
  needs ``holdout_reviewed=1``.
- ``activate_workflow`` (POST): a supervisor puts an approved version in force;
  ``expected_seq`` is the current ``activation_seq``.
- ``rollback_workflow`` (POST): a supervisor restores the previous approved version.
- ``get_workflow`` (GET): versions, the active version and the activation history.
- ``check_workflow_execution`` (GET): call before running a learned workflow; only
  the active approved version is executable.
"""

from __future__ import annotations

from typing import Any, NoReturn

import frappe

from vati_tax.identity import permissions
from vati_tax.ids.canonical import MappingError
from vati_tax.workflows import store


class WorkflowBadRequest(frappe.ValidationError):
    http_status_code = 400


class WorkflowConflict(frappe.ValidationError):
    http_status_code = 422


def _raise(exc: MappingError) -> NoReturn:
    if exc.http_status == 404:
        frappe.throw(str(exc), frappe.DoesNotExistError, title=exc.code)
    cls = WorkflowBadRequest if exc.http_status == 400 else WorkflowConflict
    reason = exc.field_errors[0]["code"] if exc.field_errors else exc.code
    frappe.throw(str(exc), cls, title=f"{exc.code}:{reason}")
    raise AssertionError("unreachable")


def _tenant() -> str:
    return permissions.request_tenant_header() or ""


def _header(name: str) -> str | None:
    request = getattr(frappe.local, "request", None)
    return request.headers.get(name) if request is not None else None


def _int(value: Any) -> int | None:
    return frappe.utils.cint(value) if value not in (None, "") else None


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return frappe.parse_json(value)
        except Exception:
            return value
    return value


@frappe.whitelist(methods=["POST"])
def propose_workflow(
    workflow: str, version: str, proposal: Any, idempotency_key: str | None = None
) -> dict[str, Any]:
    try:
        return store.propose_workflow(
            tenant_id=_tenant(),
            body={"workflow": workflow, "version": version, "proposal": _json(proposal)},
            idempotency_key=idempotency_key or _header("Idempotency-Key") or "",
            correlation_id=_header("X-Vati-Correlation-ID"),
        )
    except MappingError as exc:
        _raise(exc)


def _decide(
    workflow: str,
    version: str,
    expected_version: Any,
    approve: bool,
    reason: str | None,
    holdout_reviewed: Any,
) -> dict[str, Any]:
    try:
        return store.decide_workflow(
            tenant_id=_tenant(),
            workflow=workflow,
            version=version,
            expected_version=_int(expected_version),
            approve=approve,
            reason=reason or None,
            holdout_reviewed=bool(frappe.utils.cint(holdout_reviewed)),
            correlation_id=_header("X-Vati-Correlation-ID"),
        )
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["POST"])
def approve_workflow(
    workflow: str,
    version: str,
    expected_version: int | str,
    reason: str,
    holdout_reviewed: int | str = 0,
) -> dict[str, Any]:
    return _decide(workflow, version, expected_version, True, reason, holdout_reviewed)


@frappe.whitelist(methods=["POST"])
def reject_workflow(
    workflow: str, version: str, expected_version: int | str, reason: str
) -> dict[str, Any]:
    return _decide(workflow, version, expected_version, False, reason, 0)


@frappe.whitelist(methods=["POST"])
def activate_workflow(
    workflow: str, version: str, expected_seq: int | str, reason: str
) -> dict[str, Any]:
    try:
        return store.activate_workflow(
            tenant_id=_tenant(),
            workflow=workflow,
            version=version,
            expected_seq=_int(expected_seq),
            reason=reason or None,
            correlation_id=_header("X-Vati-Correlation-ID"),
        )
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["POST"])
def rollback_workflow(workflow: str, expected_seq: int | str, reason: str) -> dict[str, Any]:
    try:
        return store.rollback_workflow(
            tenant_id=_tenant(),
            workflow=workflow,
            expected_seq=_int(expected_seq),
            reason=reason or None,
            correlation_id=_header("X-Vati-Correlation-ID"),
        )
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["GET"])
def get_workflow(workflow: str) -> dict[str, Any]:
    try:
        return store.get_workflow(tenant_id=_tenant(), workflow=workflow)
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["GET"])
def check_workflow_execution(workflow: str, version: str) -> dict[str, Any]:
    try:
        return store.check_execution(tenant_id=_tenant(), workflow=workflow, version=version)
    except MappingError as exc:
        _raise(exc)
