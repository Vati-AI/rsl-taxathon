"""Whitelisted entry point for the RSL claim intake (TAX-122).

``ingest_online_refunds`` reads the tenant's Online Refunds Application claims into cases.
Only a site operator may run it. It is an integration job, not a taxpayer or staff
action; in a deployment it would run on a schedule behind WSO2 (ADR-040). From a shell:
``bench --site <site> execute vati_tax.rsl_intake.api.run --kwargs '{"tenant_id": "..."}'``.
"""

from __future__ import annotations

from typing import Any

import frappe

from vati_tax.identity import permissions
from vati_tax.rsl_intake import store


@frappe.whitelist(methods=["POST"])
def ingest_online_refunds(tenant_id: str) -> dict[str, Any]:
    if not permissions.is_operator(frappe.session.user):
        frappe.throw("Only a site operator runs the RSL intake.", frappe.PermissionError)
    return store.ingest_online_refunds(tenant_id)


def run(tenant_id: str) -> dict[str, Any]:
    """``bench execute`` helper: run as Administrator and commit."""
    frappe.set_user("Administrator")
    summary = ingest_online_refunds(tenant_id)
    frappe.db.commit()
    return summary
