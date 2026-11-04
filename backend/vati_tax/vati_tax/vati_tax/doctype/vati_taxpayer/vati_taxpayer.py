"""Taxpayer registry record (TAX-031).

``taxpayer_id`` is the document name and the UUID that memberships, submissions
and cases use. The record carries ``tenant_id`` and ``taxpayer_id``, so the
TAX-029 hooks let a taxpayer read only its own profile and staff only profiles
in their tenants. The identification number is synthetic; the API exposes only
``tin_masked`` (``TaxpayerProfile`` in integration/api/openapi.json).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.submissions import domain, guard

PROFILE = frozenset({"display_name", "taxpayer_type", "tin", "sector", "registered_on"})
WRITABLE: dict[str, frozenset[str]] = {"supervisor": PROFILE, "service": PROFILE}
SERVER = frozenset({"tin_masked", "tin_key"})


class VatiTaxpayer(Document):
    if TYPE_CHECKING:
        taxpayer_id: str
        tenant_id: str
        display_name: str
        taxpayer_type: str
        tin: str | None
        tin_masked: str | None
        tin_key: str | None
        sector: str | None
        registered_on: str | None
        synthetic: int

    def before_insert(self) -> None:
        self.taxpayer_id = (
            guard.domain_call(domain.canonical_uuid, self.taxpayer_id, "taxpayer_id")
            if self.taxpayer_id
            else domain.new_uuid()
        )

    def validate(self) -> None:
        actor = guard.actor_for(self.tenant_id)
        guard.check_writes(self, actor, WRITABLE, SERVER, (*PROFILE, *SERVER, "synthetic"))
        self.taxpayer_id = guard.domain_call(domain.canonical_uuid, self.taxpayer_id, "taxpayer_id")
        self.display_name = (self.display_name or "").strip()
        if not self.display_name:
            frappe.throw("display_name is required.", guard.InvalidDomainInput, title="required")
        self.taxpayer_type = guard.domain_call(domain.validate_taxpayer_type, self.taxpayer_type)
        self.tin = guard.domain_call(domain.validate_tin, self.tin)
        self.tin_masked = domain.mask_tin(self.tin)
        self.tin_key = domain.scoped_key(self.tenant_id, self.tin) if self.tin else None
        if self.registered_on:
            registered = guard.domain_call(domain.parse_date, self.registered_on, "registered_on")
            if registered > guard.today():
                frappe.throw(
                    "registered_on cannot be in the future.",
                    guard.InvalidDomainInput,
                    title="future_date",
                )
