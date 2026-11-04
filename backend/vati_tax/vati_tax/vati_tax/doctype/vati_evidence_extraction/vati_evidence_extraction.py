"""Append-only extraction revision of one evidence document (TAX-032).

Created only through ``vati_tax.evidence.store.record_extraction``. A revision
records the SHA-256 of the evidence it read; it can never change or be deleted.
A newer result is a new revision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

APPEND_ONLY = "Extraction revisions are append-only; record a new revision instead."


class VatiEvidenceExtraction(Document):
    if TYPE_CHECKING:
        extraction_id: str
        tenant_id: str
        taxpayer_id: str
        evidence_id: str
        document_sha256: str
        revision: int
        status: str
        error_code: str | None

    def validate(self) -> None:
        if not self.is_new():
            frappe.throw(APPEND_ONLY, frappe.ValidationError)
        evidence = frappe.db.get_value(
            "Vati Evidence",
            self.evidence_id,
            ["tenant_id", "taxpayer_id", "document_sha256"],
            as_dict=True,
        )
        if not evidence or evidence.tenant_id != self.tenant_id:
            frappe.throw("Evidence not found.", frappe.DoesNotExistError)
        if evidence.taxpayer_id != self.taxpayer_id:
            frappe.throw("Taxpayer does not match the evidence.")
        if evidence.document_sha256 != self.document_sha256:
            frappe.throw("The extraction must cite the evidence's original SHA-256.")
        if (self.status == "success") == bool(self.error_code):
            frappe.throw("Only a partial or failed extraction carries an error code.")
        if self.status == "failure" and self.items:
            frappe.throw("A failed extraction carries no items.")

    def on_trash(self) -> None:
        frappe.throw(APPEND_ONLY, frappe.PermissionError)
