"""Shared controller for the figures attached to a submission (TAX-031).

``Vati VAT Return``, ``Vati Refund Claim`` and ``Vati Imported Service`` are the
taxpayer's declared figures. Each one belongs to one ``Vati Submission`` of the
same tenant and taxpayer, copies its period, and can only be created, changed
or deleted while that submission is ``draft`` or ``correction_needed``. Only the
taxpayer (and site operators) write figures; staff and service identities read
them.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import frappe
from frappe.model.document import Document

from vati_tax.submissions import domain, guard

REFERENCE_FIELDS = ("tenant_id", "taxpayer_id", "submission_id")


class DeclarationDocument(Document):
    ID_FIELD: ClassVar[str]
    # Submission types this record can attach to.
    PARENT_TYPES: ClassVar[frozenset[str]]
    # Fields the taxpayer declares.
    DECLARED: ClassVar[frozenset[str]]
    # Fields only the server sets.
    SERVER: ClassVar[frozenset[str]] = frozenset({"period", "currency"})

    def before_insert(self) -> None:
        # Runs before naming, so the document name is the canonical UUID.
        current = self.get(self.ID_FIELD)
        self.set(
            self.ID_FIELD,
            guard.domain_call(domain.canonical_uuid, current, self.ID_FIELD)
            if current
            else domain.new_uuid(),
        )

    def validate(self) -> None:
        parent = guard.parent_submission(self)
        if parent.submission_type not in self.PARENT_TYPES:
            frappe.throw(
                f"{self.doctype} cannot be attached to a {parent.submission_type} submission.",
                guard.InvalidDomainInput,
                title="invalid_reference",
            )
        guard.require_editable(parent)
        actor = guard.actor_for(self.tenant_id)
        writable = {"taxpayer": self.DECLARED}
        guard.check_writes(self, actor, writable, self.SERVER, (*self.DECLARED, *self.SERVER))
        if self.is_new() and actor not in ("taxpayer", "operator"):
            frappe.throw("Only the taxpayer declares figures.", frappe.PermissionError)
        self.period = parent.period
        self.currency = guard.domain_call(domain.validate_currency, self.get("currency"))
        self.validate_figures(parent)

    def on_trash(self) -> None:
        guard.require_editable(guard.parent_submission(self))

    def validate_figures(self, parent: Any) -> None:
        raise NotImplementedError

    def set_evidence_ids(self) -> None:
        ids = guard.domain_call(domain.evidence_ids, self.get("evidence_ids"))
        self.evidence_ids = json.dumps(ids) if ids else None
