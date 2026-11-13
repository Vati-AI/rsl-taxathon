"""Submission and tax-domain rules (TAX-031).

Pure functions with no Frappe import, so the rules are testable without a bench.
``vati_tax.submissions.guard`` applies them inside the DocType controllers
(``Vati Taxpayer``, ``Vati Submission``, ``Vati VAT Return``, ``Vati Refund
Claim`` and ``Vati Imported Service``).

Model (see docs/decisions/ADR-010-submission-doctypes.md):

- Money is ``Decimal`` with exactly two decimal places in LSL. Values arrive as
  strings (REST), floats (Frappe's Currency read path) or ``Decimal``; each is
  parsed strictly *before* Frappe casts it, so a malformed amount is rejected
  instead of becoming ``0``.
- A tax period is a month (``YYYY-MM``, the API contract form) or a quarter
  (``YYYY-Qn``, used by the TAX-024 synthetic scenarios). It must not start in
  the future.
- Frappe owns submission status: ``draft`` -> ``submitted`` -> optionally
  ``correction_needed`` -> ``submitted``, or ``draft`` -> ``withdrawn``. Case,
  risk and investigation state stay in Jube; nothing here assesses tax.
- The figures in a submission are the taxpayer's declaration. Only the taxpayer
  (or a site operator) edits them, and only while the submission is editable.
"""

from __future__ import annotations

import calendar
import datetime
import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

CURRENCY: Final = "LSL"
CENT: Final = Decimal("0.01")
# Exclusive bound. Keeps every 2-decimal amount exactly representable when
# Frappe's read path surfaces a DECIMAL column as a float.
MAX_AMOUNT: Final = Decimal("1000000000000")
_MONEY = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]{1,2})?$")

MIN_YEAR: Final = 2000
_PERIOD = re.compile(r"^([0-9]{4})-(?:(0[1-9]|1[0-2])|Q([1-4]))$")

SubmissionType = Literal["vat_return", "refund_claim", "paye_return", "withholding_return"]
SUBMISSION_TYPES: Final[tuple[SubmissionType, ...]] = (
    "vat_return",
    "refund_claim",
    "paye_return",
    "withholding_return",
)
# Submission types whose figures live in a detail DocType that must exist
# before the submission can be sent.
REQUIRED_DETAIL: Final[dict[str, str]] = {
    "vat_return": "Vati VAT Return",
    "refund_claim": "Vati Refund Claim",
}
IMPORTED_SERVICE_TYPES: Final = frozenset({"vat_return", "withholding_return"})
REFERENCE_PREFIX: Final[dict[str, str]] = {
    "vat_return": "VAT",
    "refund_claim": "REF",
    "paye_return": "PAYE",
    "withholding_return": "WHT",
}

DRAFT: Final = "draft"
SUBMITTED: Final = "submitted"
CORRECTION_NEEDED: Final = "correction_needed"
WITHDRAWN: Final = "withdrawn"
STATUSES: Final = (DRAFT, SUBMITTED, CORRECTION_NEEDED, WITHDRAWN)
EDITABLE_STATUSES: Final = frozenset({DRAFT, CORRECTION_NEEDED})

Actor = Literal["operator", "taxpayer", "auditor", "specialist", "supervisor", "service", "none"]

# Who may move a submission between statuses. Operators may take any listed
# transition (site administration, synthetic seeding); no one else may take an
# unlisted one.
TRANSITIONS: Final[dict[tuple[str, str], frozenset[str]]] = {
    (DRAFT, SUBMITTED): frozenset({"taxpayer"}),
    (CORRECTION_NEEDED, SUBMITTED): frozenset({"taxpayer"}),
    (DRAFT, WITHDRAWN): frozenset({"taxpayer"}),
    (SUBMITTED, CORRECTION_NEEDED): frozenset({"auditor", "supervisor"}),
}

# Where a submission came from. ``rsl_online_refunds`` (TAX-122, ADR-043): the claim was
# filed in RSL's Online Refunds Application and read through the TAX-111 contract; the
# pipeline service identity creates and sends it, and no taxpayer can.
SOURCE_PORTAL: Final = "portal"
SOURCE_RSL_ONLINE_REFUNDS: Final = "rsl_online_refunds"
SOURCES: Final = (SOURCE_PORTAL, "api", "synthetic_seed", SOURCE_RSL_ONLINE_REFUNDS)
RSL_SOURCES: Final = frozenset({SOURCE_RSL_ONLINE_REFUNDS})
# TAX-125: site key of the fixture-only Online Refunds simulator. Where it is on, a portal
# send stands in for a claim filed with RSL (fixtures and the demo).
SIMULATOR_KEY: Final = "vati_online_refunds_simulator"
NOT_REFERRED_MESSAGE: Final = (
    "File with RSL: refund claims in the Online Refunds Application, returns in e-Tax. "
    "Vati works on the cases RSL refers."
)
# The only transition the service identity takes, and only for an RSL-sourced claim.
RSL_INTAKE_TRANSITIONS: Final[dict[tuple[str, str], frozenset[str]]] = {
    (DRAFT, SUBMITTED): frozenset({"service"}),
}

TAXPAYER_TYPES: Final = ("individual", "company", "partnership", "trust", "other")
_TIN = re.compile(r"^[0-9]{6,15}$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")
HOME_COUNTRY: Final = "LS"
MAX_EVIDENCE_IDS: Final = 50
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9:._-]{16,255}$")
_DATELIKE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}([ T]|$)")


class DomainError(ValueError):
    """Invalid domain input (API ``invalid_domain_input``, HTTP 422)."""

    code = "invalid_domain_input"

    def __init__(self, message: str, field: str, reason: str):
        super().__init__(message)
        self.field = field
        self.reason = reason


class TransitionDenied(Exception):
    """The actor may not take this status transition (HTTP 403)."""


class WriteDenied(Exception):
    """The actor changed a field it may not change (HTTP 403)."""


# Money -----------------------------------------------------------------------


def parse_money(
    value: object, field: str, *, allow_negative: bool = False, allow_zero: bool = True
) -> Decimal:
    """Strictly parse an LSL amount to a 2-decimal ``Decimal``.

    Accepts a decimal string (``"15000"``, ``"15000.5"``, ``"15000.50"``), an
    ``int``, a ``Decimal`` or a finite ``float`` with at most two decimals.
    Rejects missing values, booleans, exponents, thousands separators, more than
    two decimals, NaN/infinity, negatives (unless allowed), zero (unless
    allowed) and amounts of 10^12 or more.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise DomainError(f"{field} is required.", field, "required")
    if isinstance(value, bool):
        raise DomainError(f"{field} must be an amount.", field, "invalid_amount")
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, float):
        # repr gives the shortest round-tripping text, so 15000.1 -> "15000.1".
        text = repr(value)
        if "e" in text.lower() or text in {"nan", "inf", "-inf"}:
            raise DomainError(f"{field} must be an amount.", field, "invalid_amount")
        amount = Decimal(text)
    elif isinstance(value, str):
        text = value.strip()
        if not _MONEY.fullmatch(text):
            raise DomainError(
                f"{field} must be a decimal amount with at most two decimal places.",
                field,
                "invalid_amount",
            )
        amount = Decimal(text)
    else:
        raise DomainError(f"{field} must be an amount.", field, "invalid_amount")
    if not amount.is_finite():
        raise DomainError(f"{field} must be an amount.", field, "invalid_amount")
    try:
        quantized = amount.quantize(CENT)
    except InvalidOperation:
        raise DomainError(f"{field} is too large.", field, "amount_out_of_range") from None
    if quantized != amount:
        raise DomainError(f"{field} must have at most two decimal places.", field, "invalid_amount")
    if abs(quantized) >= MAX_AMOUNT:
        raise DomainError(f"{field} is too large.", field, "amount_out_of_range")
    if quantized < 0 and not allow_negative:
        raise DomainError(f"{field} cannot be negative.", field, "negative_amount")
    if quantized == 0 and not allow_zero:
        raise DomainError(f"{field} must be more than zero.", field, "zero_amount")
    return quantized + Decimal("0.00")  # normalise -0.00 to 0.00


def format_money(amount: Decimal) -> str:
    """Contract form of an amount: ``"15000.00"``."""
    return str(amount.quantize(CENT))


def validate_currency(value: object, field: str = "currency") -> str:
    """Only LSL is accepted; an empty value defaults to LSL."""
    if value is None or value == "":
        return CURRENCY
    if value != CURRENCY:
        raise DomainError(f"{field} must be {CURRENCY}.", field, "unsupported_currency")
    return CURRENCY


# Periods ---------------------------------------------------------------------


@dataclass(frozen=True)
class Period:
    label: str
    kind: Literal["month", "quarter"]
    start: datetime.date
    end: datetime.date

    def contains(self, day: datetime.date) -> bool:
        return self.start <= day <= self.end


def parse_period(value: object, today: datetime.date, field: str = "period") -> Period:
    """Parse ``YYYY-MM`` or ``YYYY-Qn``; reject malformed and future periods."""
    text = value.strip() if isinstance(value, str) else None
    match = _PERIOD.fullmatch(text) if text else None
    if not text or match is None:
        raise DomainError(
            f"{field} must be a month (YYYY-MM) or a quarter (YYYY-Q1 to YYYY-Q4).",
            field,
            "invalid_period",
        )
    year = int(match.group(1))
    if year < MIN_YEAR:
        raise DomainError(f"{field} is before {MIN_YEAR}.", field, "invalid_period")
    if match.group(2):
        first_month, last_month, kind = int(match.group(2)), int(match.group(2)), "month"
    else:
        quarter = int(match.group(3))
        first_month, last_month, kind = quarter * 3 - 2, quarter * 3, "quarter"
    start = datetime.date(year, first_month, 1)
    end = datetime.date(year, last_month, calendar.monthrange(year, last_month)[1])
    if start > today:
        raise DomainError(f"{field} cannot be in the future.", field, "future_period")
    return Period(text, kind, start, end)  # type: ignore[arg-type]


def parse_date(value: object, field: str) -> datetime.date:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise DomainError(f"{field} must be a date (YYYY-MM-DD).", field, "invalid_date")


# Identifiers -----------------------------------------------------------------


def canonical_uuid(value: object, field: str) -> str:
    if isinstance(value, str) and len(value.strip()) == 36:
        try:
            return str(uuid.UUID(value.strip()))
        except ValueError:
            pass
    raise DomainError(f"{field} must be a UUID.", field, "invalid_uuid")


def optional_uuid(value: object, field: str) -> str | None:
    return None if value in (None, "") else canonical_uuid(value, field)


def new_uuid() -> str:
    return str(uuid.uuid4())


def evidence_ids(value: object, field: str = "evidence_ids") -> list[str]:
    """Evidence references (TAX-032 evidence IDs): unique UUIDs, at most 50."""
    if value in (None, ""):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise DomainError(f"{field} must be a list of UUIDs.", field, "invalid_uuid") from None
    if not isinstance(value, list):
        raise DomainError(f"{field} must be a list of UUIDs.", field, "invalid_uuid")
    ids = [canonical_uuid(item, field) for item in value]
    if len(ids) > MAX_EVIDENCE_IDS:
        raise DomainError(f"{field} has more than {MAX_EVIDENCE_IDS} items.", field, "too_many")
    return list(dict.fromkeys(ids))


def scoped_key(*parts: str) -> str:
    """SHA-256 uniqueness key (same approach as ADR-006): no composite unique index."""
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def validate_idempotency_key(value: object) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not IDEMPOTENCY_KEY.fullmatch(value):
        raise DomainError(
            "idempotency_key must be 16-255 characters of letters, digits and :._-",
            "idempotency_key",
            "invalid_idempotency_key",
        )
    return value


# Taxpayer --------------------------------------------------------------------


def validate_tin(value: object) -> str | None:
    """Synthetic taxpayer identification number: 6 to 15 digits, or empty."""
    if value in (None, ""):
        return None
    text = value.strip() if isinstance(value, str) else ""
    if not _TIN.fullmatch(text):
        raise DomainError("tin must be 6 to 15 digits.", "tin", "invalid_tin")
    return text


def mask_tin(tin: str | None) -> str | None:
    """Contract form (``^\\*{4,}[0-9]{2,4}$``): all but the last three digits masked."""
    if not tin:
        return None
    return "*" * max(4, len(tin) - 3) + tin[-3:]


def validate_taxpayer_type(value: object) -> str:
    if value not in TAXPAYER_TYPES:
        raise DomainError("taxpayer_type is not supported.", "taxpayer_type", "invalid_choice")
    return str(value)


# VAT figures -----------------------------------------------------------------


def net_vat(output_vat: Decimal, input_vat: Decimal) -> Decimal:
    """Declared output VAT minus declared input VAT; negative is a refund position.

    Arithmetic on the taxpayer's own figures only, not an assessment.
    """
    return output_vat - input_vat


def refundable_position(output_vat: Decimal, input_vat: Decimal) -> Decimal:
    return max(-net_vat(output_vat, input_vat), Decimal("0.00"))


def validate_refund_against_return(
    claimed: Decimal, output_vat: Decimal, input_vat: Decimal
) -> None:
    """A claim cannot exceed the refund position declared in the linked VAT return."""
    position = refundable_position(output_vat, input_vat)
    if claimed > position:
        raise DomainError(
            "claimed_amount exceeds the refund position of the linked VAT return "
            f"(LSL {format_money(position)}).",
            "claimed_amount",
            "exceeds_declared_position",
        )


def validate_country(value: object) -> str:
    text = value.strip().upper() if isinstance(value, str) else ""
    if not _COUNTRY.fullmatch(text):
        raise DomainError(
            "supplier_country must be an ISO 3166-1 alpha-2 code.",
            "supplier_country",
            "invalid_country",
        )
    if text == HOME_COUNTRY:
        raise DomainError(
            "An imported service needs a supplier outside Lesotho.",
            "supplier_country",
            "not_imported",
        )
    return text


# Status and write rules ------------------------------------------------------


def check_transition(before: str, after: str, actor: str, source: str | None = None) -> None:
    """Raise ``TransitionDenied`` unless ``actor`` may move ``before`` -> ``after``.

    An RSL-sourced claim (``source`` in ``RSL_SOURCES``) is sent by the service identity
    that read it from RSL, never by the taxpayer: the taxpayer filed it in RSL's system.
    """
    if before == after:
        return
    if source in RSL_SOURCES:
        allowed = RSL_INTAKE_TRANSITIONS.get((before, after)) or TRANSITIONS.get((before, after))
        if (before, after) in RSL_INTAKE_TRANSITIONS and actor == "taxpayer":
            raise TransitionDenied("This claim was filed with RSL; it is sent by RSL's intake.")
    else:
        allowed = TRANSITIONS.get((before, after))
    if allowed is None:
        raise TransitionDenied(f"A submission cannot move from {before} to {after}.")
    if actor != "operator" and actor not in allowed:
        raise TransitionDenied(f"Your role cannot move a submission from {before} to {after}.")


def simulator_enabled(conf: Mapping[str, Any]) -> bool:
    """Whether this site offers the fixture-only Online Refunds simulator (TAX-125)."""
    return conf.get(SIMULATOR_KEY) is True or conf.get(SIMULATOR_KEY) == 1


def referred_source(source: str | None, *, simulator: bool) -> bool:
    """TAX-131 (ADR-043 §1, §5): whether a send comes from a case RSL selected.

    A claim read from RSL's Online Refunds Application is. A portal, API or seeded send is
    only where the site runs the Online Refunds simulator, which stands in for RSL in
    fixtures and the demo. Nothing else opens a case or reaches Jube's rules.
    """
    return source in RSL_SOURCES or simulator


def check_send_source(source: str | None, *, simulator: bool) -> None:
    """Raise ``TransitionDenied`` for a send that RSL did not refer (see above)."""
    if not referred_source(source, simulator=simulator):
        raise TransitionDenied(NOT_REFERRED_MESSAGE)


def check_create(actor: str, source: str | None, submission_type: str) -> None:
    """Raise ``TransitionDenied`` unless ``actor`` may create a submission from ``source``.

    Taxpayers create their own portal submissions. Only the service identity (or a site
    operator) records a claim filed in an RSL system, and only a refund claim.
    """
    if source in RSL_SOURCES:
        if submission_type != "refund_claim":
            raise TransitionDenied(
                "Only refund claims are read from the Online Refunds Application."
            )
        if actor not in ("service", "operator"):
            raise TransitionDenied("Only RSL's intake records a claim filed with RSL.")
        return
    if actor not in ("taxpayer", "operator"):
        raise TransitionDenied("Only the taxpayer creates a submission.")


def declaration_required(source: str | None) -> bool:
    """A portal send needs the taxpayer's declaration; an RSL claim was declared in RSL's system."""
    return source not in RSL_SOURCES


def check_writes(
    changed: Iterable[str],
    actor: str,
    writable: Mapping[str, frozenset[str]],
    server_fields: frozenset[str],
) -> None:
    """Raise ``WriteDenied`` if ``actor`` changed a field it may not change.

    ``writable`` maps an actor to the fields it may change; operators may change
    every field except server-set ones. Server-set fields are never client-written.
    """
    changed = set(changed)
    server = sorted(changed & server_fields)
    if server:
        raise WriteDenied("Set by the server: " + ", ".join(server))
    if actor == "operator":
        return
    denied = sorted(changed - writable.get(actor, frozenset()))
    if denied:
        raise WriteDenied("Your role cannot change: " + ", ".join(denied))


def normalise(value: object) -> object:
    """Comparable form of a field value across str/float/Decimal/date inputs."""
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float | int | Decimal):
        try:
            return Decimal(repr(value) if isinstance(value, float) else value).normalize()
        except InvalidOperation:
            return value
    if isinstance(value, datetime.datetime | datetime.date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        if _MONEY.fullmatch(text):
            return Decimal(text).normalize()
        if _DATELIKE.match(text):
            try:
                parsed = datetime.datetime.fromisoformat(text)
            except ValueError:
                return text
            return parsed.date().isoformat() if len(text) == 10 else parsed.isoformat()
        if text.startswith("["):
            try:
                return json.dumps(json.loads(text), sort_keys=True)
            except ValueError:
                return text
        return text
    if isinstance(value, list | dict):
        return json.dumps(value, sort_keys=True)
    return value
