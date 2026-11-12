"""Checks on an evidence response before it is sent (TAX-126).

Pure functions with no Frappe import. ``vati_tax.responses.store.review`` gathers
the facts (the request's items, the claim period, each uploaded document's
latest TAX-038 Docling extraction) and this module turns them into guidance per
answered item:

- **legibility**: whether the document could be read at all, or only partly, or is
  still being read;
- **period**: whether any date in the document falls in the period the item is
  about (the requested line's invoice month for a TAX-127 line item, otherwise
  the claim's VAT period);
- **amount**: for a requested line, whether the line's VAT amount appears in the
  document;
- **invoice number**: for a requested line, whether its invoice number appears.

Guidance is advisory and never blocks sending: the response endpoint does not
call this module. Wording is neutral and comes from a fixed list of codes; the
document's text is read as data and never repeated back except the dates and
amounts the checks found. TAX-078 reminders whose topic covers a check are
marked on it, as TAX-044 does for claim prompts.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

from vati_tax.responses.rules import RequestedItem

CHECKS_VERSION: Final = "tax126-response-checks-v1"

Level = Literal["info", "attention"]
ExtractionState = Literal["pending", "success", "partial", "failure"]

# Check code -> TAX-078 reminder topic that covers the same kind of gap.
REMINDER_TOPICS: Final[dict[str, str]] = {
    "document_period_differs": "period_figures",
    "document_amount_not_found": "invoice_totals",
    "document_invoice_number_not_found": "missing_invoices",
}
CODES: Final = frozenset(
    {
        "document_still_being_read",
        "document_not_readable",
        "document_partly_read",
        "document_period_differs",
        "document_amount_not_found",
        "document_invoice_number_not_found",
        "document_matches_request",
    }
)
MAX_SHOWN: Final = 3  # dates or amounts quoted back per document

_MONTHS: Final = {
    name: index
    for index, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
_MONTH_NAMES: Final = (
    "January February March April May June July August September October November December"
).split()
_MONTH_RE: Final = "|".join(sorted(_MONTHS, key=len, reverse=True))
_YEAR: Final = r"(19[9][0-9]|20[0-9]{2})"
_ISO_DATE: Final = re.compile(rf"\b{_YEAR}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])\b")
# Day first, as dates are written in Lesotho: 05/03/2025, 5-3-2025, 05.03.2025.
_DMY_DATE: Final = re.compile(rf"\b(0?[1-9]|[12][0-9]|3[01])[/.-](0?[1-9]|1[0-2])[/.-]{_YEAR}\b")
_TEXT_DATE: Final = re.compile(
    rf"\b(?:(0?[1-9]|[12][0-9]|3[01])\s+)?({_MONTH_RE})\.?,?\s+{_YEAR}\b", re.IGNORECASE
)
# Amounts with cents: 1,500.00, 1 500.00 or 1500.00 (optionally after M, LSL, R or ZAR).
_AMOUNT: Final = re.compile(r"(?<![\d.,])(\d{1,3}(?:[, ]\d{3})+|\d+)\.(\d{2})(?![\d])")
_ALNUM: Final = re.compile(r"[^0-9a-z]")
_PERIOD: Final = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])")


@dataclass(frozen=True)
class DocumentFacts:
    """What the latest extraction of one uploaded document says, as data."""

    evidence_id: str
    name: str
    extraction: ExtractionState
    texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Found:
    months: frozenset[tuple[int, int]]
    dates: tuple[datetime.date | tuple[int, int], ...]
    amounts: frozenset[Decimal]
    shown_amounts: tuple[Decimal, ...]
    compact_text: str


def _scan(texts: Iterable[str]) -> Found:
    dates: list[datetime.date | tuple[int, int]] = []
    months: set[tuple[int, int]] = set()
    amounts: list[Decimal] = []
    compact: list[str] = []
    for text in texts:
        for m in _ISO_DATE.finditer(text):
            _add_date(dates, months, int(m[1]), int(m[2]), int(m[3]))
        for m in _DMY_DATE.finditer(text):
            _add_date(dates, months, int(m[3]), int(m[2]), int(m[1]))
        for m in _TEXT_DATE.finditer(text):
            month = _MONTHS[m[2].lower()]
            if m[1]:
                _add_date(dates, months, int(m[3]), month, int(m[1]))
            elif (int(m[3]), month) not in months:
                months.add((int(m[3]), month))
                dates.append((int(m[3]), month))
        for m in _AMOUNT.finditer(text):
            try:
                amounts.append(Decimal(re.sub(r"[, ]", "", m[1]) + "." + m[2]))
                if " " in m[1]:  # "2 100.00" may be a quantity then 100.00: keep both readings
                    amounts.append(Decimal(m[1].rsplit(" ", 1)[1] + "." + m[2]))
            except InvalidOperation:  # pragma: no cover - the pattern only admits digits
                continue
        compact.append(_ALNUM.sub("", text.lower()))
    unique = list(dict.fromkeys(amounts))
    return Found(
        frozenset(months), tuple(dates), frozenset(unique), tuple(unique), "".join(compact)
    )


def _add_date(
    dates: list[datetime.date | tuple[int, int]],
    months: set[tuple[int, int]],
    year: int,
    month: int,
    day: int,
) -> None:
    try:
        value = datetime.date(year, month, day)
    except ValueError:
        return
    if value not in dates:
        dates.append(value)
        months.add((year, month))


def period_of(value: object) -> tuple[int, int] | None:
    """``(year, month)`` from a ``YYYY-MM`` period or an ISO date."""
    if not isinstance(value, str):
        return None
    m = _PERIOD.match(value.strip())
    return (int(m[1]), int(m[2])) if m else None


def period_label(period: tuple[int, int]) -> str:
    return f"{_MONTH_NAMES[period[1] - 1]} {period[0]}"


def _date_label(value: datetime.date | tuple[int, int]) -> str:
    if isinstance(value, datetime.date):
        return f"{value.day} {_MONTH_NAMES[value.month - 1][:3]} {value.year}"
    return period_label(value)


def _money(value: Decimal) -> str:
    return f"M {value:,.2f}"


def _parse_amount(value: object) -> Decimal | None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(\.[0-9]{1,2})?", value):
        return None
    return Decimal(value).quantize(Decimal("0.01"))


def _check(
    code: str, level: Level, message: str, doc: DocumentFacts | None = None
) -> dict[str, Any]:
    assert code in CODES
    out: dict[str, Any] = {"code": code, "level": level, "message": message}
    if doc is not None:
        out["evidence_id"] = doc.evidence_id
        out["document"] = doc.name
    return out


def check_document(
    doc: DocumentFacts,
    *,
    period: tuple[int, int] | None,
    period_source: str,
    vat_amount: Decimal | None,
    invoice_number: str | None,
) -> list[dict[str, Any]]:
    """Guidance for one uploaded document against what its item asks for."""
    if doc.extraction == "pending":
        return [
            _check(
                "document_still_being_read",
                "info",
                f"{doc.name} is still being read, so it has not been checked yet. You can "
                "send now; the officer will see the document either way.",
                doc,
            )
        ]
    texts = tuple(t for t in doc.texts if t and t.strip())
    if doc.extraction == "failure" or not texts:
        return [
            _check(
                "document_not_readable",
                "attention",
                f"We could not read any text in {doc.name}. Check that it is a clear, complete "
                "copy (not blank, cropped or a photo at an angle). If it is, you can still "
                "send it.",
                doc,
            )
        ]
    out: list[dict[str, Any]] = []
    if doc.extraction == "partial":
        out.append(
            _check(
                "document_partly_read",
                "info",
                f"Only part of {doc.name} could be read, so the checks below may miss "
                "something. A clearer copy helps the officer.",
                doc,
            )
        )
    found = _scan(texts)
    if period is not None and found.months and period not in found.months:
        shown = ", ".join(_date_label(d) for d in found.dates[:MAX_SHOWN])
        out.append(
            _check(
                "document_period_differs",
                "attention",
                f"The dates in {doc.name} ({shown}) are not in {period_label(period)}, "
                f"{period_source}. Check that this is the document for that period.",
                doc,
            )
        )
    if vat_amount is not None and found.amounts and vat_amount not in found.amounts:
        out.append(
            _check(
                "document_amount_not_found",
                "attention",
                f"We could not find the VAT amount {_money(vat_amount)} from the requested "
                f"invoice line in {doc.name}. Check that this is the invoice for that line.",
                doc,
            )
        )
    wanted = _ALNUM.sub("", (invoice_number or "").lower())
    if wanted and wanted not in found.compact_text:
        out.append(
            _check(
                "document_invoice_number_not_found",
                "attention",
                f"We could not find invoice number {invoice_number} in {doc.name}. Check "
                "that this is the invoice that was requested.",
                doc,
            )
        )
    if not [c for c in out if c["level"] == "attention"]:
        checked = [
            what
            for what, ran in (
                ("the period", period is not None and bool(found.months)),
                ("the VAT amount", vat_amount is not None and bool(found.amounts)),
                ("the invoice number", bool(wanted)),
            )
            if ran
        ]
        what = (
            "We found nothing to point out"
            if not checked
            else "It matches the request on " + _join(checked)
        )
        out.append(_check("document_matches_request", "info", f"{doc.name} was read. {what}.", doc))
    return out


def _join(parts: Sequence[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def review(
    items: Sequence[RequestedItem],
    answers: Iterable[Mapping[str, Any]],
    documents: Mapping[str, DocumentFacts],
    *,
    claim_period: str | None,
    reminders: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Guidance per answered item. Items answered with a reason get none."""
    by_index = {item.index: item for item in items}
    topics = {r["topic"]: r["reminder_id"] for r in reminders if "topic" in r}
    claim = period_of(claim_period)
    out_items: list[dict[str, Any]] = []
    for answer in answers:
        index = answer.get("item_index")
        item = by_index.get(index) if isinstance(index, int) else None
        ids = [e for e in answer.get("evidence_ids") or [] if isinstance(e, str)]
        if item is None or not ids:
            continue
        line = item.line or {}
        line_period = period_of(line.get("invoice_date"))
        period = line_period or claim
        source = (
            "the month of the requested invoice"
            if line_period
            else "the period of your claim"
        )  # fmt: skip
        checks: list[dict[str, Any]] = []
        for evidence_id in ids:
            doc = documents.get(evidence_id)
            if doc is None:
                continue
            checks.extend(
                check_document(
                    doc,
                    period=period,
                    period_source=source,
                    vat_amount=_parse_amount(line.get("vat_amount")),
                    invoice_number=str(line["invoice_number"])
                    if line.get("invoice_number")
                    else None,
                )
            )
        for check in checks:
            check["reminder_id"] = topics.get(REMINDER_TOPICS.get(check["code"], ""))
        out_items.append(
            {
                "index": item.index,
                "attention": sum(c["level"] == "attention" for c in checks),
                "checks": checks,
            }
        )
    return {
        "checks_version": CHECKS_VERSION,
        "items": out_items,
        "blocks_sending": False,
    }
