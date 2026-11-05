"""Purchase-invoice schedule rules for VAT refund claims (TAX-044).

Pure functions with no Frappe import, so they are testable without a bench.
The ``Vati Refund Claim`` controller and the taxpayer portal both use them, so
the server and the form can never disagree about what is valid.

Each row is a purchase invoice the taxpayer typed in. Amounts are exact
two-decimal LSL ``Decimal`` values (``domain.parse_money``); the server
recomputes every total itself. Only arithmetic and dates are checked: no VAT
rate or tax-law rule is assumed here (there is no authoritative RSL source in
this repository for one).
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from vati_tax.submissions import domain

MAX_ROWS: Final = 200
MAX_TEXT: Final = 140
ZERO: Final = Decimal("0.00")


@dataclass(frozen=True)
class RowError:
    row: int  # zero-based row index
    field: str
    code: str
    message: str


class ScheduleError(ValueError):
    """One or more rows are invalid (API ``invalid_domain_input``, HTTP 422)."""

    def __init__(self, errors: list[RowError]):
        super().__init__(errors[0].message if errors else "The invoice schedule is not valid.")
        self.errors = errors


@dataclass(frozen=True)
class InvoiceRow:
    invoice_number: str
    invoice_date: datetime.date
    supplier_name: str
    supplier_tin: str | None
    net_amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal
    amount_paid: Decimal | None
    invoice_evidence_id: str
    contract_evidence_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "invoice_number": self.invoice_number,
            "invoice_date": self.invoice_date.isoformat(),
            "supplier_name": self.supplier_name,
            "supplier_tin": self.supplier_tin,
            "net_amount": domain.format_money(self.net_amount),
            "vat_amount": domain.format_money(self.vat_amount),
            "gross_amount": domain.format_money(self.gross_amount),
            "amount_paid": None
            if self.amount_paid is None
            else domain.format_money(self.amount_paid),
            "invoice_evidence_id": self.invoice_evidence_id,
            "contract_evidence_id": self.contract_evidence_id,
        }


@dataclass(frozen=True)
class Totals:
    net: Decimal
    vat: Decimal
    gross: Decimal


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


_MONEY_MESSAGES = {
    "required": "Enter the amount.",
    "negative_amount": "The amount cannot be negative.",
    "amount_out_of_range": "The amount is too large. Check the figure.",
}


def _money(raw: Mapping[str, Any], field: str, row: int, errors: list[RowError]) -> Decimal | None:
    try:
        return domain.parse_money(raw.get(field), field)
    except domain.DomainError as exc:
        message = _MONEY_MESSAGES.get(
            exc.reason, "Enter numbers only, with up to two decimal places, for example 1500.00."
        )
        errors.append(RowError(row, field, exc.reason, message))
        return None


def parse_row(
    raw: Mapping[str, Any], row: int, period_end: datetime.date | None, today: datetime.date
) -> tuple[InvoiceRow | None, list[RowError]]:
    """Validate one row; return the row (if valid) and its problems."""
    errors: list[RowError] = []
    number = _text(raw.get("invoice_number"))
    supplier = _text(raw.get("supplier_name"))
    tin = _text(raw.get("supplier_tin"))
    if number is None:
        errors.append(RowError(row, "invoice_number", "required", "Enter the invoice number."))
    elif len(number) > MAX_TEXT:
        errors.append(
            RowError(row, "invoice_number", "too_long", "The invoice number is too long.")
        )
    if supplier is None:
        errors.append(RowError(row, "supplier_name", "required", "Enter the supplier's name."))
    elif len(supplier) > MAX_TEXT:
        errors.append(RowError(row, "supplier_name", "too_long", "The supplier name is too long."))
    if tin is not None and len(tin) > 30:
        errors.append(RowError(row, "supplier_tin", "too_long", "The supplier TIN is too long."))

    invoice_date: datetime.date | None = None
    if raw.get("invoice_date") in (None, ""):
        errors.append(RowError(row, "invoice_date", "required", "Enter the invoice date."))
    else:
        try:
            invoice_date = domain.parse_date(raw.get("invoice_date"), "invoice_date")
        except domain.DomainError:
            errors.append(
                RowError(row, "invoice_date", "invalid_date", "Enter the date as YYYY-MM-DD.")
            )
        else:
            if invoice_date > today:
                errors.append(
                    RowError(
                        row,
                        "invoice_date",
                        "future_date",
                        "The invoice date cannot be in the future.",
                    )
                )
            elif period_end is not None and invoice_date > period_end:
                errors.append(
                    RowError(
                        row,
                        "invoice_date",
                        "after_period",
                        "The invoice date is after the end of the claim period.",
                    )
                )

    net = _money(raw, "net_amount", row, errors)
    vat = _money(raw, "vat_amount", row, errors)
    gross = _money(raw, "gross_amount", row, errors)
    if net is not None and vat is not None and gross is not None and net + vat != gross:
        errors.append(
            RowError(
                row,
                "gross_amount",
                "invoice_total_mismatch",
                "The amount excluding VAT plus the VAT must equal the total.",
            )
        )

    paid: Decimal | None = None
    if raw.get("amount_paid") not in (None, ""):
        paid = _money(raw, "amount_paid", row, errors)
        # Zero paid means nothing paid yet. Frappe also stores an empty Currency
        # field as 0, so this keeps a stored row equal to the row that was sent.
        if paid == ZERO:
            paid = None

    evidence_id: str | None = None
    try:
        evidence_id = domain.canonical_uuid(raw.get("invoice_evidence_id"), "invoice_evidence_id")
    except domain.DomainError:
        errors.append(
            RowError(
                row, "invoice_evidence_id", "required", "Choose the uploaded copy of this invoice."
            )
        )
    contract_id: str | None = None
    if raw.get("contract_evidence_id") not in (None, ""):
        try:
            contract_id = domain.canonical_uuid(
                raw.get("contract_evidence_id"), "contract_evidence_id"
            )
        except domain.DomainError:
            errors.append(
                RowError(
                    row, "contract_evidence_id", "invalid_uuid", "Choose an uploaded document."
                )
            )

    if errors:
        return None, errors
    assert number and supplier and invoice_date and evidence_id
    assert net is not None and vat is not None and gross is not None
    return (
        InvoiceRow(
            number, invoice_date, supplier, tin, net, vat, gross, paid, evidence_id, contract_id
        ),
        [],
    )


def parse_schedule(
    raw_rows: object,
    *,
    period_end: datetime.date | None,
    today: datetime.date,
    required: bool = True,
) -> list[InvoiceRow]:
    """Validate a whole schedule; raise ``ScheduleError`` listing every problem."""
    if raw_rows in (None, ""):
        raw_rows = []
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, str | bytes):
        raise ScheduleError(
            [
                RowError(
                    -1, "invoices", "invalid_type", "Add the purchase invoices behind the claim."
                )
            ]
        )
    if required and not raw_rows:
        raise ScheduleError(
            [RowError(-1, "invoices", "required", "Add at least one purchase invoice.")]
        )
    if len(raw_rows) > MAX_ROWS:
        raise ScheduleError(
            [RowError(-1, "invoices", "too_many", f"Add at most {MAX_ROWS} invoices.")]
        )
    rows: list[InvoiceRow] = []
    errors: list[RowError] = []
    seen: dict[tuple[str, str], int] = {}
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, Mapping):
            errors.append(
                RowError(index, "invoices", "invalid_type", "This invoice row is not valid.")
            )
            continue
        row, row_errors = parse_row(raw, index, period_end, today)
        errors.extend(row_errors)
        if row is None:
            continue
        key = (row.supplier_name.casefold(), row.invoice_number.casefold())
        if key in seen:
            errors.append(
                RowError(
                    index,
                    "invoice_number",
                    "duplicate_invoice",
                    f"This invoice is already listed in row {seen[key] + 1}.",
                )
            )
            continue
        seen[key] = index
        rows.append(row)
    if errors:
        raise ScheduleError(errors)
    return rows


def totals(rows: Sequence[InvoiceRow]) -> Totals:
    """Totals recomputed by the server; never taken from the client."""
    return Totals(
        net=sum((r.net_amount for r in rows), ZERO),
        vat=sum((r.vat_amount for r in rows), ZERO),
        gross=sum((r.gross_amount for r in rows), ZERO),
    )


def evidence_ids(rows: Sequence[InvoiceRow]) -> list[str]:
    """Every document a schedule points at (invoice copies and contracts)."""
    ids: list[str] = []
    for row in rows:
        ids.append(row.invoice_evidence_id)
        if row.contract_evidence_id:
            ids.append(row.contract_evidence_id)
    return list(dict.fromkeys(ids))
