"""Declared VAT refund claim for a ``refund_claim`` submission (TAX-031).

One per submission. If it references a VAT return, that return must belong to
the same tenant, taxpayer and period, and the claim cannot exceed the refund
position declared there. Supporting documents are referenced by TAX-032
evidence IDs; this DocType stores no files. Each referenced evidence must
belong to the claim's own tenant and taxpayer (TAX-043).

TAX-044 adds the period's declared output and input VAT and a purchase-invoice
schedule (``Vati Refund Claim Invoice`` rows). Rows are validated with
``vati_tax.submissions.invoices`` (exact arithmetic and dates only), their
documents must be the taxpayer's own, and the schedule totals are recomputed by
the server. Both are optional here so existing claims stay valid; the taxpayer
portal requires them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import frappe

from vati_tax.submissions import domain, guard
from vati_tax.submissions import invoices as schedule
from vati_tax.submissions.declaration import DeclarationDocument

REASONS = ("excess_input_vat", "zero_rated_supplies", "other")
EVIDENCE = "Vati Evidence"


def foreign_evidence_ids(
    tenant_id: str, taxpayer_id: str, evidence_ids: Iterable[str]
) -> list[str]:
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
        {
            "claimed_amount",
            "claim_reason",
            "explanation",
            "vat_return_id",
            "evidence_ids",
            "period_output_vat",
            "period_input_vat",
        }
    )
    # Schedule totals are server-set (TAX-044); a client that changes them is refused.
    SERVER = DeclarationDocument.SERVER | frozenset(
        {"invoice_net_total", "invoice_vat_total", "invoice_gross_total"}
    )
    SCHEDULE_FIELDS = (
        "invoice_number",
        "invoice_date",
        "supplier_name",
        "supplier_tin",
        "net_amount",
        "vat_amount",
        "gross_amount",
        "amount_paid",
        "invoice_evidence_id",
        "contract_evidence_id",
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
        period_output_vat: Any
        period_input_vat: Any
        invoices: list[Any]
        invoice_net_total: Any
        invoice_vat_total: Any
        invoice_gross_total: Any

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
        rows = self._validate_schedule(parent)
        ids = [*ids, *schedule.evidence_ids(rows)]
        if foreign_evidence_ids(self.tenant_id, self.taxpayer_id, ids):
            frappe.throw(
                "evidence_ids must reference this taxpayer's own evidence.",
                guard.InvalidDomainInput,
                title="invalid_reference",
            )
        if self.vat_return_id:
            self._check_vat_return()

    def _validate_schedule(self, parent: Any) -> list[schedule.InvoiceRow]:
        """Period figures and invoice rows (TAX-044); totals are recomputed here."""
        for field in ("period_output_vat", "period_input_vat"):
            if self.get(field) not in (None, ""):
                guard.money(self, field)
        period_end = guard.domain_call(domain.parse_period, parent.period, guard.today()).end
        raw = [{f: row.get(f) for f in self.SCHEDULE_FIELDS} for row in self.get("invoices") or []]
        try:
            rows = schedule.parse_schedule(
                raw, period_end=period_end, today=guard.today(), required=False
            )
        except schedule.ScheduleError as exc:
            first = exc.errors[0]
            where = f"Invoice {first.row + 1}: " if first.row >= 0 else ""
            frappe.throw(where + first.message, guard.InvalidDomainInput, title=first.code)
        total = schedule.totals(rows)
        self.invoice_net_total = total.net
        self.invoice_vat_total = total.vat
        self.invoice_gross_total = total.gross
        return rows

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
        # Declared period figures must agree with the linked return (TAX-044).
        for field, stored in (
            ("period_output_vat", ret.output_vat),
            ("period_input_vat", ret.input_vat),
        ):
            if self.get(field) not in (None, "") and domain.parse_money(
                self.get(field), field
            ) != domain.parse_money(stored, field):
                frappe.throw(
                    f"{field} must match the linked VAT return.",
                    guard.InvalidDomainInput,
                    title="return_mismatch",
                )
        guard.domain_call(
            domain.validate_refund_against_return,
            domain.parse_money(self.claimed_amount, "claimed_amount"),
            domain.parse_money(ret.output_vat, "output_vat"),
            domain.parse_money(ret.input_vat, "input_vat"),
        )
