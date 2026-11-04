"""Whitelisted audit-ledger methods (TAX-059).

``X-Vati-Tenant-ID`` selects the tenant (TAX-029). Every read needs the
``ledger.read`` action (supervisors only) and is itself recorded in the ledger.
``record_override`` needs ``override.record`` (supervisors only) and a reason.
Refused attempts are recorded before ``PermissionError`` is raised.

Read contract for the management view (TAX-060):

- ``get_ledger_summary(since, until)``: counts by category and action, distinct
  actors, ``ledger_intact`` and ``head_seq`` for the window;
- ``get_integrity_signals(window_days)``: review-only signals, never about the
  viewing supervisor (``withheld_about_viewer`` counts those);
- ``verify_ledger()``: the full chain check;
- ``get_ledger(case_id, actor, category, since_seq, limit)``: entries, oldest
  first, with ``next_since_seq`` for paging and the current ``chain_head``.
"""

from __future__ import annotations

from typing import Any, NoReturn

import frappe

from vati_tax.audit import store
from vati_tax.ids.canonical import MappingError


class LedgerBadRequest(frappe.ValidationError):
    http_status_code = 400


class LedgerConflict(frappe.ValidationError):
    http_status_code = 422


def _raise(exc: MappingError) -> NoReturn:
    cls = LedgerBadRequest if exc.http_status == 400 else LedgerConflict
    reason = exc.field_errors[0]["code"] if exc.field_errors else exc.code
    frappe.throw(str(exc), cls, title=f"{exc.code}:{reason}")
    raise AssertionError("unreachable")


def _tenant() -> str:
    return store.permissions.request_tenant_header() or ""


def _header(name: str) -> str | None:
    request = getattr(frappe.local, "request", None)
    return request.headers.get(name) if request is not None else None


@frappe.whitelist(methods=["GET"])
def get_ledger(
    case_id: str | None = None,
    actor: str | None = None,
    category: str | None = None,
    since_seq: int | str = 0,
    limit: int | str = 100,
) -> dict[str, Any]:
    try:
        return store.ledger_entries(
            tenant_id=_tenant(),
            case_id=case_id or None,
            actor=actor or None,
            category=category or None,
            since_seq=frappe.utils.cint(since_seq),
            limit=frappe.utils.cint(limit),
        )
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["GET"])
def verify_ledger() -> dict[str, Any]:
    try:
        return store.verify_ledger(tenant_id=_tenant())
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["GET"])
def get_integrity_signals(window_days: int | str = 30) -> dict[str, Any]:
    try:
        return store.review_signals(tenant_id=_tenant(), window_days=frappe.utils.cint(window_days))
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["GET"])
def get_ledger_summary(since: str, until: str) -> dict[str, Any]:
    try:
        lo = frappe.utils.get_datetime(since)
        hi = frappe.utils.get_datetime(until)
    except Exception:
        frappe.throw("since and until must be ISO date-times.", LedgerBadRequest)
    try:
        return store.ledger_summary(tenant_id=_tenant(), since=lo, until=hi)
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["POST"])
def record_override(
    control: str,
    subject_type: str,
    subject_id: str,
    reason: str,
    case_id: str | None = None,
    target_actor: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    body = {
        "control": control,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "reason": reason,
        "case_id": case_id or None,
        "target_actor": target_actor or None,
    }
    try:
        return store.record_override(
            tenant_id=_tenant(),
            body=body,
            idempotency_key=idempotency_key or _header("Idempotency-Key") or "",
            correlation_id=_header("X-Vati-Correlation-ID"),
        )
    except MappingError as exc:
        _raise(exc)
