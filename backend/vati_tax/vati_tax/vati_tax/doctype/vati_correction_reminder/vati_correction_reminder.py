"""Correction reminder for one taxpayer (TAX-078).

Written only through ``vati_tax.reminders.store``. This controller is the
database-side backstop: identifiers are canonical, the source of a reminder
never changes, a change moves the version forward by one and records who
changed it and why, a withdrawn reminder never changes again, one reminder per
decision and topic (unique key), and nothing is deleted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.ids import canonical
from vati_tax.reminders import rules

FROZEN = (
    "reminder_id",
    "tenant_id",
    "taxpayer_id",
    "case_id",
    "decision_id",
    "recorded_by",
    "recorded_at",
    "request_digest",
)


class VatiCorrectionReminder(Document):
    if TYPE_CHECKING:
        reminder_id: str
        tenant_id: str
        taxpayer_id: str
        case_id: str
        decision_id: str
        topic: str
        status: str
        version: int
        explanation: str
        updated_by: str | None
        change_reason: str | None
        reminder_key: str | None

    def before_insert(self) -> None:
        if self.status != "active" or int(self.version or 0) != 1 or self.updated_by:
            frappe.throw("A new reminder starts active, version 1, with no changes.")

    def validate(self) -> None:
        try:
            for field in ("reminder_id", "tenant_id", "taxpayer_id", "case_id", "decision_id"):
                self.set(field, canonical.canonical_uuid(self.get(field), field))
            rules.check_topic(self.topic)
            rules.text(
                self.explanation, "explanation", rules.EXPLANATION_MIN, rules.EXPLANATION_MAX
            )
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        if self.status not in rules.STATUSES:
            frappe.throw("Unknown reminder status.")
        self.reminder_key = rules.reminder_key(self.tenant_id, self.decision_id, self.topic)
        if self.is_new():
            return
        before = self.get_doc_before_save()
        if before is None:
            return
        for field in FROZEN:
            if (before.get(field) or None) != (self.get(field) or None):
                frappe.throw(f"{field} cannot change after the reminder is recorded.")
        if before.status != "active":
            frappe.throw("A withdrawn reminder cannot change.")
        if int(self.version or 0) != int(before.version or 0) + 1:
            frappe.throw("A change moves the version forward by one.")
        if not self.updated_by or not (self.change_reason or "").strip():
            frappe.throw("A change records who made it and why.")

    def on_trash(self) -> None:
        frappe.throw("Reminders are kept for traceability.", frappe.PermissionError)
