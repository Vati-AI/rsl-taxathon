"""Learned workflow version (TAX-065).

Written only through ``vati_tax.workflows.store``. Database-side backstop: the
recorded proposal never changes, the status only moves ``proposed -> approved |
rejected`` with a record-version bump, the decider is never the person who
recorded it, steps stay within the TAX-064/TAX-046 allowlist, and nothing is
deleted. ``version_key`` is unique on (tenant, workflow, version).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.ids import canonical
from vati_tax.workflows import rules

FROZEN = (
    "tenant_id",
    "workflow",
    "version",
    "steps",
    "proposal_id",
    "observer_version",
    "proposal_digest",
    "holdout_evaluation",
    "request_digest",
    "proposed_by",
    "proposed_at",
)


class VatiWorkflowVersion(Document):
    if TYPE_CHECKING:
        tenant_id: str
        workflow: str
        version: str
        status: str
        record_version: int
        steps: str
        proposed_by: str
        decided_by: str | None

    def validate(self) -> None:
        try:
            self.tenant_id = canonical.canonical_uuid(self.tenant_id, "tenant_id")
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        if not rules.WORKFLOW_NAME.match(self.workflow or ""):
            frappe.throw("Invalid workflow name.")
        if not rules.SEMVER.match(self.version or ""):
            frappe.throw("Invalid workflow version.")
        steps = json.loads(self.steps or "[]")
        if not rules.MIN_STEPS <= len(steps) <= rules.MAX_STEPS or any(
            step not in rules.STEP_NAMES for step in steps
        ):
            frappe.throw("Workflow steps are outside the allowed set.")
        if self.decided_by and self.decided_by.lower() == (self.proposed_by or "").lower():
            frappe.throw("The person who recorded a version cannot decide it.")
        self.version_key = rules.version_key(self.tenant_id, self.workflow, self.version)
        if self.is_new():
            if self.status != "proposed" or int(self.record_version or 0) != 1 or self.decided_by:
                frappe.throw("A new version starts as proposed, record version 1.")
            return
        before = self.get_doc_before_save()
        if before is None:
            return
        for field in FROZEN:
            if (before.get(field) or None) != (self.get(field) or None):
                frappe.throw(f"{field} cannot change after the version is recorded.")
        if before.status != "proposed":
            frappe.throw("A decided version cannot change.")
        if self.status not in ("approved", "rejected"):
            frappe.throw("A proposed version can only be approved or rejected.")
        if int(self.record_version or 0) != int(before.record_version or 0) + 1:
            frappe.throw("A decision moves the record version forward by one.")
        if not (self.decided_by and self.decided_at and self.decision_reason):
            frappe.throw("A decision needs who, when and why.")

    def on_trash(self) -> None:
        frappe.throw("Workflow versions are kept; reject instead.", frappe.PermissionError)
