"""Whitelisted entry points for RSL audit referrals (TAX-123, ADR-047).

``ingest_audit_referrals`` reads the tenant's RSL audit referrals into cases. Only a site
operator may run it: it is an integration job, not a taxpayer or staff action. In a
deployment it would run on a schedule behind WSO2 (ADR-040). From a shell:
``bench --site <site> execute vati_tax.rsl_referrals.api.run --kwargs '{"tenant_id": "..."}'``.

``get_case_referrals`` shows officers the RSL referral behind a case (``X-Vati-Tenant-ID``
selects the tenant, TAX-029).
"""

from __future__ import annotations

from typing import Any

import frappe

from vati_tax.identity import permissions
from vati_tax.ids.canonical import MappingError
from vati_tax.rsl_referrals import store


@frappe.whitelist(methods=["POST"])
def ingest_audit_referrals(tenant_id: str) -> dict[str, Any]:
    if not permissions.is_operator(frappe.session.user):
        frappe.throw("Only a site operator runs the RSL referral intake.", frappe.PermissionError)
    return store.ingest_audit_referrals(tenant_id)


@frappe.whitelist(methods=["GET"])
def get_case_referrals(case_id: str) -> list[dict[str, Any]]:
    try:
        return store.case_referrals(
            tenant_id=permissions.request_tenant_header() or "", case_id=case_id
        )
    except MappingError as exc:
        if exc.http_status == 404:
            frappe.throw(str(exc), frappe.DoesNotExistError, title=exc.code)
        frappe.throw(str(exc), title=exc.code)
        raise AssertionError("unreachable") from exc


def run(tenant_id: str) -> dict[str, Any]:
    """``bench execute`` helper: run as Administrator and commit."""
    frappe.set_user("Administrator")
    summary = ingest_audit_referrals(tenant_id)
    frappe.db.commit()
    return summary
