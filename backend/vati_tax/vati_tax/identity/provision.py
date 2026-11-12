"""Operator helpers to provision tenants, memberships and service identities.

Run on a developer site with ``bench --site SITE execute``, for example::

    bench --site taxathon.localhost execute vati_tax.identity.provision.ensure_tenant \\
        --kwargs '{"tenant_id": "<uuid>", "tenant_name": "Synthetic Revenue Authority"}'
    bench --site taxathon.localhost execute vati_tax.identity.provision.grant_membership \\
        --kwargs '{"user": "officer@example.test", "tenant_id": "<uuid>", "role": "auditor"}'
    bench --site taxathon.localhost execute \\
        vati_tax.identity.provision.ensure_service_identity \\
        --kwargs '{"user": "vati-api@service.example.test", "tenant_ids": ["<uuid>"]}'

Service identities are ordinary Frappe users with only the ``Vati Service``
role, no password login and explicit per-tenant memberships. Generate their API
key/secret in Desk (User > API Access) and keep it in ignored local config; this
module never creates or prints credentials. Use synthetic users only.
"""

from __future__ import annotations

import frappe

from vati_tax.identity import policy
from vati_tax.identity.permissions import MEMBERSHIP, TENANT, clear_principal_cache


def _require_operator() -> None:
    if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
        raise frappe.PermissionError("Only site operators may provision identities")


def ensure_tenant(tenant_id: str, tenant_name: str) -> str:
    _require_operator()
    if not frappe.db.exists(TENANT, tenant_id):
        frappe.get_doc(
            {"doctype": TENANT, "tenant_id": tenant_id, "tenant_name": tenant_name}
        ).insert()
    return tenant_id


def ensure_role(user: str, role: policy.Role) -> None:
    _require_operator()
    role_name = policy.FRAPPE_ROLES[role]
    if role_name not in frappe.get_roles(user):
        frappe.get_doc("User", user).add_roles(role_name)


def grant_membership(
    user: str,
    tenant_id: str,
    role: policy.Role,
    taxpayer_id: str | None = None,
    refund_team: bool = False,
) -> str:
    """Give ``user`` the Frappe role and an enabled membership (idempotent).

    ``refund_team`` (TAX-142) puts a staff member on the tenant's refund team.
    """
    _require_operator()
    ensure_role(user, role)
    existing = frappe.db.get_value(MEMBERSHIP, {"user": user, "tenant_id": tenant_id}, "name")
    if existing:
        doc = frappe.get_doc(MEMBERSHIP, existing)
        doc.update(
            {"vati_role": role, "taxpayer_id": taxpayer_id, "enabled": 1,
             "refund_team": 1 if refund_team else 0}
        )  # fmt: skip
        doc.save()
    else:
        doc = frappe.get_doc(
            {
                "doctype": MEMBERSHIP,
                "user": user,
                "tenant_id": tenant_id,
                "vati_role": role,
                "taxpayer_id": taxpayer_id,
                "refund_team": 1 if refund_team else 0,
            }
        ).insert()
    clear_principal_cache()
    return str(doc.name)


def ensure_service_identity(user: str, tenant_ids: list[str], full_name: str = "") -> str:
    """Create a login-less service user with ``Vati Service`` memberships."""
    _require_operator()
    if not frappe.db.exists("User", user):
        frappe.get_doc(
            {
                "doctype": "User",
                "email": user,
                "first_name": full_name or "Vati service",
                "send_welcome_email": 0,
            }
        ).insert()
    for tenant_id in tenant_ids:
        grant_membership(user, tenant_id, policy.SERVICE)
    return user
