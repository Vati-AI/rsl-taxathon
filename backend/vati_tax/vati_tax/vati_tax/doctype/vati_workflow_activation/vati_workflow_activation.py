"""Learned workflow activation history (TAX-065).

Append-only: entries are never edited or deleted. ``activation_key`` is unique
on (tenant, workflow, seq), so two supervisors acting on the same view cannot
both append the next entry. The latest entry per workflow is the version in
force; ``rollback_to`` is the approved version a rollback of it restores.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.ids import canonical
from vati_tax.workflows import rules

APPEND_ONLY = "Workflow activation history is append-only."


class VatiWorkflowActivation(Document):
    if TYPE_CHECKING:
        tenant_id: str
        workflow: str
        seq: int
        kind: str
        version: str
        rollback_to: str | None

    def validate(self) -> None:
        if not self.is_new():
            frappe.throw(APPEND_ONLY, frappe.ValidationError)
        try:
            self.tenant_id = canonical.canonical_uuid(self.tenant_id, "tenant_id")
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        if int(self.seq or 0) < 1:
            frappe.throw("Activation sequence starts at 1.")
        for field in ("version", "replaced_version", "rollback_to"):
            value = self.get(field)
            if value and not rules.SEMVER.match(value):
                frappe.throw(f"{field} must be a version.")
        status = frappe.db.get_value(
            "Vati Workflow Version",
            {"version_key": rules.version_key(self.tenant_id, self.workflow, self.version)},
            "status",
        )
        if status != "approved":
            frappe.throw("Only an approved version can be put in force.")
        self.activation_key = rules.activation_key(self.tenant_id, self.workflow, int(self.seq))

    def on_trash(self) -> None:
        frappe.throw(APPEND_ONLY, frappe.PermissionError)
