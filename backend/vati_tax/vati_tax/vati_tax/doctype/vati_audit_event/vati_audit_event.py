"""Append-only audit event for decisions and evidence requests (TAX-046).

Advancing events (proposal, review, evidence request) carry the case version
they took; ``sequence_key`` is unique on (tenant, case, version), so two
writers cannot both act on the same view of a case. Events are never changed
or deleted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.decisions import rules
from vati_tax.ids import canonical

APPEND_ONLY = "Audit events are append-only."


class VatiAuditEvent(Document):
    if TYPE_CHECKING:
        event_id: str
        tenant_id: str
        taxpayer_id: str | None
        case_id: str
        sequence: int
        sequence_key: str | None
        payload_hash: str

    def before_insert(self) -> None:
        try:
            self.event_id = canonical.canonical_uuid(self.event_id, "event_id")
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)

    def validate(self) -> None:
        if not self.is_new():
            frappe.throw(APPEND_ONLY, frappe.ValidationError)
        try:
            self.tenant_id = canonical.canonical_uuid(self.tenant_id, "tenant_id")
            self.case_id = canonical.canonical_uuid(self.case_id, "case_id")
            self.taxpayer_id = canonical.optional_uuid(self.taxpayer_id, "taxpayer_id")
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        if len(self.payload_hash or "") != 64:
            frappe.throw("payload_hash must be a SHA-256.")
        sequence = int(self.sequence or 0)
        if sequence < 0:
            frappe.throw("sequence cannot be negative.")
        self.sequence_key = (
            rules.sequence_key(self.tenant_id, self.case_id, sequence) if sequence else None
        )

    def on_trash(self) -> None:
        frappe.throw(APPEND_ONLY, frappe.PermissionError)
