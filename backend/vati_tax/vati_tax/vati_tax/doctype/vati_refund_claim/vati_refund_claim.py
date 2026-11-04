"""Declared VAT refund claim for a ``refund_claim`` submission (TAX-031).

One per submission. If it references a VAT return, that return must belong to
the same tenant, taxpayer and period, and the claim cannot exceed the refund
position declared there. Supporting documents are referenced by TAX-032
evidence IDs; this DocType stores no files. Each referenced evidence must
belong to the claim's own tenant and taxpayer (TAX-043).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import frappe

from vati_tax.submissions import domain, guard
from vati_tax.submissions.declaration import DeclarationDocument

REASONS = ("excess_input_vat", "zero_rated_supplies", "other")
EVIDENCE = "Vati Evidence"


def foreign_evidence_ids(tenant_id: str, taxpayer_id: str, evidence_ids: Iterable[str]) -> list[str]:
    """The IDs that are not evidence of this tenant and taxpayer.

    A missing ID and another taxpayer's (or tenant's) evidence are reported the
    same way, so a claim cannot be used to probe for other records. Database
    read on purpose: the explicit tenant and taxpayer filters confine it.
    """
    ids = list(dict.fromkeys(evidence_ids))
    if not ids:
        return []
    known = set(
        frappe.get_all(
            EVIDENCE,
            filters={"name": ["in", ids], "tenant_id": tenant_id, "taxpayer_id": taxpayer_id},
            pluck="name",
        )
    )
    return [evidence_id for evidence_id in ids if evidence_id not in known]


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
        ids = json.loads(self.evidence_ids) if self.evidence_ids else []
        if foreign_evidence_ids(self.tenant_id, self.taxpayer_id, ids):
            frappe.throw(
                "evidence_ids must reference this taxpayer's own evidence.",
                guard.InvalidDomainInput,
                title="invalid_reference",
            )
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
