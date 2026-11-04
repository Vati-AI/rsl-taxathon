"""Create the Vati roles on install and keep them in sync on every migrate."""

from __future__ import annotations

import frappe

from vati_tax.identity.policy import DESK_ACCESS, FRAPPE_ROLES


def sync_roles() -> list[str]:
    """Idempotently ensure each Vati Frappe role exists with its desk access."""
    changed: list[str] = []
    for role, role_name in FRAPPE_ROLES.items():
        desk_access = 1 if DESK_ACCESS[role] else 0
        if not frappe.db.exists("Role", role_name):
            frappe.get_doc(
                {"doctype": "Role", "role_name": role_name, "desk_access": desk_access}
            ).insert(ignore_permissions=True)
            changed.append(role_name)
        elif frappe.db.get_value("Role", role_name, "desk_access") != desk_access:
            frappe.db.set_value("Role", role_name, "desk_access", desk_access)
            changed.append(role_name)
    return changed


def before_install() -> None:
    # Before DocType sync, so DocType permissions reference existing roles.
    sync_roles()


def before_migrate() -> None:
    sync_roles()
