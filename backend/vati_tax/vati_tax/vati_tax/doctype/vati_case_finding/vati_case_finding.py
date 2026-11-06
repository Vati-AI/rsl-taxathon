"""One case finding from a rule, a playbook run or an officer (TAX-086).

Written only by ``vati_tax.findings.store`` after the engine's checks. This
controller is the database-side backstop: identifiers are canonical, the text
labels nobody (ADR-026), a legal route is never recorded as verified, agent
findings carry provenance and rule/officer findings none, and a recorded
finding never changes and is never deleted (a correction is a new revision).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.findings import rules
from vati_tax.ids import canonical


class VatiCaseFinding(Document):
    if TYPE_CHECKING:
        finding_id: str
        tenant_id: str
        case_id: str
        revision: int
        supersedes_finding_id: str | None
        producer: str
        explanation: str
        alternative_explanations: str
        analysis_provenance: str | None
        legal_route_status: str

    def validate(self) -> None:
        if not self.is_new():
            frappe.throw("Findings cannot change; record a new revision.", frappe.PermissionError)
        try:
            self.finding_id = canonical.canonical_uuid(self.finding_id, "finding_id")
            self.tenant_id = canonical.canonical_uuid(self.tenant_id, "tenant_id")
            self.case_id = canonical.canonical_uuid(self.case_id, "case_id")
            self.supersedes_finding_id = canonical.optional_uuid(
                self.supersedes_finding_id, "supersedes_finding_id"
            )
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        texts = [self.explanation, *json.loads(self.alternative_explanations or "[]")]
        if any(rules.labelling_terms(text) for text in texts):
            frappe.throw("Finding text cannot label a person or entity.")
        if self.legal_route_status not in rules.RECORDABLE_ROUTES:
            frappe.throw("A legal route is recorded unverified.")
        if (self.producer == "langgraph_run") != bool(self.analysis_provenance):
            frappe.throw("Agent findings carry analysis provenance; rule and officer ones none.")
        if int(self.revision or 0) < 1 or (int(self.revision) > 1) != bool(
            self.supersedes_finding_id
        ):
            frappe.throw("A revision after the first supersedes the previous one.")

    def on_trash(self) -> None:
        frappe.throw("Findings are kept.", frappe.PermissionError)
