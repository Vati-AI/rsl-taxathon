"""Check before you file, for RSL's front door through WSO2 (TAX-129, ADR-051).

``check_before_filing`` is called by RSL's Online Refunds front door as a Vati service
identity (``X-Vati-Tenant-ID`` selects the tenant). It returns the same guidance-only
result the taxpayer portal shows (``portal.api.check_before_filing``). Taxpayers and staff
use the portal and the auditor app instead.
"""

from __future__ import annotations

from typing import Any

import frappe

from vati_tax.identity import permissions, policy
from vati_tax.prefiling import rules, store


@frappe.whitelist(methods=["GET"])
def check_before_filing(tin: str, period: str) -> dict[str, Any]:
    scope = permissions.resolve_scope()
    if scope.roles != {policy.SERVICE}:
        raise frappe.PermissionError("Only RSL's front door (a service identity) calls this.")
    try:
        taxpayer_id = store.taxpayer_by_tin(scope.tenant_id, str(tin))
        return {"mode": "live", "data": store.check(
            tenant_id=scope.tenant_id, taxpayer_id=taxpayer_id, period=period)}  # fmt: skip
    except store.TaxpayerNotFound:
        frappe.throw("Taxpayer not found.", frappe.DoesNotExistError, title="not_found")
        raise AssertionError("unreachable") from None
    except rules.InvalidPeriod as exc:
        frappe.throw(str(exc), title="invalid_period")
        raise AssertionError("unreachable") from exc
