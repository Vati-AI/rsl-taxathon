"""Replay ledger for posted case state envelopes (TAX-039).

``inbound_key`` (tenant + event ID) is unique, so a concurrent duplicate delivery
loses at the database. Rows are written only by ``vati_tax.bridge.store`` and
never change afterwards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document


class VatiBridgeInboundEvent(Document):
    if TYPE_CHECKING:
        tenant_id: str
        case_id: str
        event_id: str
        outcome: str
        received_at: str | None
        received_by: str | None
        inbound_key: str
        request_digest: str

    def validate(self) -> None:
        if not self.is_new():
            frappe.throw("Inbound event records cannot change.", frappe.PermissionError)

    def on_trash(self) -> None:
        frappe.throw("Inbound event records cannot be deleted.", frappe.PermissionError)
