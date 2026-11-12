"""Whitelisted entry points for gap-only evidence requests (TAX-127, ADR-050).

``X-Vati-Tenant-ID`` selects the tenant (TAX-029). ``propose_gap_request`` shows an
officer what an itemised request would ask; ``approve_gap_request`` is the officer's
approval that turns it into an evidence request for the taxpayer.
"""

from __future__ import annotations

from typing import Any, NoReturn

import frappe

from vati_tax.evidence_gaps import store
from vati_tax.identity import permissions
from vati_tax.ids.canonical import MappingError


def _raise(exc: MappingError) -> NoReturn:
    if exc.http_status == 404:
        frappe.throw(str(exc), frappe.DoesNotExistError, title=exc.code)
    frappe.throw(str(exc), title=exc.code)
    raise AssertionError("unreachable") from exc


def _header(name: str) -> str | None:
    request = getattr(frappe.local, "request", None)
    return request.headers.get(name) if request is not None else None


@frappe.whitelist(methods=["GET"])
def propose_gap_request(case_id: str) -> dict[str, Any]:
    try:
        return store.propose_gap_request(
            tenant_id=permissions.request_tenant_header() or "", case_id=case_id
        )
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["POST"])
def approve_gap_request(
    case_id: str,
    proposal_digest: str,
    expected_version: int | str,
    due_date: str,
    message_to_taxpayer: str,
    finding_ids: Any = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ids = frappe.parse_json(finding_ids) if isinstance(finding_ids, str) else finding_ids
    try:
        return store.approve_gap_request(
            tenant_id=permissions.request_tenant_header() or "",
            case_id=case_id,
            proposal_digest=proposal_digest,
            expected_version=frappe.utils.cint(expected_version),
            due_date=due_date,
            message_to_taxpayer=message_to_taxpayer,
            finding_ids=list(ids) if ids else None,
            idempotency_key=idempotency_key or _header("Idempotency-Key") or "",
            correlation_id=_header("X-Vati-Correlation-ID"),
        )
    except MappingError as exc:
        _raise(exc)
