"""Whitelisted entry points for refund-case assignment (TAX-142, ADR-049).

``X-Vati-Tenant-ID`` selects the tenant (TAX-029). ``assign_case`` is for refund-team
supervisors; ``get_case_assignment`` for staff who may see the case.
"""

from __future__ import annotations

from typing import Any, NoReturn

import frappe

from vati_tax.identity import permissions
from vati_tax.ids.canonical import MappingError
from vati_tax.refund_access import store


def _raise(exc: MappingError) -> NoReturn:
    if exc.http_status == 404:
        frappe.throw(str(exc), frappe.DoesNotExistError, title=exc.code)
    frappe.throw(str(exc), title=exc.code)
    raise AssertionError("unreachable") from exc


@frappe.whitelist(methods=["POST"])
def assign_case(
    case_id: str, assignee: str, reason: str, expected_version: int | str | None = None
) -> dict[str, Any]:
    try:
        return store.assign_case(
            tenant_id=permissions.request_tenant_header() or "",
            case_id=case_id,
            assignee=assignee,
            reason=reason,
            expected_version=int(expected_version) if expected_version not in (None, "") else None,
        )
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["GET"])
def get_case_assignment(case_id: str) -> dict[str, Any] | None:
    try:
        return store.case_assignment(
            tenant_id=permissions.request_tenant_header() or "", case_id=case_id
        )
    except MappingError as exc:
        _raise(exc)
