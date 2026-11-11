"""One RSL audit referral (TAX-123, ADR-047).

Written only by ``vati_tax.rsl_referrals.store``. This controller is the database-side
backstop: identifiers are canonical, the record hash is a SHA-256, every risk-set score
is owned by RSL, and a recorded referral never changes and is never deleted.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.ids import canonical
from vati_tax.rsl_referrals import rules

_SHA256 = re.compile(r"[0-9a-f]{64}")


class VatiRSLReferral(Document):
    if TYPE_CHECKING:
        referral_id: str
        tenant_id: str
        case_id: str
        taxpayer_id: str
        audit_case_ref: str
        rsl_inputs: str
        record_sha256: str
        contract_id: str

    def validate(self) -> None:
        if not self.is_new():
            frappe.throw("RSL referrals cannot change.", frappe.PermissionError)
        try:
            self.referral_id = canonical.canonical_uuid(self.referral_id, "referral_id")
            self.tenant_id = canonical.canonical_uuid(self.tenant_id, "tenant_id")
            self.case_id = canonical.canonical_uuid(self.case_id, "case_id")
            self.taxpayer_id = canonical.canonical_uuid(self.taxpayer_id, "taxpayer_id")
            inputs = json.loads(self.rsl_inputs or "{}")
        except (canonical.MappingError, ValueError) as exc:
            frappe.throw(str(exc), title=getattr(exc, "code", "invalid_domain_input"))
        if not _SHA256.fullmatch(self.record_sha256 or ""):
            frappe.throw("record_sha256 must be a SHA-256 digest.")
        if not str(self.contract_id or "").startswith(rules.CONTRACT_PREFIX):
            frappe.throw("contract_id must name the RSL audit-referral contract.")
        if inputs.get("owner") != rules.OWNER or any(
            item.get("owner") != rules.OWNER for item in inputs.get("risk_sets") or []
        ):
            frappe.throw("RSL inputs must be owned by RSL.")

    def on_trash(self) -> None:
        frappe.throw("RSL referrals are kept.", frappe.PermissionError)
