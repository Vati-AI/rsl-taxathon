"""Whitelisted integrity review for supervisors (TAX-077, ADR-036).

``X-Vati-Tenant-ID`` selects the tenant (TAX-029). Only supervisors may read the
review; refusals and reads are recorded in the TAX-059 ledger and committed,
because reads arrive on GET and Frappe does not commit GET requests.

Reviewed policy-gap findings have no store in Frappe until the case findings
record (TAX-086) lands, so ``policy_exposure`` is reported as ``unavailable``
with a ``missing_data`` note rather than as zero.
"""

from __future__ import annotations

from typing import Any

import frappe

from vati_tax.audit import store as audit_store
from vati_tax.identity import permissions, policy
from vati_tax.ids.canonical import MappingError
from vati_tax.integrity import service


class IntegrityBadRequest(frappe.ValidationError):
    http_status_code = 400


@frappe.whitelist(methods=["GET"])
def get_integrity_review(window_days: int | str = 30) -> dict[str, Any]:
    tenant = permissions.request_tenant_header() or ""
    ctx = audit_store.context(tenant)
    try:
        result = service.integrity_review(
            ctx,
            audit_store.FrappeLedger(),
            gaps=None,
            gap_labels={},
            window_days=frappe.utils.cint(window_days),
        )
    except policy.ActionDenied as exc:
        frappe.db.commit()  # nosemgrep: keep the recorded refusal
        raise frappe.PermissionError(str(exc)) from exc
    except MappingError as exc:
        frappe.throw(str(exc), IntegrityBadRequest, title=exc.code)
        raise AssertionError("unreachable") from exc
    frappe.db.commit()  # nosemgrep: keep the recorded read (GET)
    return result
