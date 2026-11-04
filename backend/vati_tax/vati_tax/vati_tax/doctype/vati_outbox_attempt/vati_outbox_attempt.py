"""One persisted delivery attempt of an outbox event (TAX-033).

Written and committed before the handler runs, so a crash mid-delivery still
leaves a ``started`` row and counts against the retry budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import frappe
from frappe.model.document import Document

IDENTITY = ("tenant_id", "event", "attempt_no", "worker", "lease_token", "started_at")


class VatiOutboxAttempt(Document):
    if TYPE_CHECKING:
        tenant_id: str
        event: str
        attempt_no: int
        outcome: str
        error_code: str | None
        retryable: int
        worker: str | None
        lease_token: str | None
        started_at: Any
        finished_at: Any
        next_attempt_at: Any
        attempt_key: str

    def validate(self) -> None:
        event_tenant = frappe.db.get_value("Vati Outbox Event", self.event, "tenant_id")
        if event_tenant != self.tenant_id:
            frappe.throw("Event not found.", frappe.DoesNotExistError)
        if not isinstance(self.attempt_no, int) or self.attempt_no < 1:
            frappe.throw("attempt_no must be a positive integer.")
        self.attempt_key = f"{self.event}:{self.attempt_no:04d}"
        if not self.is_new():
            before = self.get_doc_before_save()
            if before is not None:
                for field in (*IDENTITY, "attempt_key"):
                    if (before.get(field) or None) != (self.get(field) or None):
                        frappe.throw(f"{field} cannot change.")
