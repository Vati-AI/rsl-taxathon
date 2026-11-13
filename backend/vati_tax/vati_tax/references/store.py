"""Frappe binding of the case reference card (TAX-133, ADR-052).

Reads only; nothing here is whitelisted and nothing authorises on its own. Callers
resolve and check the caller first, then pass what they checked:

- the auditor app's ``getCaseReferences`` (``api_v1``) checks ``case.read`` in the
  tenant and the TAX-142 refund-confidentiality gate, then asks for the officer card;
- the taxpayer portal passes the caller's own (tenant, taxpayer) memberships and one of
  their submissions or evidence requests; anything outside them is reported exactly like
  a missing case.

Every query is scoped to the tenant, so a case UUID alone never reaches another
tenant's references.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import frappe

from vati_tax.ids.canonical import CaseNotFound, TenantBoundaryError, canonical_uuid
from vati_tax.references import rules
from vati_tax.responses import store as responses_store

CASE = "Vati Case Identity"
LINK = "Vati Service Link"
TAXPAYER = "Vati Taxpayer"
SUBMISSION = "Vati Submission"
REQUEST = "Vati Evidence Request"


def _card(tenant_id: str, row: Any, audience: str) -> dict[str, Any]:
    links = frappe.get_all(
        LINK,
        filters={"tenant_id": tenant_id, "case_id": row.case_id},
        fields=["service", "external_type", "external_id"],
        order_by="creation asc, name asc",
    )
    tin_masked = None
    if row.taxpayer_id:
        tin_masked = frappe.db.get_value(
            TAXPAYER, {"name": row.taxpayer_id, "tenant_id": tenant_id}, "tin_masked"
        )
    return rules.card(
        tenant_id=tenant_id,
        case_id=row.case_id,
        display_id=row.display_id,
        tin_masked=tin_masked,
        links=links,
        audience=audience,
    )


def _case(case_id: str) -> Any:
    return frappe.db.get_value(
        CASE,
        canonical_uuid(case_id, "case_id"),
        ["case_id", "tenant_id", "display_id", "taxpayer_id"],
        as_dict=True,
    )


def officer_card(*, tenant_id: str, case_id: str) -> dict[str, Any]:
    """The full card of a case in ``tenant_id`` (the caller is an authorised officer)."""
    tenant = canonical_uuid(tenant_id, "tenant_id")
    row = _case(case_id)
    if row is None:
        raise CaseNotFound("Case not found.", "case_id", "not_found")
    if row.tenant_id != tenant:
        raise TenantBoundaryError("Case not found.", "case_id", "not_found")
    return _card(tenant, row, rules.OFFICER)


def _own_case_id(
    scope: list[tuple[str, str]], submission_id: str | None, request_id: str | None
) -> str | None:
    """The case behind the caller's own submission or visible evidence request."""
    if submission_id:
        for tenant_id, taxpayer_id in scope:
            found = frappe.db.get_value(
                SUBMISSION,
                {"name": submission_id, "tenant_id": tenant_id, "taxpayer_id": taxpayer_id},
                "case_id",
            )
            if found:
                return str(found)
        return None
    if request_id:
        # The same visibility as the portal's evidence requests (raises RequestNotFound).
        responses_store.get_request(scope, request_id)
        return frappe.db.get_value(REQUEST, canonical_uuid(request_id, "request_id"), "case_id")
    return None


def taxpayer_card(
    *,
    memberships: Iterable[tuple[str, str]],
    submission_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """The taxpayer's card of the case behind one of their own submissions or requests.

    Taxpayers never see case IDs in the portal, so they reach the card through what
    they do see. Anything not theirs, or not yet a case, is not found.
    """
    scope = [(str(t), str(p)) for t, p in memberships]
    case_id = _own_case_id(scope, submission_id, request_id)
    row = _case(case_id) if case_id else None
    if row is None or (row.tenant_id, row.taxpayer_id or "") not in set(scope):
        raise CaseNotFound("Case not found.", "case_id", "not_found")
    return _card(row.tenant_id, row, rules.TAXPAYER)
