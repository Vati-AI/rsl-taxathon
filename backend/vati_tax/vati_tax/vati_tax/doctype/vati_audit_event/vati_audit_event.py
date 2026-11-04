"""Append-only audit ledger entry (TAX-046, TAX-059).

Advancing decision events carry the case version they took; ``sequence_key`` is
unique on (tenant, case, version), so two writers cannot both act on the same
view of a case. Every entry also takes the next position in its tenant's hash
chain (``chain_seq``, ``prev_hash``, ``entry_hash``); ``chain_key`` is unique
on (tenant, position). ``vati_tax.audit.store`` seals entries; this controller
re-checks the seal on insert and refuses every edit and delete. A row changed
directly in the database is caught by ``vati_tax.audit.ledger.verify_chain``.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

import frappe
from frappe.model.document import Document

from vati_tax.audit import ledger
from vati_tax.decisions import rules
from vati_tax.ids import canonical

APPEND_ONLY = "Audit events are append-only."


def entry_from_doc(doc: Any) -> ledger.LedgerEntry:
    """A ``LedgerEntry`` from a document or a ``frappe.get_all`` row."""
    occurred = frappe.utils.get_datetime(doc.occurred_at)
    return ledger.LedgerEntry(
        event_id=doc.event_id or doc.name,
        tenant_id=doc.tenant_id,
        chain_seq=int(doc.chain_seq or 0),
        prev_hash=doc.prev_hash or "",
        entry_hash=doc.entry_hash or "",
        category=doc.category,
        action=doc.action,
        outcome=doc.outcome,
        actor=doc.actor,
        actor_role=doc.actor_role,
        occurred_at=occurred.replace(tzinfo=datetime.UTC),
        correlation_id=doc.correlation_id,
        subject_type=doc.subject_type,
        subject_id=doc.subject_id,
        payload_hash=doc.payload_hash,
        case_id=doc.case_id or None,
        taxpayer_id=doc.taxpayer_id or None,
        sequence=int(doc.sequence or 0) or None,
        reason=doc.reason or None,
        target_actor=doc.target_actor or None,
    )


class VatiAuditEvent(Document):
    if TYPE_CHECKING:
        event_id: str
        tenant_id: str
        taxpayer_id: str | None
        case_id: str | None
        sequence: int
        sequence_key: str | None
        payload_hash: str
        category: str
        chain_seq: int
        prev_hash: str
        entry_hash: str
        chain_key: str | None

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
            self.case_id = canonical.optional_uuid(self.case_id, "case_id")
            self.taxpayer_id = canonical.optional_uuid(self.taxpayer_id, "taxpayer_id")
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        if len(self.payload_hash or "") != 64:
            frappe.throw("payload_hash must be a SHA-256.")
        if self.category not in ledger.CATEGORIES:
            frappe.throw("Unknown ledger category.")
        sequence = int(self.sequence or 0)
        if sequence < 0:
            frappe.throw("sequence cannot be negative.")
        if sequence and not self.case_id:
            frappe.throw("A case version needs a case.")
        self.sequence_key = (
            rules.sequence_key(self.tenant_id, self.case_id, sequence)
            if sequence and self.case_id
            else None
        )
        position = int(self.chain_seq or 0)
        if position < 1:
            frappe.throw("chain_seq starts at 1.")
        if ledger.compute_hash(entry_from_doc(self)) != self.entry_hash:
            frappe.throw("The entry hash does not match its fields.")
        self.chain_key = ledger.chain_key(self.tenant_id, position)

    def on_trash(self) -> None:
        frappe.throw(APPEND_ONLY, frappe.PermissionError)
