"""Declared VAT refund claim for a ``refund_claim`` submission (TAX-031).

One per submission. If it references a VAT return, that return must belong to
the same tenant, taxpayer and period, and the claim cannot exceed the refund
position declared there. Supporting documents are referenced by TAX-032
evidence IDs; this DocType stores no files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import frappe

from vati_tax.submissions import domain, guard
from vati_tax.submissions.declaration import DeclarationDocument

REASONS = ("excess_input_vat", "zero_rated_supplies", "other")


class VatiRefundClaim(DeclarationDocument):
    ID_FIELD = "claim_id"
    PARENT_TYPES = frozenset({"refund_claim"})
    DECLARED = frozenset(
        {"claimed_amount", "claim_reason", "explanation", "vat_return_id", "evidence_ids"}
    )

    if TYPE_CHECKING:
        claim_id: str
        submission_id: str
        tenant_id: str
        taxpayer_id: str
        period: str
        currency: str
        claimed_amount: Any
        claim_reason: str
        explanation: str | None
        vat_return_id: str | None
        evidence_ids: str | None

    def validate_figures(self, parent: Any) -> None:
        guard.money(self, "claimed_amount", allow_zero=False)
        if self.claim_reason not in REASONS:
            frappe.throw(
                "claim_reason is not supported.", guard.InvalidDomainInput, title="invalid_choice"
            )
        if self.claim_reason == "other" and not (self.explanation or "").strip():
            frappe.throw(
                "Explain the reason for the claim.",
                guard.InvalidDomainInput,
                title="explanation_required",
            )
        self.set_evidence_ids()
        if self.vat_return_id:
            self._check_vat_return()

    def _check_vat_return(self) -> None:
        # Database read on purpose; equality checks confine it to this taxpayer.
        ret = frappe.db.get_value(
            "Vati VAT Return",
            self.vat_return_id,
            ["tenant_id", "taxpayer_id", "period", "output_vat", "input_vat"],
            as_dict=True,
        )
        if (
            not ret
            or ret.tenant_id != self.tenant_id
            or ret.taxpayer_id != self.taxpayer_id
            or ret.period != self.period
        ):
            frappe.throw(
                "vat_return_id does not reference this taxpayer's VAT return for the period.",
                guard.InvalidDomainInput,
                title="invalid_reference",
            )
        guard.domain_call(
            domain.validate_refund_against_return,
            domain.parse_money(self.claimed_amount, "claimed_amount"),
            domain.parse_money(ret.output_vat, "output_vat"),
            domain.parse_money(ret.input_vat, "input_vat"),
        )
