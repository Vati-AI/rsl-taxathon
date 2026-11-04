"""Canonical case identity (TAX-030).

Holds only identifiers. Jube owns the operational case and its status; this
DocType never carries a lifecycle. Records are created through
``vati_tax.ids.mapping.register_case``; desk users can only read them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.ids import canonical

IMMUTABLE = ("case_id", "tenant_id", "display_id", "taxpayer_id", "submission_id", "synthetic")


class VatiCaseIdentity(Document):
    if TYPE_CHECKING:
        case_id: str
        tenant_id: str
        display_id: str
        taxpayer_id: str | None
        submission_id: str | None
        synthetic: int
        display_key: str
        registration_key: str | None

    def before_insert(self) -> None:
        # Runs before naming, so the document name is the canonical UUID.
        try:
            self.case_id = canonical.canonical_uuid(self.case_id, "case_id")
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)

    def validate(self) -> None:
        try:
            self.case_id = canonical.canonical_uuid(self.case_id, "case_id")
            self.tenant_id = canonical.canonical_uuid(self.tenant_id, "tenant_id")
            self.display_id = canonical.validate_display_id(self.display_id)
            self.taxpayer_id = canonical.optional_uuid(self.taxpayer_id, "taxpayer_id")
            self.submission_id = canonical.optional_uuid(self.submission_id, "submission_id")
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        self.display_key = canonical.display_key(self.tenant_id, self.display_id)
        if not self.is_new():
            before = self.get_doc_before_save()
            if before is not None:
                for field in (*IMMUTABLE, "registration_key"):
                    if (before.get(field) or None) != (self.get(field) or None):
                        frappe.throw(f"{field} cannot change after registration.")
