"""An assistant-drafted evidence response (TAX-100).

Written only by ``vati_tax.assistance.store``. Taxpayers have no DocType
permission: they read and confirm their own drafts through the TAX-096
assistance operations, which check the caller's own taxpayer first. A draft
moves ``draft`` -> ``confirmed`` once, when the taxpayer confirms it and the
TAX-045 response is recorded; it is never deleted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.ids import canonical


class VatiAssistanceDraft(Document):
    if TYPE_CHECKING:
        draft_id: str
        tenant_id: str
        taxpayer_id: str
        request_id: str
        status: str
        version: int

    def validate(self) -> None:
        try:
            for field in ("draft_id", "tenant_id", "taxpayer_id", "request_id"):
                self.set(field, canonical.canonical_uuid(self.get(field), field))
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        if self.is_new():
            return
        before = self.get_doc_before_save()
        if before is not None and before.status == "confirmed":
            frappe.throw("A confirmed draft cannot change.", frappe.PermissionError)

    def on_trash(self) -> None:
        frappe.throw("Assistance drafts are kept.", frappe.PermissionError)
