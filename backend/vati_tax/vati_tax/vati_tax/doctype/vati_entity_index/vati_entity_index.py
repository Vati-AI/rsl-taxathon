"""Ledger of entities written to a tenant's OpenAleph collection (TAX-087, ADR-036).

``index_key`` (tenant, collection, entity) is unique, so a concurrent write of the
same entity loses at the database. Rows are written only by ``vati_tax.pipeline``.
Only the re-index flag may change later: an entity whose mapping changed is never
re-sent, and an operator re-indexes the collection after review (TAX-036).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

MUTABLE = ("reindex_required", "pending_digest", "modified", "modified_by")


class VatiEntityIndex(Document):
    if TYPE_CHECKING:
        tenant_id: str
        case_id: str
        collection_id: int
        entity_id: str
        entity_schema: str | None
        entity_digest: str
        indexed_at: str | None
        reindex_required: int
        pending_digest: str | None
        event_id: str | None
        correlation_id: str | None
        index_key: str

    def validate(self) -> None:
        if self.is_new():
            return
        before = self.get_doc_before_save()
        if before is None:
            return
        for field in self.meta.get_valid_columns():
            if field not in MUTABLE and before.get(field) != self.get(field):
                frappe.throw(f"{field} cannot change.", frappe.CannotChangeConstantError)

    def on_trash(self) -> None:
        frappe.throw("Entity index records cannot be deleted.", frappe.PermissionError)
