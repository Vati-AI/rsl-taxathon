"""Officer-authorised evidence request (TAX-046).

Written only through ``vati_tax.decisions.store``. The request as authorised
never changes; only ``status`` moves on (sending and responses are TAX-045).
Requests are never deleted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.ids import canonical

MUTABLE = {"status", "modified", "modified_by"}
NEXT_STATUS = {
    "recorded": {"recorded", "sent", "withdrawn"},
    "sent": {"sent", "responded", "withdrawn"},
    "responded": {"responded"},
    "withdrawn": {"withdrawn"},
}


class VatiEvidenceRequest(Document):
    if TYPE_CHECKING:
        request_id: str
        tenant_id: str
        taxpayer_id: str
        case_id: str
        status: str

    def before_insert(self) -> None:
        try:
            self.request_id = canonical.canonical_uuid(self.request_id, "request_id")
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)

    def validate(self) -> None:
        try:
            self.tenant_id = canonical.canonical_uuid(self.tenant_id, "tenant_id")
            self.taxpayer_id = canonical.canonical_uuid(self.taxpayer_id, "taxpayer_id")
            self.case_id = canonical.canonical_uuid(self.case_id, "case_id")
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        if self.is_new():
            if self.status != "recorded":
                frappe.throw("A new evidence request starts as recorded.")
            return
        before = self.get_doc_before_save()
        if before is None:
            return
        for field in self.meta.get_valid_columns():
            if field in MUTABLE:
                continue
            if (before.get(field) or None) != (self.get(field) or None):
                frappe.throw(f"{field} cannot change after the request is authorised.")
        if self.status not in NEXT_STATUS.get(before.status, set()):
            frappe.throw(f"A {before.status} request cannot become {self.status}.")

    def on_trash(self) -> None:
        frappe.throw("Evidence requests are kept; withdraw instead.", frappe.PermissionError)
