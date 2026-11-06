"""One run of the assurance checks for one case (TAX-121, ADR-042).

Written only by ``vati_tax.assurance.store``, once per outbox event (unique run key). It
records what ran and which services answered; a run that could not read what it needs is
``not_run`` with a reason code. Runs never change and are never deleted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.ids import canonical


class VatiAssuranceRun(Document):
    if TYPE_CHECKING:
        run_id: str
        tenant_id: str
        case_id: str
        status: str
        reason: str | None

    def validate(self) -> None:
        if not self.is_new():
            frappe.throw("Assurance runs cannot change.", frappe.PermissionError)
        try:
            self.run_id = canonical.canonical_uuid(self.run_id, "run_id")
            self.tenant_id = canonical.canonical_uuid(self.tenant_id, "tenant_id")
            self.case_id = canonical.canonical_uuid(self.case_id, "case_id")
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        if self.status in ("not_run", "superseded") and not self.reason:
            frappe.throw("A run that did not run says why.")

    def on_trash(self) -> None:
        frappe.throw("Assurance runs are kept.", frappe.PermissionError)
