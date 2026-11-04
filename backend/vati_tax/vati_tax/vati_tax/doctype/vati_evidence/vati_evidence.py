"""Immutable original evidence document (TAX-032).

Created only through ``vati_tax.evidence.store``, which computes the SHA-256 from
the stored bytes. This controller is the database-side backstop: it re-checks
identifiers and limits on insert, refuses every later change and refuses
deletion. A correction is a new version that supersedes this one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.evidence import rules
from vati_tax.ids import canonical

APPEND_ONLY = "Evidence is append-only; register a new version instead."


class VatiEvidence(Document):
    if TYPE_CHECKING:
        evidence_id: str
        tenant_id: str
        taxpayer_id: str
        case_id: str | None
        reference_doctype: str | None
        reference_name: str | None
        version: int
        supersedes_evidence_id: str | None
        lineage_id: str
        document_type: str
        source: str
        received_at: str
        synthetic: int
        document_sha256: str
        media_type: str
        size_bytes: int
        original_filename: str | None
        file_ref: str
        file_url: str
        registration_key: str | None
        request_digest: str | None
        supersede_key: str | None

    def before_insert(self) -> None:
        try:
            self.evidence_id = canonical.canonical_uuid(self.evidence_id, "evidence_id")
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)

    def validate(self) -> None:
        if not self.is_new():
            frappe.throw(APPEND_ONLY, frappe.ValidationError)
        try:
            self.tenant_id = canonical.canonical_uuid(self.tenant_id, "tenant_id")
            self.taxpayer_id = canonical.canonical_uuid(self.taxpayer_id, "taxpayer_id")
            self.lineage_id = canonical.canonical_uuid(self.lineage_id, "lineage_id")
            rules.validate_sha256(self.document_sha256)
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        if self.media_type not in rules.MEDIA_TYPES:
            frappe.throw("Media type is not allowed.")
        if not 0 < int(self.size_bytes or 0) <= rules.MAX_DOCUMENT_BYTES:
            frappe.throw("Evidence size is outside the allowed range.")
        if not str(self.file_url or "").startswith("/private/files/"):
            frappe.throw("Evidence must be stored as a private file.")
        if int(self.version or 0) < 1 or (int(self.version) > 1) != bool(
            self.supersedes_evidence_id
        ):
            frappe.throw("Only a superseding version has a version above 1.")
        self.synthetic = 1
        if self.supersedes_evidence_id:
            self.supersede_key = rules.supersede_key(self.tenant_id, self.supersedes_evidence_id)

    def on_trash(self) -> None:
        frappe.throw(APPEND_ONLY, frappe.PermissionError)
