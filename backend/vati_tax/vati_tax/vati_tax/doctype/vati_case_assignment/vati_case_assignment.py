"""Who works a case (TAX-142, ADR-049).

Written only by ``vati_tax.refund_access.store``. This controller is the database-side
backstop: identifiers are canonical, the assignee holds an enabled refund-team staff
membership in the case's tenant, and an assignment is never deleted (reassign instead).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.identity import policy
from vati_tax.ids import canonical


class VatiCaseAssignment(Document):
    if TYPE_CHECKING:
        case_id: str
        tenant_id: str
        assignee: str
        assignee_role: str
        reason: str
        version: int

    def validate(self) -> None:
        try:
            self.case_id = canonical.canonical_uuid(self.case_id, "case_id")
            self.tenant_id = canonical.canonical_uuid(self.tenant_id, "tenant_id")
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        row = frappe.db.get_value(
            "Vati Tenant Membership",
            {"user": self.assignee, "tenant_id": self.tenant_id, "enabled": 1},
            ["vati_role", "refund_team"],
            as_dict=True,
        )
        membership = (
            policy.Membership(self.tenant_id, row.vati_role, None, bool(row.refund_team))
            if row and row.vati_role in policy.STAFF_ROLES
            else None
        )
        if not policy.may_be_assigned_refund_case(membership):
            frappe.throw("Only a refund-team officer of this tenant can be assigned a case.")
        if len((self.reason or "").strip()) < 10:
            frappe.throw("An assignment needs a reason.")

    def on_update(self) -> None:
        from vati_tax.refund_access.store import clear_cache

        clear_cache()

    def on_trash(self) -> None:
        frappe.throw(
            "Case assignments are kept; reassign the case instead.", frappe.PermissionError
        )
