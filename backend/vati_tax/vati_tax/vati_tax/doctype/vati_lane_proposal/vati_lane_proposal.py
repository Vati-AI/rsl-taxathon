"""Proposed refund lane for one Online Refunds Application claim (TAX-121, ADR-042).

Written only by ``vati_tax.assurance.store`` after the engine's checks. This controller
is the database-side backstop: identifiers are canonical, the lane is one of the TAX-114
lanes, amounts are decimal strings, the proposal records no decision, and a recorded
proposal never changes and is never deleted (changed inputs add a revision).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.assurance import rules
from vati_tax.ids import canonical


class VatiLaneProposal(Document):
    if TYPE_CHECKING:
        proposal_id: str
        tenant_id: str
        case_id: str
        claim_ref: str
        revision: int
        supersedes_proposal_id: str | None
        lane: str
        body_json: str

    def validate(self) -> None:
        if not self.is_new():
            frappe.throw("Lane proposals cannot change; record a new revision.",
                         frappe.PermissionError)  # fmt: skip
        try:
            self.proposal_id = canonical.canonical_uuid(self.proposal_id, "proposal_id")
            self.tenant_id = canonical.canonical_uuid(self.tenant_id, "tenant_id")
            self.case_id = canonical.canonical_uuid(self.case_id, "case_id")
            self.supersedes_proposal_id = canonical.optional_uuid(
                self.supersedes_proposal_id, "supersedes_proposal_id"
            )
            body = rules.validate_lane(json.loads(self.body_json or "{}"))
        except (canonical.MappingError, ValueError) as exc:
            frappe.throw(str(exc), title=getattr(exc, "code", "invalid_domain_input"))
        if body["lane"] != self.lane or body["claim_ref"] != self.claim_ref:
            frappe.throw("The stored lane differs from the proposal body.")
        if int(self.revision or 0) < 1 or (int(self.revision) > 1) != bool(
            self.supersedes_proposal_id
        ):
            frappe.throw("A revision after the first supersedes the previous one.")

    def on_trash(self) -> None:
        frappe.throw("Lane proposals are kept.", frappe.PermissionError)
