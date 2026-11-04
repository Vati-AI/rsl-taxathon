"""Deterministic imported-services VAT and withholding evidence reconciliation (TAX-050).

Each imported-service charge is traced through contract -> invoice -> payment
-> declared VAT treatment -> declared withholding treatment using Decimal
amounts only. Missing records are evidence gaps; contradictory records are
discrepancies. Every gap produces a targeted, neutral evidence question tied to
evidence IDs.

Nothing here determines the applicable law. Statutory VAT and withholding
treatment always stays ``unverified_pending_expert_review``: a declared
treatment is recorded as the taxpayer's statement, never as a liability,
non-compliance or abuse conclusion.

A charge is treated as an imported service only from explicit record markers
(``supplier_residence == "non_resident"`` on the invoice or contract, or the
TAX-024 ``vat_status == "treatment_unverified"`` marker). Residence is never
inferred from names or identifiers.
"""

from collections.abc import Mapping
from decimal import Decimal

from ai.tools.refund_reconciliation import (
    DEFAULT_CURRENCY,
    RefundDataError,
    _items,
    _text,
    _unique_ids,
    money,
    parse_money,
)

REVIEW_VERSION = "tax050-imported-services-v1"
FINDING_CODE = "imported_service_treatment_unverified"
STATUTORY_STATUS = "unverified_pending_expert_review"
MAX_CHARGES = 50
UNDECLARED_VAT_STATUSES = frozenset({"treatment_unverified"})

# Conclusions this playbook must never emit; humans and legal experts decide.
PROHIBITED_CONCLUSIONS = frozenset(
    {
        "withholding_tax_liability_established",
        "vat_liability_established",
        "noncompliance_established",
        "proven_abuse",
        "tax_assessment",
        "penalty",
        "refund_denial",
    }
)


def _is_imported(invoice: Mapping[str, object], contract: Mapping[str, object] | None) -> bool:
    if invoice.get("vat_status") in UNDECLARED_VAT_STATUSES:
        return True
    return any(
        item is not None and item.get("supplier_residence") == "non_resident"
        for item in (invoice, contract)
    )


def _question(
    charge_id: str,
    topic: str,
    evidence_ids: list[str],
    text: str,
    requested_records: list[str],
) -> dict[str, object]:
    return {
        "question_id": f"{charge_id}:{topic}",
        "topic": topic,
        "addressed_to": "taxpayer",
        "evidence_ids": evidence_ids,
        "text": text,
        "requested_records": requested_records,
        "status": "draft_requires_officer_approval",
    }


def _review_charge(
    invoice_id: str,
    invoice: Mapping[str, object],
    contracts: Mapping[str, Mapping[str, object]],
    payments: list[tuple[str, Mapping[str, object]]],
    *,
    period: str,
    currency: str,
) -> dict[str, object]:
    net = parse_money(invoice.get("net_amount"), field=f"invoice {invoice_id}.net_amount")
    vat = parse_money(invoice.get("vat_amount"), field=f"invoice {invoice_id}.vat_amount")
    gross = parse_money(invoice.get("gross_amount"), field=f"invoice {invoice_id}.gross_amount")
    supplier = invoice.get("counterparty_id")
    supplier_ref = supplier if isinstance(supplier, str) else "the supplier"
    contract_id = invoice.get("contract_id")
    contract = contracts.get(contract_id) if isinstance(contract_id, str) else None

    gaps: list[dict[str, object]] = []
    discrepancies: list[dict[str, object]] = []
    questions: list[dict[str, object]] = []
    evidence = [invoice_id]
    if contract is not None and isinstance(contract_id, str):
        evidence.append(contract_id)
    payment_ids = [payment_id for payment_id, _ in payments]
    evidence.extend(payment_ids)

    # Contract link.
    if contract is None:
        contract_status = "missing"
        gaps.append({"code": "contract_evidence_missing", "evidence_ids": [invoice_id]})
        questions.append(
            _question(
                invoice_id,
                "contract",
                [invoice_id],
                f"Please provide the agreement or order under which {supplier_ref} supplied "
                f"the services billed on invoice {invoice_id} for period {period}.",
                ["signed agreement or purchase order", "statement of work"],
            )
        )
    elif contract.get("counterparty_id") != supplier:
        contract_status = "contradictory"
        discrepancies.append(
            {"code": "contract_counterparty_mismatch", "evidence_ids": [invoice_id, contract_id]}
        )
    else:
        contract_status = "linked"

    # Invoice arithmetic.
    invoice_status = "consistent"
    if net + vat != gross:
        invoice_status = "contradictory"
        discrepancies.append({"code": "invoice_total_mismatch", "evidence_ids": [invoice_id]})

    # Payment trail, including any declared amount withheld at source.
    paid = Decimal("0.00")
    withheld = Decimal("0.00")
    withholding_declared = False
    for payment_id, payment in payments:
        paid += parse_money(payment.get("amount"), field=f"payment {payment_id}.amount")
        if "withholding_amount" in payment:
            withholding_declared = True
            withheld += parse_money(
                payment.get("withholding_amount"), field=f"payment {payment_id}.withholding_amount"
            )
    if not payments:
        payment_status = "missing"
        gaps.append({"code": "payment_evidence_missing", "evidence_ids": [invoice_id]})
        questions.append(
            _question(
                invoice_id,
                "payment",
                [invoice_id],
                f"Please provide proof of payment to {supplier_ref} for invoice {invoice_id} "
                f"(gross {currency} {gross}), including date, amount and paying account.",
                ["bank statement or remittance advice", "foreign exchange confirmation"],
            )
        )
    elif paid + withheld != gross:
        payment_status = "contradictory"
        discrepancies.append(
            {
                "code": "payment_total_differs_from_invoice",
                "evidence_ids": [invoice_id, *payment_ids],
            }
        )
    else:
        payment_status = "reconciled"

    # Declared VAT treatment: recorded as a statement, never validated as law.
    vat_status = invoice.get("vat_status")
    declared_vat = invoice.get("declared_vat_treatment")
    if isinstance(declared_vat, str) and declared_vat.strip():
        vat_treatment: dict[str, object] = {
            "status": "declared_by_taxpayer",
            "declared": declared_vat,
        }
    else:
        vat_treatment = {"status": "not_declared", "declared": None}
        gaps.append({"code": "imported_service_vat_treatment_undeclared", "evidence_ids": evidence})
        questions.append(
            _question(
                invoice_id,
                "vat_treatment",
                evidence,
                f"Please state how VAT was accounted for on the services billed by {supplier_ref} "
                f"on invoice {invoice_id} for period {period}, and identify the return line or "
                "working paper where that treatment is recorded.",
                ["VAT working paper for the period", "return line reference"],
            )
        )
    if isinstance(vat_status, str) and vat_status not in UNDECLARED_VAT_STATUSES:
        vat_treatment["invoice_vat_status"] = vat_status

    # Declared withholding treatment.
    if withholding_declared:
        withholding_treatment: dict[str, object] = {
            "status": "declared_by_taxpayer",
            "declared_withheld": money(withheld, currency),
        }
    else:
        withholding_treatment = {"status": "not_declared", "declared_withheld": None}
        gaps.append({"code": "withholding_treatment_evidence_missing", "evidence_ids": evidence})
        payment_ref = ", ".join(payment_ids) if payment_ids else f"invoice {invoice_id}"
        questions.append(
            _question(
                invoice_id,
                "withholding",
                evidence,
                f"Please state whether any amount was withheld from the payment(s) to "
                f"{supplier_ref} ({payment_ref}) and, if so, provide the withholding "
                "remittance record; if nothing was withheld, please say so.",
                ["withholding remittance record or written confirmation"],
            )
        )

    # Where services were performed and received informs expert review only.
    if contract is not None and not contract.get("service_location"):
        questions.append(
            _question(
                invoice_id,
                "service_performance",
                evidence,
                f"Please describe where the services under {contract_id} were performed and "
                "where they were used, with any deliverables or activity reports.",
                ["deliverables or activity reports", "statement of work"],
            )
        )

    unresolved = bool(gaps or discrepancies)
    return {
        "invoice_id": invoice_id,
        "contract_id": contract_id if contract is not None else None,
        "payment_ids": payment_ids,
        "supplier_id": supplier if isinstance(supplier, str) else None,
        "gross": money(gross, currency),
        "vat_charged": money(vat, currency),
        "paid": money(paid, currency),
        "trail": {
            "contract": contract_status,
            "invoice": invoice_status,
            "payment": payment_status,
            "vat_treatment": vat_treatment,
            "withholding_treatment": withholding_treatment,
        },
        "trail_status": "unresolved" if unresolved else "documented_pending_expert_review",
        "gaps": gaps,
        "discrepancies": discrepancies,
        "evidence_questions": questions,
        "statutory_treatment": {"status": STATUTORY_STATUS, "legal_conclusion": None},
        "evidence_ids": evidence,
    }


def review_imported_services(
    record: Mapping[str, object], *, currency: str = DEFAULT_CURRENCY
) -> dict[str, object]:
    """Reconcile imported-service charges with declared VAT/withholding treatment.

    Raises ``RefundDataError`` (stable ``code``) for malformed or unsafe input.
    """
    if record.get("currency", currency) != currency:
        raise RefundDataError(
            "currency_mismatch", "Record currency differs from requested currency"
        )
    period = record.get("period")
    period_label = period if isinstance(period, str) and period else "the period under review"
    contracts = _unique_ids(_items(record, "contracts"), "contract")
    invoices = _unique_ids(_items(record, "invoices"), "invoice")
    payments = _unique_ids(_items(record, "payments"), "payment")
    by_invoice: dict[str, list[tuple[str, Mapping[str, object]]]] = {}
    for payment_id, payment in payments.items():
        by_invoice.setdefault(_text(payment, "invoice_id", field="payment"), []).append(
            (payment_id, payment)
        )

    imported: list[tuple[str, Mapping[str, object]]] = []
    for invoice_id, invoice in invoices.items():
        if invoice.get("direction") != "purchase":
            continue
        contract_id = invoice.get("contract_id")
        contract = contracts.get(contract_id) if isinstance(contract_id, str) else None
        if _is_imported(invoice, contract):
            imported.append((invoice_id, invoice))
    if len(imported) > MAX_CHARGES:
        raise RefundDataError("charge_budget_exceeded", "Too many imported charges for one review")

    charges = [
        _review_charge(
            invoice_id,
            invoice,
            contracts,
            by_invoice.get(invoice_id, []),
            period=period_label,
            currency=currency,
        )
        for invoice_id, invoice in imported
    ]

    def total(key: str, only_unresolved: bool = False) -> dict[str, str]:
        amount = Decimal("0.00")
        for charge in charges:
            if only_unresolved and charge["trail_status"] != "unresolved":
                continue
            value = charge[key]
            raw = value.get("amount") if isinstance(value, dict) else None
            amount += parse_money(raw, field=key)
        return money(amount, currency)

    return {
        "case_ref": record.get("case_id"),
        "synthetic": record.get("synthetic") is True,
        "method": "deterministic_decimal",
        "review_version": REVIEW_VERSION,
        "currency": currency,
        "imported_charge_count": len(charges),
        "imported_gross_total": total("gross"),
        "imported_paid_total": total("paid"),
        "unresolved_trail_amount": total("gross", only_unresolved=True),
        "unresolved_trail": any(charge["trail_status"] == "unresolved" for charge in charges),
        "charges": charges,
        "statutory_treatment": {"status": STATUTORY_STATUS, "legal_conclusion": None},
        "enforcement_conclusion": None,
    }
