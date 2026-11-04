"""Ledger of a named side effect of an outbox event (TAX-033).

``started`` is committed before the outward call, so a replay knows an earlier
call may have happened. ``completed`` stores the upstream reference, so a replay
reuses it instead of calling again. The unique ``effect_key`` (tenant, event,
effect name) allows one row per effect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import frappe
from frappe.model.document import Document

from vati_tax.ids.canonical import MappingError
from vati_tax.outbox import policy

IDENTITY = ("tenant_id", "event", "effect_name", "idempotency_key", "effect_key")


class VatiOutboxEffect(Document):
    if TYPE_CHECKING:
        tenant_id: str
        event: str
        effect_name: str
        state: str
        idempotency_key: str
        attempt_no: int
        result_ref: str | None
        completed_at: Any
        effect_key: str

    def validate(self) -> None:
        try:
            policy.validate_effect_name(self.effect_name)
        except MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        event_tenant = frappe.db.get_value("Vati Outbox Event", self.event, "tenant_id")
        if event_tenant != self.tenant_id:
            frappe.throw("Event not found.", frappe.DoesNotExistError)
        self.effect_key = policy.effect_key(self.tenant_id, self.event, self.effect_name)
        if not self.is_new():
            before = self.get_doc_before_save()
            if before is not None:
                for field in IDENTITY:
                    if (before.get(field) or None) != (self.get(field) or None):
                        frappe.throw(f"{field} cannot change.")
                if before.get("state") == "completed" and self.state != "completed":
                    frappe.throw("A completed effect cannot be reopened.")
