"""Objection or appeal against an approved decision (TAX-069).

Written only through ``vati_tax.challenges.store``. This controller is the
database-side backstop: identifiers are canonical, what was lodged never
changes, the status only moves ``lodged -> reviewed`` with a version bump and a
known outcome, the reviewer is not the lodger, one objection per decision and
one appeal per objection (unique keys), and nothing is deleted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.challenges import rules
from vati_tax.ids import canonical

FROZEN = (
    "challenge_id",
    "tenant_id",
    "taxpayer_id",
    "case_id",
    "decision_id",
    "stage",
    "parent_challenge_id",
    "grounds",
    "evidence_ids",
    "route_reference",
    "lodged_by",
    "lodger_role",
    "lodged_at",
    "decision_digest",
    "request_digest",
)


class VatiDecisionChallenge(Document):
    if TYPE_CHECKING:
        challenge_id: str
        tenant_id: str
        taxpayer_id: str
        case_id: str
        decision_id: str
        stage: str
        parent_challenge_id: str | None
        status: str
        version: int
        outcome: str | None
        lodged_by: str
        reviewed_by: str | None
        objection_key: str | None
        appeal_key: str | None

    def before_insert(self) -> None:
        if self.status != "lodged" or int(self.version or 0) != 1 or self.outcome:
            frappe.throw("A new challenge starts as lodged, version 1, with no outcome.")

    def validate(self) -> None:
        try:
            self.challenge_id = canonical.canonical_uuid(self.challenge_id, "challenge_id")
            self.tenant_id = canonical.canonical_uuid(self.tenant_id, "tenant_id")
            self.taxpayer_id = canonical.canonical_uuid(self.taxpayer_id, "taxpayer_id")
            self.case_id = canonical.canonical_uuid(self.case_id, "case_id")
            self.decision_id = canonical.canonical_uuid(self.decision_id, "decision_id")
            self.parent_challenge_id = canonical.optional_uuid(
                self.parent_challenge_id, "parent_challenge_id"
            )
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        if (self.stage == "appeal") != bool(self.parent_challenge_id):
            frappe.throw("An appeal names its objection; an objection names none.")
        self.objection_key = (
            rules.digest(["objection", self.tenant_id, self.decision_id])
            if self.stage == "objection"
            else None
        )
        self.appeal_key = (
            rules.digest(["appeal", self.tenant_id, self.parent_challenge_id])
            if self.stage == "appeal"
            else None
        )
        if self.status == "reviewed":
            if self.outcome not in rules.OUTCOMES or not self.reviewed_by:
                frappe.throw("A reviewed challenge has a known outcome and a reviewer.")
            if (self.reviewed_by or "").lower() == (self.lodged_by or "").lower():
                frappe.throw("The person who lodged a challenge cannot review it.")
        elif self.outcome or self.reviewed_by:
            frappe.throw("Only a reviewed challenge has an outcome.")
        if self.is_new():
            return
        before = self.get_doc_before_save()
        if before is None:
            return
        for field in FROZEN:
            if (before.get(field) or None) != (self.get(field) or None):
                frappe.throw(f"{field} cannot change after the challenge is lodged.")
        if before.status != "lodged":
            frappe.throw("A reviewed challenge cannot change.")
        if int(self.version or 0) != int(before.version or 0) + 1:
            frappe.throw("A review moves the version forward by one.")

    def on_trash(self) -> None:
        frappe.throw("Challenges are kept for traceability.", frappe.PermissionError)
