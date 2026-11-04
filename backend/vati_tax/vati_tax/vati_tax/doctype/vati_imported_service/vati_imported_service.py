"""Declared imported service (services bought from a non-resident supplier) (TAX-031).

Many per ``vat_return`` or ``withholding_return`` submission. ``declared_treatment``
is what the taxpayer says it did; the applicable treatment is not established
here (TAX-024 scenario SYN-004 keeps it ``unverified`` until a human review).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import frappe

from vati_tax.submissions import domain, guard
from vati_tax.submissions.declaration import DeclarationDocument

TREATMENTS = ("vat_accounted", "withholding_declared", "not_declared", "unsure")


class VatiImportedService(DeclarationDocument):
    ID_FIELD = "record_id"
    PARENT_TYPES = domain.IMPORTED_SERVICE_TYPES
    DECLARED = frozenset(
        {
            "supplier_name",
            "supplier_country",
            "counterparty_id",
            "service_description",
            "invoice_reference",
            "invoice_date",
            "amount",
            "vat_accounted",
            "declared_treatment",
            "evidence_ids",
        }
    )

    if TYPE_CHECKING:
        record_id: str
        submission_id: str
        tenant_id: str
        taxpayer_id: str
        period: str
        currency: str
        supplier_name: str
        supplier_country: str
        counterparty_id: str | None
        service_description: str
        invoice_reference: str | None
        invoice_date: Any
        amount: Any
        vat_accounted: Any
        declared_treatment: str
        evidence_ids: str | None

    def validate_figures(self, parent: Any) -> None:
        for field in ("supplier_name", "service_description"):
            value = (self.get(field) or "").strip()
            if not value:
                frappe.throw(f"{field} is required.", guard.InvalidDomainInput, title="required")
            self.set(field, value)
        self.supplier_country = guard.domain_call(domain.validate_country, self.supplier_country)
        self.counterparty_id = guard.domain_call(
            domain.optional_uuid, self.counterparty_id, "counterparty_id"
        )
        guard.money(self, "amount", allow_zero=False)
        guard.money(self, "vat_accounted")
        if self.declared_treatment not in TREATMENTS:
            frappe.throw(
                "declared_treatment is not supported.",
                guard.InvalidDomainInput,
                title="invalid_choice",
            )
        invoice_date = guard.domain_call(domain.parse_date, self.invoice_date, "invoice_date")
        period = domain.parse_period(parent.period, guard.today())
        if invoice_date > period.end or invoice_date > guard.today():
            frappe.throw(
                "invoice_date cannot be after the end of the tax period or in the future.",
                guard.InvalidDomainInput,
                title="invalid_date",
            )
        self.invoice_date = invoice_date
        self.set_evidence_ids()
