"""Officer decision record (TAX-046).

Written only through ``vati_tax.decisions.store``. This controller is the
database-side backstop: identifiers are canonical, the proposal itself never
changes after insert, the status only moves forward along
``proposed -> approved | rejected | withdrawn``, an agent draft never leaves
``draft``, and nothing is deleted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.decisions import rules
from vati_tax.ids import canonical

FROZEN = (
    "decision_id",
    "tenant_id",
    "taxpayer_id",
    "case_id",
    "origin",
    "outcome",
    "rationale",
    "evidence_ids",
    "finding_ids",
    "rating",
    "draft_id",
    "proposed_by",
    "proposer_role",
    "proposed_at",
    "case_status_at_proposal",
    "case_version",
    "registration_key",
    "request_digest",
)
# Once set, a review is final.
REVIEW = ("reviewed_by", "reviewer_role", "reviewed_at", "review_reason", "review_key")
NEXT_STATUS = {
    "draft": {"draft"},
    "proposed": {"proposed", "approved", "rejected", "withdrawn"},
    "approved": {"approved"},
    "rejected": {"rejected"},
    "withdrawn": {"withdrawn"},
}


class VatiDecision(Document):
    if TYPE_CHECKING:
        decision_id: str
        tenant_id: str
        taxpayer_id: str | None
        case_id: str
        status: str
        origin: str
        outcome: str
        proposer_role: str
        sync_state: str

    def before_insert(self) -> None:
        try:
            self.decision_id = canonical.canonical_uuid(self.decision_id, "decision_id")
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)

    def validate(self) -> None:
        try:
            self.tenant_id = canonical.canonical_uuid(self.tenant_id, "tenant_id")
            self.case_id = canonical.canonical_uuid(self.case_id, "case_id")
            self.taxpayer_id = canonical.optional_uuid(self.taxpayer_id, "taxpayer_id")
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        if self.outcome not in rules.OUTCOMES:
            frappe.throw("Outcome is not allowed.")
        if self.origin == "agent_draft" and self.status != "draft":
            frappe.throw("An agent draft cannot be proposed, approved or executed.")
        if self.status != "draft" and self.proposer_role not in (
            "auditor",
            "specialist",
            "supervisor",
        ):
            frappe.throw("Only an officer can propose a decision.")
        if self.sync_state != "not_started" and self.status != "approved":
            frappe.throw("Only an approved decision reaches Jube.")
        if self.is_new():
            return
        before = self.get_doc_before_save()
        if before is None:
            return
        for field in FROZEN:
            if (before.get(field) or None) != (self.get(field) or None):
                frappe.throw(f"{field} cannot change after the decision is recorded.")
        for field in REVIEW:
            if before.get(field) and before.get(field) != self.get(field):
                frappe.throw(f"{field} cannot change after review.")
        if self.status not in NEXT_STATUS.get(before.status, set()):
            frappe.throw(f"A {before.status} decision cannot become {self.status}.")
        if before.sync_state == "synced" and self.sync_state != "synced":
            frappe.throw("A synced decision stays synced.")

    def on_trash(self) -> None:
        frappe.throw("Decisions are kept; reject or withdraw instead.", frappe.PermissionError)
