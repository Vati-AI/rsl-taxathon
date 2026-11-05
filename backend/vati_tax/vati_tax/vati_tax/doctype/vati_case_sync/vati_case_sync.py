"""Staff-only mirror of one Jube case (TAX-039).

Jube owns the case (ADR-001). This record only remembers what the bridge last
read from Jube and when, so older observations can be refused and the taxpayer's
``review_status`` can be derived. It has no ``taxpayer_id`` and no taxpayer
DocPerm, so a taxpayer never reaches it. Written only by ``vati_tax.bridge.store``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

IDENTITY = ("tenant_id", "case_id", "submission_id")


class VatiCaseSync(Document):
    if TYPE_CHECKING:
        tenant_id: str
        case_id: str
        submission_id: str | None
        jube_case_id: str | None
        case_status: str | None
        escalated: int
        closed: int
        review_status: str | None
        observed_at: str | None
        last_event_id: str | None
        last_polled_at: str | None
        last_error_code: str | None

    def validate(self) -> None:
        if self.is_new():
            return
        before = self.get_doc_before_save()
        if before is None:
            return
        for field in IDENTITY:
            if (before.get(field) or None) != (self.get(field) or None):
                frappe.throw(f"{field} cannot change.", frappe.CannotChangeConstantError)
        if before.jube_case_id and before.jube_case_id != self.jube_case_id:
            frappe.throw("jube_case_id cannot change once set.", frappe.CannotChangeConstantError)
