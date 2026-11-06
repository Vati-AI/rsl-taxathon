"""One append-only officer verdict on a rule or agent finding (TAX-086).

Written only by ``vati_tax.findings.store``. The controller refuses edits and
deletes, keeps ``changes_finding`` false and repeats the reason rules of
``FindingFeedbackRequest``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.findings import rules
from vati_tax.ids import canonical


class VatiFindingFeedback(Document):
    if TYPE_CHECKING:
        feedback_id: str
        tenant_id: str
        case_id: str
        finding_id: str
        verdict: str
        reason: str | None
        modified_explanation: str | None
        previous_feedback_id: str | None
        changes_finding: int

    def validate(self) -> None:
        if not self.is_new():
            frappe.throw("Feedback cannot change; record newer feedback.", frappe.PermissionError)
        try:
            for name in ("feedback_id", "tenant_id", "case_id", "finding_id"):
                self.set(name, canonical.canonical_uuid(self.get(name), name))
            self.previous_feedback_id = canonical.optional_uuid(
                self.previous_feedback_id, "previous_feedback_id"
            )
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        if self.changes_finding:
            frappe.throw("Feedback never changes a finding.")
        if self.verdict in ("modified", "rejected") and len(self.reason or "") < rules.MIN_REASON:
            frappe.throw("A modified or rejected verdict needs a reason.")
        if (self.verdict == "modified") != bool(self.modified_explanation):
            frappe.throw("Only a modified verdict carries corrected wording.")

    def on_trash(self) -> None:
        frappe.throw("Feedback is kept.", frappe.PermissionError)
