"""Deterministic pre-submission checks on a taxpayer's own draft claim (TAX-098).

The taxpayer assistant explains these results; it never computes them. Every issue
uses a code from the taxpayer-facing allowlist (``TaxpayerFacingIssueCode`` in
integration/api/openapi.json, the TAX-044 codes plus the TAX-098 row checks), and
every message is fixed text, not model output. Amounts are ``Decimal`` parsed from
decimal strings; nothing is rounded silently.

Row checks, each reported once on the row it concerns:

- ``invoice_total_mismatch``: net + VAT differs from the declared total;
- ``duplicate_invoice``: the same supplier and supplier invoice number as an earlier
  row (case-insensitive, as the TAX-044 schedule rule does), reported on the later row;
- ``invoice_period_mismatch``: dated before the claim period starts or after it ends
  (the boundary days are inside);
- ``supporting_document_missing``: no uploaded copy of the invoice.

Claim checks: ``return_arithmetic_mismatch`` (claim differs from input VAT less
output VAT) and ``refund_claim_exceeds_documented_input_vat`` (declared input VAT is
more than the VAT on the listed invoices).

``compare_document_fields`` sets what Docling read from each uploaded invoice beside
what the taxpayer declared for that row, and lists the fields for the taxpayer to
confirm. It reports; it never changes a declared value.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

CHECKS_VERSION = "tax098-submission-checks-v1"
_MONEY = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]{1,2})?")
CENT = Decimal("0.01")

ROW_CODES = (
    "invoice_total_mismatch",
    "duplicate_invoice",
    "invoice_period_mismatch",
    "supporting_document_missing",
)
CLAIM_CODES = ("return_arithmetic_mismatch", "refund_claim_exceeds_documented_input_vat")
MESSAGES = {
    "invoice_total_mismatch": (
        "Invoice {n}: the amount excluding VAT plus the VAT does not equal the total entered."
    ),
    "duplicate_invoice": (
        "Invoice {n} has the same supplier and invoice number as invoice {first}."
    ),
    "invoice_period_mismatch": "Invoice {n} is dated outside the claim period.",
    "supporting_document_missing": "Invoice {n} has no uploaded copy.",
    "return_arithmetic_mismatch": (
        "The refund claimed does not equal the input VAT less the output VAT."
    ),
    "refund_claim_exceeds_documented_input_vat": (
        "The input VAT claimed is more than the VAT on the invoices listed."
    ),
}
# Declared row field -> the field name Docling's invoice extraction uses for it.
DOCUMENT_FIELDS = {
    "supplier_invoice_number": "invoice_number",
    "invoice_date": "invoice_date",
    "supplier_name": "supplier_name",
    "supplier_tin": "supplier_tin",
    "net_amount": "net_amount",
    "vat_amount": "vat_amount",
    "gross_amount": "gross_amount",
}
AMOUNT_FIELDS = frozenset({"net_amount", "vat_amount", "gross_amount", "amount_paid"})


class SubmissionDataError(ValueError):
    """The stored submission cannot be checked safely; carries a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def money(value: object, field: str) -> Decimal:
    if not isinstance(value, str) or not _MONEY.fullmatch(value):
        raise SubmissionDataError("invalid_money", f"{field} must be a decimal string")
    return Decimal(value).quantize(CENT)


def _date(value: object, field: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise SubmissionDataError("invalid_date", f"{field} must be an ISO date") from None


def _issue(code: str, invoice_number: int | None, **fmt: object) -> dict[str, Any]:
    return {
        "code": code,
        "invoice_number": invoice_number,
        "message": MESSAGES[code].format(n=invoice_number, **fmt),
    }


def validate_submission(submission: Mapping[str, Any]) -> dict[str, Any]:
    """Issues and figures for one submission, in row order then claim-level."""
    period = submission.get("period") or {}
    start = _date(period.get("start"), "period.start")
    end = _date(period.get("end"), "period.end")
    rows: Sequence[Mapping[str, Any]] = submission.get("invoices") or []
    issues: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], int] = {}
    duplicates: dict[int, int] = {}
    documented = Decimal("0.00")
    for row in rows:
        n = row.get("invoice_number")
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise SubmissionDataError("invalid_row", "invoice_number must be a positive integer")
        net = money(row.get("net_amount"), f"invoice {n}.net_amount")
        vat = money(row.get("vat_amount"), f"invoice {n}.vat_amount")
        gross = money(row.get("gross_amount"), f"invoice {n}.gross_amount")
        documented += vat
        if net + vat != gross:
            issues.append(_issue("invoice_total_mismatch", n))
        key = (
            str(row.get("supplier_name") or "").strip().casefold(),
            str(row.get("supplier_invoice_number") or "").strip().casefold(),
        )
        if key in seen:
            issues.append(_issue("duplicate_invoice", n, first=seen[key]))
            duplicates[n] = seen[key]
        else:
            seen[key] = n
        dated = _date(row.get("invoice_date"), f"invoice {n}.invoice_date")
        if not start <= dated <= end:
            issues.append(_issue("invoice_period_mismatch", n))
        if not row.get("invoice_evidence_id"):
            issues.append(_issue("supporting_document_missing", n))
    declared = submission.get("declared") or {}
    claimed = money(declared.get("claimed_refund"), "declared.claimed_refund")
    output = money(declared.get("output_vat"), "declared.output_vat")
    input_claimed = money(declared.get("input_vat_claimed"), "declared.input_vat_claimed")
    if claimed != max(Decimal("0.00"), input_claimed - output):
        issues.append(_issue("return_arithmetic_mismatch", None))
    if input_claimed > documented:
        issues.append(_issue("refund_claim_exceeds_documented_input_vat", None))
    return {
        "calculation_version": CHECKS_VERSION,
        "complete": not issues,
        "figures": {
            "claimed_refund": claimed,
            "output_vat": output,
            "documented_input_vat": documented,
        },
        "issues": issues,
        # Row -> the earlier row it repeats (for explanations; not a contract field).
        "duplicates": duplicates,
    }


def _same(field: str, extracted: object, declared: object) -> bool:
    if field in AMOUNT_FIELDS:
        try:
            return money(str(extracted), field) == money(str(declared), field)
        except SubmissionDataError:
            return False
    return str(extracted).strip().casefold() == str(declared).strip().casefold()


def compare_document_fields(
    row: Mapping[str, Any], document: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Fields where the uploaded invoice and the declared row differ, to confirm.

    Only a completed extraction is compared; a field the extraction did not read is
    not reported as different (the document may simply not show it).
    """
    if document.get("extraction_status") != "completed":
        return []
    extracted = document.get("extracted") or {}
    out = []
    for row_field, doc_field in DOCUMENT_FIELDS.items():
        value = extracted.get(doc_field)
        declared = row.get(row_field)
        if value is None or declared is None or _same(row_field, value, declared):
            continue
        out.append(
            {
                "field": doc_field,
                "extracted_value": str(value),
                "declared_value": str(declared),
                "reason": "differs_from_declared",
            }
        )
    return out
