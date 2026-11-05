"""Pre-submission reconciliation for the taxpayer portal (TAX-044).

Reuses the TAX-048 deterministic reconciliation (``ai.tools.refund_reconciliation``)
without changing it: that file is pinned by approved playbooks (ADR-017) and uses
only the standard library. This module is the only place in the Frappe app that
imports from ``ai``, and it imports only that module (``test_presubmission``
enforces both). See ADR-020.

What the taxpayer sees is limited on purpose:

- figures: claimed amount, VAT on the listed invoices, the part fully supported
  by the documents given, and the rest;
- correction prompts from a fixed allowlist of reconciliation codes, worded as
  guidance. Anything else the reconciliation reports (indicators such as related
  parties or loss periods) is dropped, and the advisory lane proposal
  (``propose_refund_lane``) is never computed here. Prompts never block sending.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from decimal import Decimal
from types import ModuleType
from typing import Any, Final

from vati_tax.submissions import domain
from vati_tax.submissions.invoices import InvoiceRow

SHARED_MODULE: Final = "ai.tools.refund_reconciliation"


class ReconciliationUnavailable(RuntimeError):
    """The shared reconciliation module cannot be imported in this deployment."""


def _shared() -> ModuleType:
    try:
        return importlib.import_module(SHARED_MODULE)
    except ImportError as exc:  # pragma: no cover - depends on the deployment path
        raise ReconciliationUnavailable(
            "The pre-submission check is not available right now."
        ) from exc


def _invoice_id(index: int) -> str:
    return f"invoice-{index}"


def build_record(
    claimed: Decimal,
    output_vat: Decimal,
    input_vat: Decimal,
    rows: Sequence[InvoiceRow],
) -> dict[str, Any]:
    """The TAX-048 record for a draft claim. Money is passed as decimal strings."""
    invoices, contracts, payments = [], [], []
    for index, row in enumerate(rows):
        contract_id = f"contract-{index}" if row.contract_evidence_id else None
        invoices.append(
            {
                "id": _invoice_id(index),
                "direction": "purchase",
                "net_amount": domain.format_money(row.net_amount),
                "vat_amount": domain.format_money(row.vat_amount),
                "gross_amount": domain.format_money(row.gross_amount),
                "contract_id": contract_id,
            }
        )
        if contract_id:
            # The taxpayer attached a contract or purchase order for this invoice.
            contracts.append({"id": contract_id})
        if row.amount_paid is not None:
            payments.append(
                {
                    "id": f"payment-{index}",
                    "invoice_id": _invoice_id(index),
                    "amount": domain.format_money(row.amount_paid),
                }
            )
    return {
        "currency": domain.CURRENCY,
        "return": {
            "id": "draft-claim",
            "refund_claim": domain.format_money(claimed),
            "input_vat_claimed": domain.format_money(input_vat),
            "output_vat": domain.format_money(output_vat),
        },
        "invoices": invoices,
        "contracts": contracts,
        "payments": payments,
    }


# Reconciliation code -> taxpayer prompt. Codes not listed are never shown.
# Wording is guidance only: no accusation, penalty or decision language.
CLAIM_PROMPTS: Final[dict[str, str]] = {
    "refund_claim_exceeds_documented_input_vat": (
        "Your claim is {amount} more than the VAT on the invoices you listed. "
        "Add any missing invoices, or check the amount claimed."
    ),
    "return_arithmetic_mismatch": (
        "The amount claimed should equal the period's input VAT minus its output VAT. "
        "Check these figures and the amount claimed."
    ),
}
INVOICE_PROMPTS: Final[dict[str, str]] = {
    "invoice_total_mismatch": (
        "Invoice {n}: the amount excluding VAT plus the VAT does not equal the total."
    ),
    "payment_total_differs_from_invoice": (
        "Invoice {n}: the amount paid is different from the invoice total. Check the amount paid."
    ),
    "payment_evidence_missing": "Invoice {n}: add the amount paid, if you have paid this invoice.",
    "contract_evidence_missing": (
        "Invoice {n}: if you have a contract or purchase order for it, attach it. "
        "An officer may ask for it later."
    ),
}


def _money(value: Any) -> str:
    return f"{domain.CURRENCY} {value['amount']}" if isinstance(value, dict) else str(value)


def taxpayer_review(result: dict[str, Any], rows: Sequence[InvoiceRow]) -> dict[str, Any]:
    """Allowlisted taxpayer view of a TAX-048 reconciliation result."""
    prompts: list[dict[str, Any]] = []
    for entry in [*result.get("discrepancies", []), *result.get("gaps", [])]:
        code = entry.get("code")
        if code in CLAIM_PROMPTS and not any(p["code"] == code for p in prompts):
            amount = _money(entry.get("amount")) if entry.get("amount") else ""
            prompts.append(
                {
                    "code": code,
                    "invoice": None,
                    "message": CLAIM_PROMPTS[code].format(amount=amount),
                }
            )
    for index, item in enumerate(result.get("line_items", [])):
        if item.get("invoice_id") != _invoice_id(index) or index >= len(rows):
            continue  # defensive: line items follow the invoice order we sent
        for code in item.get("reasons", []):
            if code in INVOICE_PROMPTS:
                prompts.append(
                    {
                        "code": code,
                        "invoice": index,
                        "message": INVOICE_PROMPTS[code].format(n=index + 1),
                    }
                )
    return {
        "calculation_version": result.get("calculation_version"),
        "figures": {
            key: result[key]["amount"]
            for key in (
                "claimed_refund",
                "output_vat",
                "documented_input_vat",
                "explained_amount",
                "unresolved_amount",
            )
            if isinstance(result.get(key), dict)
        },
        "prompts": prompts,
        "complete": not prompts,
    }


def review(
    claimed: Decimal,
    output_vat: Decimal,
    input_vat: Decimal,
    rows: Sequence[InvoiceRow],
) -> dict[str, Any]:
    """Run the shared reconciliation on a draft claim and return the taxpayer view."""
    shared = _shared()
    result = shared.reconcile_refund(build_record(claimed, output_vat, input_vat, rows))
    return taxpayer_review(result, rows)
