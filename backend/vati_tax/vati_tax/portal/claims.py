"""VAT refund claim form rules for the taxpayer portal (TAX-043).

Pure functions with no Frappe import, so they are testable without a bench.
``vati_tax.portal.api.submit_refund_claim`` applies them before it creates the
TAX-031 ``Vati Submission`` and ``Vati Refund Claim`` records, whose
controllers enforce the same rules again (defence in depth).

The portal checks every field at once so the taxpayer sees all problems in one
pass, with plain-language messages tied to the field that needs attention.
Messages never echo the submitted values or any document content.

Idempotency follows docs/API_STANDARD.md: the same key with the same request
replays the stored result; the same key with a different request is an
``idempotency_conflict``. ``same_request`` decides which.
"""

from __future__ import annotations

import datetime
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from vati_tax.submissions import domain
from vati_tax.submissions import invoices as schedule

# Same choices as the Vati Refund Claim DocType (checked by the bench tests).
REASONS: Final = ("excess_input_vat", "zero_rated_supplies", "other")
# Monthly periods only: the shared case contract (CreateCaseRequest.period) is monthly.
_MONTH = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MAX_EXPLANATION: Final = 2000
MAX_EMAIL: Final = 140
# A claim is sent with its supporting documents; at least one is required.
MIN_DOCUMENTS: Final = 1
MAX_DOCUMENTS: Final = domain.MAX_EVIDENCE_IDS


@dataclass(frozen=True)
class FieldError:
    field: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code, "message": self.message}


class ClaimInputError(ValueError):
    """One or more fields are invalid (API ``invalid_domain_input``, HTTP 422)."""

    def __init__(self, errors: list[FieldError]):
        super().__init__("Check the highlighted fields.")
        self.errors = errors


@dataclass(frozen=True)
class ClaimRequest:
    """A validated, normalised refund claim as the portal will store it."""

    idempotency_key: str
    period: str
    claimed_amount: Decimal
    claim_reason: str
    explanation: str | None
    contact_email: str | None
    evidence_ids: tuple[str, ...]
    # TAX-044: the period's declared figures and the purchase-invoice schedule.
    period_output_vat: Decimal = Decimal("0.00")
    period_input_vat: Decimal = Decimal("0.00")
    invoices: tuple[schedule.InvoiceRow, ...] = ()


def _text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _truthy(value: object) -> bool:
    return value is True or value in (1, "1", "true", "True")


def _evidence_list(value: object) -> object:
    # Frappe passes JSON-body lists through; form posts send a JSON string.
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def validate_claim(raw: Mapping[str, Any], today: datetime.date) -> ClaimRequest:
    """Validate the whole form; raise ``ClaimInputError`` listing every problem."""
    errors: list[FieldError] = []
    period_end: datetime.date | None = None

    key = raw.get("idempotency_key")
    try:
        domain.validate_idempotency_key(key)
        if key in (None, ""):
            raise domain.DomainError("missing", "idempotency_key", "required")
    except domain.DomainError:
        errors.append(
            FieldError(
                "idempotency_key",
                "invalid_idempotency_key",
                "This form could not be sent safely. Reload the page and try again.",
            )
        )

    period = _text(raw.get("period"))
    if period is None:
        errors.append(
            FieldError("period", "required", "Enter the tax period, for example 2026-09.")
        )
    elif not _MONTH.fullmatch(period):
        errors.append(
            FieldError(
                "period", "invalid_period", "Enter the tax period as a month, for example 2026-09."
            )
        )
    else:
        try:
            period_end = domain.parse_period(period, today).end
        except domain.DomainError as exc:
            message = (
                "The tax period cannot be in the future."
                if exc.reason == "future_period"
                else f"Enter a tax period from {domain.MIN_YEAR} onwards."
            )
            errors.append(FieldError("period", exc.reason, message))

    amount: Decimal | None = None
    try:
        amount = domain.parse_money(raw.get("claimed_amount"), "claimed_amount", allow_zero=False)
    except domain.DomainError as exc:
        message = {
            "required": "Enter the amount you are claiming in maloti.",
            "zero_amount": "The amount claimed must be more than 0.00.",
            "negative_amount": "The amount claimed must be more than 0.00.",
            "amount_out_of_range": "The amount is too large. Check the figure.",
        }.get(
            exc.reason,
            "Enter numbers only, with up to two decimal places, for example 15000.00.",
        )
        errors.append(FieldError("claimed_amount", exc.reason, message))

    reason = _text(raw.get("claim_reason"))
    if reason not in REASONS:
        errors.append(
            FieldError("claim_reason", "invalid_choice", "Choose the reason for the claim.")
        )

    explanation = _text(raw.get("explanation"))
    if reason == "other" and explanation is None:
        errors.append(
            FieldError(
                "explanation",
                "explanation_required",
                "Explain why you are claiming a refund.",
            )
        )
    elif explanation is not None and len(explanation) > MAX_EXPLANATION:
        errors.append(
            FieldError(
                "explanation",
                "too_long",
                f"Keep the explanation under {MAX_EXPLANATION} characters.",
            )
        )

    email = _text(raw.get("contact_email"))
    if email is not None and (len(email) > MAX_EMAIL or not _EMAIL.fullmatch(email)):
        errors.append(
            FieldError(
                "contact_email",
                "invalid_email",
                "Enter an email address like name@example.com, or leave it blank.",
            )
        )

    evidence: list[str] = []
    try:
        evidence = domain.evidence_ids(_evidence_list(raw.get("evidence_ids")))
    except domain.DomainError as exc:
        message = (
            f"Attach at most {MAX_DOCUMENTS} documents."
            if exc.reason == "too_many"
            else "A document reference is not valid. Remove it and upload the file again."
        )
        errors.append(FieldError("evidence_ids", exc.reason, message))
    else:
        if len(evidence) < MIN_DOCUMENTS:
            errors.append(
                FieldError(
                    "evidence_ids",
                    "evidence_required",
                    "Upload at least one supporting document, such as the invoices behind the claim.",
                )
            )

    figures = _period_figures(raw, errors)
    rows = _schedule(raw, period_end, today, set(evidence), errors)

    if not _truthy(raw.get("declaration")):
        errors.append(
            FieldError("declaration", "declaration_required", "Confirm the declaration to send.")
        )

    if errors:
        raise ClaimInputError(errors)
    assert amount is not None and period is not None and reason is not None
    return ClaimRequest(
        idempotency_key=str(key),
        period=period,
        claimed_amount=amount,
        claim_reason=reason,
        explanation=explanation,
        contact_email=email,
        evidence_ids=tuple(evidence),
        period_output_vat=figures[0] or Decimal("0.00"),
        period_input_vat=figures[1] or Decimal("0.00"),
        invoices=tuple(rows),
    )


_FIGURE_LABELS = {"period_output_vat": "output VAT", "period_input_vat": "input VAT"}


def _period_figures(
    raw: Mapping[str, Any], errors: list[FieldError]
) -> tuple[Decimal | None, Decimal | None]:
    """The period's declared output and input VAT (TAX-044); zero is allowed."""
    values: list[Decimal | None] = []
    for field, label in _FIGURE_LABELS.items():
        try:
            values.append(domain.parse_money(raw.get(field), field))
        except domain.DomainError as exc:
            message = (
                f"Enter the {label} for the period, or 0.00 if there was none."
                if exc.reason == "required"
                else "Enter numbers only, with up to two decimal places, for example 1500.00."
            )
            errors.append(FieldError(field, exc.reason, message))
            values.append(None)
    return values[0], values[1]


def _schedule(
    raw: Mapping[str, Any],
    period_end: datetime.date | None,
    today: datetime.date,
    uploaded: set[str],
    errors: list[FieldError],
) -> list[schedule.InvoiceRow]:
    """The purchase-invoice schedule (TAX-044); each row's documents must be uploaded here."""
    rows_raw = raw.get("invoices")
    if isinstance(rows_raw, str):
        try:
            rows_raw = json.loads(rows_raw)
        except ValueError:
            rows_raw = None
    try:
        rows = schedule.parse_schedule(rows_raw, period_end=period_end, today=today)
    except schedule.ScheduleError as exc:
        for e in exc.errors:
            field = "invoices" if e.row < 0 else f"invoices.{e.row}.{e.field}"
            errors.append(FieldError(field, e.code, e.message))
        return []
    for index, row in enumerate(rows):
        for field, evidence_id in (("invoice_evidence_id", row.invoice_evidence_id),
                                   ("contract_evidence_id", row.contract_evidence_id)):  # fmt: skip
            if evidence_id and evidence_id not in uploaded:
                errors.append(
                    FieldError(
                        f"invoices.{index}.{field}",
                        "not_uploaded",
                        "Choose a document you uploaded for this claim.",
                    )
                )
    return rows


def schedule_fingerprint(rows: Any) -> list[tuple[Any, ...]]:
    """Comparable form of invoice rows, whether parsed or read back from the database."""
    out = []
    for row in rows or []:
        get = row.get if isinstance(row, Mapping) else (lambda f, r=row: getattr(r, f, None))
        paid = get("amount_paid")
        out.append(
            (
                str(get("invoice_number") or "").strip(),
                str(get("invoice_date") or "")[:10],
                str(get("supplier_name") or "").strip(),
                (str(get("supplier_tin") or "").strip() or None),
                domain.parse_money(get("net_amount"), "net_amount"),
                domain.parse_money(get("vat_amount"), "vat_amount"),
                domain.parse_money(get("gross_amount"), "gross_amount"),
                # Zero paid is "not paid", as in submissions.invoices.parse_row.
                None
                if paid in (None, "") or domain.parse_money(paid, "amount_paid") == 0
                else domain.parse_money(paid, "amount_paid"),
                get("invoice_evidence_id"),
                get("contract_evidence_id") or None,
            )
        )
    return out


def same_request(request: ClaimRequest, stored: Mapping[str, Any]) -> bool:
    """Whether a stored claim matches ``request`` (replay) or not (conflict).

    ``stored`` holds the submission and claim fields: ``submission_type``,
    ``period``, ``claimed_amount``, ``claim_reason``, ``explanation``,
    ``contact_email`` and ``evidence_ids`` (JSON text or a list), plus (TAX-044)
    ``period_output_vat``, ``period_input_vat`` and the ``invoices`` rows.
    """
    if stored.get("submission_type") != "refund_claim":
        return False
    try:
        stored_amount = domain.parse_money(stored.get("claimed_amount"), "claimed_amount")
        stored_evidence = domain.evidence_ids(_evidence_list(stored.get("evidence_ids")))
        stored_output = domain.parse_money(stored.get("period_output_vat") or 0, "output")
        stored_input = domain.parse_money(stored.get("period_input_vat") or 0, "input")
        stored_rows = schedule_fingerprint(stored.get("invoices"))
    except domain.DomainError:
        return False
    return (
        stored.get("period") == request.period
        and stored_amount == request.claimed_amount
        and stored.get("claim_reason") == request.claim_reason
        and _text(stored.get("explanation")) == request.explanation
        and _text(stored.get("contact_email")) == request.contact_email
        and sorted(stored_evidence) == sorted(request.evidence_ids)
        and stored_output == request.period_output_vat
        and stored_input == request.period_input_vat
        and stored_rows == schedule_fingerprint(request.invoices)
    )
