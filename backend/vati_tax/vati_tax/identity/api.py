"""Whitelisted identity method for the Vati API and officer frontends."""

from __future__ import annotations

from typing import Any

import frappe

from vati_tax.identity import policy
from vati_tax.identity.permissions import resolve_scope


@frappe.whitelist(methods=["GET"])
def get_access_scope() -> dict[str, Any]:
    """The caller's roles and permitted actions in the ``X-Vati-Tenant-ID`` tenant.

    Guests get 403 from Frappe. Missing/malformed header: 400; a tenant outside
    the caller's memberships: 403, without revealing whether it exists.
    """
    scope = resolve_scope()
    return {
        "user": scope.user,
        "tenant_id": scope.tenant_id,
        "roles": sorted(scope.roles),
        "taxpayer_ids": sorted(scope.taxpayer_ids),
        "actions": policy.allowed_actions(scope),
    }
