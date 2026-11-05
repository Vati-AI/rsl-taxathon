"""yente candidates for one case party on one dataset version (TAX-087, ADR-036).

``result_key`` (tenant, case, entity, dataset, version) is unique, so each party is
screened once per dataset version. Candidates are stored as contract
``MatchCandidate`` objects, ``unreviewed``: yente proposes, an officer decides
(TAX-037). Rows are written only by ``vati_tax.pipeline`` and never change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document


class VatiScreeningResult(Document):
    if TYPE_CHECKING:
        tenant_id: str
        case_id: str
        entity_id: str
        entity_schema: str | None
        dataset: str
        dataset_version: str
        candidate_count: int
        above_threshold: int
        ambiguous: int
        candidates_json: str | None
        retrieved_at: str | None
        event_id: str | None
        correlation_id: str | None
        result_key: str

    def validate(self) -> None:
        if not self.is_new():
            frappe.throw("Screening results cannot change.", frappe.PermissionError)

    def on_trash(self) -> None:
        frappe.throw("Screening results cannot be deleted.", frappe.PermissionError)
