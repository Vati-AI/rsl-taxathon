"""Taxpayer submission envelope (TAX-031).

One return or claim for one taxpayer and period. Frappe owns this status
(``draft`` -> ``submitted`` -> ``correction_needed`` -> ``submitted``, or
``draft`` -> ``withdrawn``); the operational case, risk and investigation state
stay in Jube. Figures live in the detail DocTypes that reference
``submission_id``. Rules: ``vati_tax.submissions.domain``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import frappe
from frappe.model.document import Document
from frappe.model.naming import make_autoname

from vati_tax.submissions import domain, guard

# Who may change what after insert. Operators may change anything that is not
# server-set; tenant, taxpayer, type, period, source and key are set once.
WRITABLE: dict[str, frozenset[str]] = {
    "taxpayer": frozenset({"declaration", "contact_email", "status"}),
    "auditor": frozenset({"status", "correction_reason"}),
    "supervisor": frozenset({"status", "correction_reason"}),
    "service": frozenset({"case_id"}),
}
SERVER = frozenset(
    {
        "reference",
        "period_start",
        "period_end",
        "currency",
        "version",
        "submitted_at",
        "submitted_by",
        "submission_key",
    }
)
# TAX-039: mirrored from Jube by vati_tax.bridge with a direct database write.
# Every document save keeps the stored values, so no client (taxpayer, staff or
# service) can set them, and a save that raced a mirror write cannot undo it.
MIRRORED = ("review_status", "review_status_at")
WATCHED = (
    *SERVER,
    "declaration",
    "contact_email",
    "status",
    "correction_reason",
    "case_id",
    "synthetic",
)
# Taxpayer edits other than the status change need an editable submission.
TAXPAYER_FIELDS = frozenset({"declaration", "contact_email"})


class VatiSubmission(Document):
    if TYPE_CHECKING:
        submission_id: str
        tenant_id: str
        taxpayer_id: str
        reference: str
        submission_type: str
        period: str
        period_start: Any
        period_end: Any
        currency: str
        status: str
        version: int
        declaration: int
        contact_email: str | None
        source: str
        idempotency_key: str | None
        submission_key: str | None
        submitted_at: str | None
        submitted_by: str | None
        correction_reason: str | None
        review_status: str | None
        review_status_at: str | None
        case_id: str | None
        synthetic: int

    def before_insert(self) -> None:
        # Runs before naming, so the document name is the canonical UUID.
        self.submission_id = (
            guard.domain_call(domain.canonical_uuid, self.submission_id, "submission_id")
            if self.submission_id
            else domain.new_uuid()
        )
        actor = guard.actor_for(self.tenant_id)
        if actor not in ("taxpayer", "operator"):
            frappe.throw("Only the taxpayer creates a submission.", frappe.PermissionError)
        # Server-set on insert whatever the client sent.
        self.status = domain.DRAFT
        self.version = 1
        self.submitted_at = None
        self.submitted_by = None
        self.review_status = None
        self.review_status_at = None
        if actor != "operator":
            self.case_id = None
            self.correction_reason = None
        prefix = domain.REFERENCE_PREFIX.get(self.submission_type, "SUB")
        self.reference = make_autoname(f"{prefix}-.YYYY.-.######")

    def validate(self) -> None:
        actor = guard.actor_for(self.tenant_id)
        before = None if self.is_new() else self.get_doc_before_save()
        if before is not None:
            for field in MIRRORED:
                self.set(field, before.get(field))
            guard.check_writes(self, actor, WRITABLE, SERVER, WATCHED)
            changed = guard.changed_fields(self, TAXPAYER_FIELDS)
            if changed and before.status not in domain.EDITABLE_STATUSES:
                frappe.throw(
                    f"The submission is {before.status}; it can no longer change.",
                    frappe.PermissionError,
                )
            guard.domain_call(domain.check_transition, before.status, self.status, actor)
            # Written once: Frappe's set_only_once would also refuse the first link.
            if before.case_id and before.case_id != self.case_id:
                frappe.throw("case_id cannot change once set.", frappe.CannotChangeConstantError)
        self._validate_fields()
        if before is not None:
            self.version = int(before.version or 0) + 1
            if before.status != self.status:
                self._on_transition(before.status)

    def _validate_fields(self) -> None:
        self.submission_id = guard.domain_call(
            domain.canonical_uuid, self.submission_id, "submission_id"
        )
        if self.submission_type not in domain.SUBMISSION_TYPES:
            frappe.throw(
                "submission_type is not supported.",
                guard.InvalidDomainInput,
                title="invalid_choice",
            )
        taxpayer_tenant = frappe.db.get_value(guard.TAXPAYER, self.taxpayer_id, "tenant_id")
        if not taxpayer_tenant or taxpayer_tenant != self.tenant_id:
            frappe.throw(
                "taxpayer_id does not reference a taxpayer in this tenant.",
                guard.InvalidDomainInput,
                title="invalid_reference",
            )
        period = guard.domain_call(domain.parse_period, self.period, guard.today())
        self.period, self.period_start, self.period_end = period.label, period.start, period.end
        self.currency = guard.domain_call(domain.validate_currency, self.currency)
        key = guard.domain_call(domain.validate_idempotency_key, self.idempotency_key)
        self.submission_key = (
            domain.scoped_key(self.tenant_id, self.taxpayer_id, key) if key else None
        )
        self.case_id = guard.domain_call(domain.optional_uuid, self.case_id, "case_id")

    def _on_transition(self, before: str) -> None:
        if self.status == domain.SUBMITTED:
            if not self.declaration:
                frappe.throw(
                    "Confirm the declaration before sending the submission.",
                    guard.InvalidDomainInput,
                    title="declaration_required",
                )
            detail = domain.REQUIRED_DETAIL.get(self.submission_type)
            if detail and not frappe.db.exists(detail, {"submission_id": self.name}):
                frappe.throw(
                    f"Add the {detail.removeprefix('Vati ')} figures before sending.",
                    guard.InvalidDomainInput,
                    title="figures_required",
                )
            self.submitted_at = frappe.utils.now_datetime()
            self.submitted_by = frappe.session.user
            self.correction_reason = None
        elif self.status == domain.CORRECTION_NEEDED and not (self.correction_reason or "").strip():
            frappe.throw(
                "Explain what the taxpayer needs to correct.",
                guard.InvalidDomainInput,
                title="correction_reason_required",
            )

    def on_trash(self) -> None:
        if self.status != domain.DRAFT:
            frappe.throw("Only draft submissions can be deleted.", frappe.PermissionError)
