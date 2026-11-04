"""Evidence export record (TAX-062).

Written only through ``vati_tax.exports.store``. This controller is the
database-side backstop: identifiers are canonical, the request never changes
after insert, the status only moves ``requested -> approved | rejected`` with a
version bump, a manifest exists exactly when the export is approved and matches
its recorded hash, the decider is not the requester, and nothing is deleted.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.exports import rules
from vati_tax.ids import canonical

FROZEN = (
    "export_id",
    "tenant_id",
    "case_id",
    "purpose",
    "recipient",
    "reason",
    "evidence_ids",
    "requested_by",
    "requested_at",
    "request_digest",
)
DECISION = ("decided_by", "decided_at", "decision_reason", "manifest", "manifest_sha256")


class VatiEvidenceExport(Document):
    if TYPE_CHECKING:
        export_id: str
        tenant_id: str
        case_id: str
        status: str
        version: int
        requested_by: str
        decided_by: str | None
        manifest: str | None
        manifest_sha256: str | None

    def before_insert(self) -> None:
        try:
            self.export_id = canonical.canonical_uuid(self.export_id, "export_id")
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        if self.status != "requested" or int(self.version or 0) != 1:
            frappe.throw("A new export starts as requested, version 1.")
        if self.manifest or self.decided_by:
            frappe.throw("A new export has no decision or manifest.")

    def validate(self) -> None:
        try:
            self.tenant_id = canonical.canonical_uuid(self.tenant_id, "tenant_id")
            self.case_id = canonical.canonical_uuid(self.case_id, "case_id")
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        if self.purpose not in rules.PURPOSES:
            frappe.throw("Unknown export purpose.")
        ids = json.loads(self.evidence_ids or "[]")
        if not ids or len(ids) > rules.MAX_EVIDENCE:
            frappe.throw("An export names 1 to 200 evidence items.")
        if self.status == "approved":
            if not self.manifest or not self.manifest_sha256:
                frappe.throw("An approved export carries its manifest.")
            manifest = json.loads(self.manifest or "{}")
            if not rules.verify_manifest(manifest) or (
                manifest.get("manifest_sha256") != self.manifest_sha256
            ):
                frappe.throw("The manifest does not match its hash.")
        elif self.manifest or self.manifest_sha256:
            frappe.throw("Only an approved export carries a manifest.")
        if self.decided_by and self.decided_by.lower() == (self.requested_by or "").lower():
            frappe.throw("The requester cannot decide their own export.")
        if self.is_new():
            return
        before = self.get_doc_before_save()
        if before is None:
            return
        for field in FROZEN:
            if (before.get(field) or None) != (self.get(field) or None):
                frappe.throw(f"{field} cannot change after the export is requested.")
        if before.status != "requested":
            frappe.throw("A decided export cannot change.")
        if self.status not in ("approved", "rejected"):
            frappe.throw("A requested export can only be approved or rejected.")
        if int(self.version or 0) != int(before.version or 0) + 1:
            frappe.throw("A decision moves the version forward by one.")
        for field in DECISION[:2]:
            if not self.get(field):
                frappe.throw(f"{field} is required for a decision.")

    def on_trash(self) -> None:
        frappe.throw("Exports are kept; reject a request instead.", frappe.PermissionError)
