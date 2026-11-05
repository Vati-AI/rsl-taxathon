"""Offline parsers for supplied payment-channel files (TAX-079).

Four adapter contracts, one per channel. Each takes the bytes of one supplied CSV file (the
original registered as TAX-032 evidence) and returns normalised transactions. Nothing here
opens a network connection, calls a provider API, reads credentials or scrapes a site: an
adapter only parses a file that a taxpayer, an officer or a statutory response supplied. A live
connector to a bank, mobile-money operator, payment network or wallet provider needs legal
authority and a separate reviewed change; it is not part of this contract.

| Channel | Format | Account column | Channel-specific columns |
|---|---|---|---|
| ``bank_statement`` | ``bank_statement_csv.v1`` | ``account_ref`` | ``transaction_type``, ``value_date`` |
| ``mobile_money`` | ``mobile_money_csv.v1`` | ``wallet_ref`` | ``transaction_type``, ``fee``, ``agent_ref`` |
| ``foreign_payment`` | ``foreign_payment_csv.v1`` | ``account_ref`` | instructed and settled amounts, supplied ``exchange_rate`` and ``rate_source``, ``counterparty_country``, ``intermediary_bank_ref`` |
| ``digital_wallet`` | ``digital_wallet_csv.v1`` | ``wallet_ref`` | ``asset_kind``, ``asset_code``, ``quantity``, supplied ``fiat_value`` and ``valuation_source`` |

Rules shared by every adapter:

- The header must equal the format's columns exactly; an unknown or missing column is refused.
  A column name with labelling vocabulary (``risk_score`` and similar) is refused as
  ``risk_label_not_allowed``. Text cells with labelling vocabulary are refused the same way
  (ADR-026 rule), so no label can travel from a supplied file into Vati records.
- Money is ``Decimal`` parsed from strings with the TAX-048 helper; floats never occur because
  CSV cells are strings and are never passed through ``float``. Currencies are ISO 4217 codes.
  An exchange rate is used only when the file supplies it (TAX-073 ``parse_rate`` /
  ``convert``); none is looked up or guessed.
- Payment method (cash deposit, cash-in at an agent, merchant payment, foreign transfer,
  virtual asset) is recorded as descriptive ``details``. No adapter derives a score, signal,
  label or interpretation from it.
- Errors carry the ADR-003 ``invalid_domain_input`` code, a field (``line N.column``) and a
  machine-readable reason. Messages name the line and column, never the cell content.

Standard library plus the TAX-048/TAX-073 pure helpers only, so ``scripts/dev.py check`` runs
the tests with any interpreter. Callers put the repository root and
``integration/followthemoney`` on ``sys.path`` (as the tests do).
"""

from __future__ import annotations

import csv
import datetime
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import scheme_graph as sg

from ai.tools.customs_reconciliation import CustomsDataError, convert, parse_rate
from ai.tools.refund_reconciliation import RefundDataError, money, parse_money

ADAPTER_VERSION = "1.0.0"
MEDIA_TYPE = "text/csv"
MAX_DOCUMENT_BYTES = 26_214_400  # ADR-011 upload limit
MAX_ROWS = 5_000
MAX_CELL = 500
INCOMPLETE_DATA = "incomplete_data"

_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_COUNTRY = re.compile(r"[a-z]{2}")
_ASSET_CODE = re.compile(r"[A-Z0-9]{2,10}")
_QUANTITY = re.compile(r"(0|[1-9][0-9]{0,17})(\.[0-9]{1,18})?")
_AMOUNT_DIGITS = 15
# Spreadsheet formula prefixes; a cell starting with one is refused (CSV injection).
_FORMULA_PREFIX = ("=", "+", "@", "\t", "\r")


class AdapterError(ValueError):
    """A supplied file is refused. ``code`` is the ADR-003 ``invalid_domain_input``; ``field``
    names the line and column and ``reason`` is machine-readable."""

    def __init__(self, message: str, *, field: str, reason: str) -> None:
        super().__init__(message)
        self.code = "invalid_domain_input"
        self.field = field
        self.reason = reason


@dataclass(frozen=True)
class Counterparty:
    name: str
    account_ref: str | None
    country: str | None = None


@dataclass
class ParsedTransaction:
    """One supplied line, normalised. ``amount``/``currency`` is ``None`` only when the file
    gives no ISO-currency value (a virtual asset without a supplied fiat value)."""

    line: int
    provider_reference: str
    account_ref: str
    date: str
    direction: str  # inflow | outflow, seen from the account holder
    amount: Decimal | None
    currency: str | None
    counterparty: Counterparty
    narration: str | None
    details: dict[str, Any] = field(default_factory=dict)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)

    def money(self) -> dict[str, str] | None:
        if self.amount is None or self.currency is None:
            return None
        return money(self.amount, self.currency)


class PaymentChannelAdapter(Protocol):
    """The adapter contract. Implementations are offline parsers of supplied files."""

    @property
    def channel(self) -> str: ...
    @property
    def adapter_id(self) -> str: ...
    @property
    def adapter_version(self) -> str: ...
    @property
    def document_format(self) -> str: ...
    @property
    def media_type(self) -> str: ...

    def parse(self, document: bytes) -> list[ParsedTransaction]: ...


# ---------------------------------------------------------------- cell helpers


def _fail(message: str, where: str, reason: str) -> AdapterError:
    return AdapterError(message, field=where, reason=reason)


class _Row:
    def __init__(self, line: int, cells: Mapping[str, str]) -> None:
        self.line = line
        self.cells = cells

    def at(self, column: str) -> str:
        return f"line {self.line}.{column}"

    def opt(self, column: str) -> str | None:
        value = self.cells[column].strip()
        if not value:
            return None
        if len(value) > MAX_CELL:
            raise _fail(f"{self.at(column)} is too long", self.at(column), "invalid")
        if value.startswith(_FORMULA_PREFIX):
            raise _fail(
                f"{self.at(column)} starts with a formula prefix", self.at(column), "unsafe_cell"
            )
        return value

    def req(self, column: str) -> str:
        value = self.opt(column)
        if value is None:
            raise _fail(f"{self.at(column)} is required", self.at(column), "missing_value")
        return value

    def text(self, column: str, *, required: bool = False) -> str | None:
        value = self.req(column) if required else self.opt(column)
        if value is not None and sg.labelling_terms(value):
            raise _fail(
                f"{self.at(column)} carries labelling vocabulary; the original stays in the "
                "evidence store for officer review",
                self.at(column),
                "risk_label_not_allowed",
            )
        return value

    def ref(self, column: str, *, required: bool = True) -> str | None:
        value = self.req(column) if required else self.opt(column)
        if value is not None and not _REF.fullmatch(value):
            raise _fail(f"{self.at(column)} is not a valid reference", self.at(column), "invalid")
        return value

    def choice(self, column: str, allowed: Mapping[str, str] | frozenset[str]) -> str:
        value = self.req(column)
        if value not in allowed:
            raise _fail(
                f"{self.at(column)} must be one of {sorted(allowed)}", self.at(column), "invalid"
            )
        return allowed[value] if isinstance(allowed, Mapping) else value

    def date(self, column: str, *, required: bool = True) -> str | None:
        value = self.req(column) if required else self.opt(column)
        if value is None:
            return None
        try:
            if not _DATE.fullmatch(value):
                raise ValueError
            datetime.date.fromisoformat(value)
        except ValueError:
            raise _fail(f"{self.at(column)} must be YYYY-MM-DD", self.at(column), "invalid_date")
        return value

    def amount(self, column: str, *, required: bool = True) -> Decimal | None:
        value = self.req(column) if required else self.opt(column)
        if value is None:
            return None
        if len(value.split(".")[0]) > _AMOUNT_DIGITS:
            raise _fail(f"{self.at(column)} is too large", self.at(column), "invalid_amount")
        try:
            return parse_money(value, field=self.at(column))
        except RefundDataError:
            raise _fail(
                f"{self.at(column)} must be a non-negative decimal string with at most 2 places",
                self.at(column),
                "invalid_amount",
            ) from None

    def currency(self, column: str, *, required: bool = True) -> str | None:
        value = self.req(column) if required else self.opt(column)
        if value is not None and not _CURRENCY.fullmatch(value):
            raise _fail(f"{self.at(column)} must be ISO 4217", self.at(column), "invalid_currency")
        return value

    def rate(self, column: str) -> Decimal | None:
        value = self.opt(column)
        if value is None:
            return None
        try:
            return parse_rate(value, field_name=self.at(column))
        except CustomsDataError:
            raise _fail(
                f"{self.at(column)} must be a positive decimal string with at most 6 places",
                self.at(column),
                "invalid_exchange_rate",
            ) from None

    def country(self, column: str) -> str | None:
        value = self.opt(column)
        if value is not None and not _COUNTRY.fullmatch(value):
            raise _fail(
                f"{self.at(column)} must be ISO 3166-1 alpha-2, lower case",
                self.at(column),
                "invalid",
            )
        return value


def read_rows(document: bytes, columns: Sequence[str]) -> list[_Row]:
    """Decode and split a supplied CSV with an exact header. Refuses non-UTF-8, empty files,
    oversized files, wrong headers, ragged rows and too many rows."""
    if not isinstance(document, bytes) or not document:
        raise _fail("The supplied document is empty", "document", "empty_document")
    if len(document) > MAX_DOCUMENT_BYTES:
        raise _fail("The supplied document is too large", "document", "document_too_large")
    try:
        text = document.decode("utf-8")
    except UnicodeDecodeError:
        raise _fail("The supplied document is not UTF-8", "document", "invalid_encoding")
    if text.startswith("﻿"):
        text = text[1:]
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        lines = list(reader)
    except csv.Error:
        raise _fail("The supplied document is not well-formed CSV", "document", "invalid_csv")
    if not lines:
        raise _fail("The supplied document has no header", "document", "empty_document")
    header = [c.strip() for c in lines[0]]
    for name in header:
        if name not in columns:
            words = set(re.findall(r"[a-z]+", name.lower()))
            labelling = words & (sg.FORBIDDEN_TERMS | sg.LABEL_FIELDS) or sg.labelling_terms(name)
            reason = "risk_label_not_allowed" if labelling else "unexpected_column"
            raise _fail("The header has a column this format does not accept", "header", reason)
    if header != list(columns):
        raise _fail("The header does not match the format's columns", "header", "invalid_header")
    body = [(i, row) for i, row in enumerate(lines[1:], start=2) if any(c.strip() for c in row)]
    if not body:
        raise _fail("The supplied document has no transactions", "document", "no_transactions")
    if len(body) > MAX_ROWS:
        raise _fail(f"At most {MAX_ROWS} rows are accepted", "document", "too_many_rows")
    rows = []
    for line, cells in body:
        if len(cells) != len(columns):
            raise _fail(f"line {line} has the wrong number of cells", f"line {line}", "ragged_row")
        rows.append(_Row(line, dict(zip(columns, cells))))
    seen: dict[str, int] = {}
    for row in rows:
        key = row.cells[columns[1]].strip()
        if key in seen:
            raise _fail(
                f"{row.at(columns[1])} repeats line {seen[key]}",
                row.at(columns[1]),
                "duplicate_transaction_ref",
            )
        seen[key] = row.line
    return rows


def _single_account(rows: list[ParsedTransaction]) -> None:
    accounts = {t.account_ref for t in rows}
    if len(accounts) != 1:
        raise _fail("A supplied file covers exactly one account", "document", "multiple_accounts")


# ---------------------------------------------------------------- adapters


@dataclass(frozen=True)
class BankStatementAdapter:
    channel: str = "bank_statement"
    adapter_id: str = "vati.payment_channel.bank_statement_csv"
    adapter_version: str = ADAPTER_VERSION
    document_format: str = "bank_statement_csv.v1"
    media_type: str = MEDIA_TYPE

    COLUMNS = (
        "account_ref",
        "transaction_ref",
        "booking_date",
        "value_date",
        "transaction_type",
        "direction",
        "amount",
        "currency",
        "counterparty_name",
        "counterparty_account_ref",
        "narration",
    )
    TYPES = frozenset(
        {
            "transfer",
            "card_payment",
            "cash_deposit",
            "cash_withdrawal",
            "cheque",
            "standing_order",
            "fee",
            "interest",
        }
    )
    DIRECTIONS = {"credit": "inflow", "debit": "outflow"}

    def parse(self, document: bytes) -> list[ParsedTransaction]:
        out = []
        for row in read_rows(document, self.COLUMNS):
            out.append(
                ParsedTransaction(
                    line=row.line,
                    provider_reference=str(row.ref("transaction_ref")),
                    account_ref=str(row.ref("account_ref")),
                    date=str(row.date("booking_date")),
                    direction=row.choice("direction", self.DIRECTIONS),
                    amount=row.amount("amount"),
                    currency=row.currency("currency"),
                    counterparty=Counterparty(
                        str(row.text("counterparty_name", required=True)),
                        row.ref("counterparty_account_ref", required=False),
                    ),
                    narration=row.text("narration"),
                    details={
                        "transaction_type": row.choice("transaction_type", self.TYPES),
                        "value_date": row.date("value_date", required=False),
                    },
                )
            )
        _single_account(out)
        return out


@dataclass(frozen=True)
class MobileMoneyAdapter:
    channel: str = "mobile_money"
    adapter_id: str = "vati.payment_channel.mobile_money_csv"
    adapter_version: str = ADAPTER_VERSION
    document_format: str = "mobile_money_csv.v1"
    media_type: str = MEDIA_TYPE

    COLUMNS = (
        "wallet_ref",
        "transaction_ref",
        "transaction_date",
        "transaction_type",
        "direction",
        "amount",
        "fee",
        "currency",
        "counterparty_name",
        "counterparty_wallet_ref",
        "agent_ref",
        "narration",
    )
    TYPES = frozenset(
        {
            "p2p_transfer",
            "merchant_payment",
            "bill_payment",
            "cash_in",
            "cash_out",
            "salary_payment",
        }
    )
    DIRECTIONS = {"received": "inflow", "sent": "outflow"}

    def parse(self, document: bytes) -> list[ParsedTransaction]:
        out = []
        for row in read_rows(document, self.COLUMNS):
            currency = str(row.currency("currency"))
            fee = row.amount("fee", required=False)
            out.append(
                ParsedTransaction(
                    line=row.line,
                    provider_reference=str(row.ref("transaction_ref")),
                    account_ref=str(row.ref("wallet_ref")),
                    date=str(row.date("transaction_date")),
                    direction=row.choice("direction", self.DIRECTIONS),
                    amount=row.amount("amount"),
                    currency=currency,
                    counterparty=Counterparty(
                        str(row.text("counterparty_name", required=True)),
                        row.ref("counterparty_wallet_ref", required=False),
                    ),
                    narration=row.text("narration"),
                    details={
                        "transaction_type": row.choice("transaction_type", self.TYPES),
                        "fee": money(fee, currency) if fee is not None else None,
                        "agent_ref": row.ref("agent_ref", required=False),
                    },
                )
            )
        _single_account(out)
        return out


@dataclass(frozen=True)
class ForeignPaymentAdapter:
    channel: str = "foreign_payment"
    adapter_id: str = "vati.payment_channel.foreign_payment_csv"
    adapter_version: str = ADAPTER_VERSION
    document_format: str = "foreign_payment_csv.v1"
    media_type: str = MEDIA_TYPE

    COLUMNS = (
        "account_ref",
        "transfer_ref",
        "instruction_date",
        "settlement_date",
        "direction",
        "instructed_amount",
        "instructed_currency",
        "settled_amount",
        "settled_currency",
        "exchange_rate",
        "rate_source",
        "counterparty_name",
        "counterparty_account_ref",
        "counterparty_country",
        "intermediary_bank_ref",
        "narration",
    )
    DIRECTIONS = {"incoming": "inflow", "outgoing": "outflow"}

    def parse(self, document: bytes) -> list[ParsedTransaction]:
        out = []
        for row in read_rows(document, self.COLUMNS):
            instructed = row.amount("instructed_amount")
            instructed_ccy = row.currency("instructed_currency")
            settled = row.amount("settled_amount", required=False)
            settled_ccy = row.currency("settled_currency", required=False)
            rate = row.rate("exchange_rate")
            rate_source = row.text("rate_source")
            if (settled is None) != (settled_ccy is None):
                raise _fail(
                    f"line {row.line} needs settled_amount and settled_currency together",
                    row.at("settled_amount"),
                    "incomplete_pair",
                )
            if (rate is None) != (rate_source is None):
                raise _fail(
                    f"line {row.line} needs exchange_rate and rate_source together",
                    row.at("exchange_rate"),
                    "incomplete_pair",
                )
            same_currency = settled_ccy is None or settled_ccy == instructed_ccy
            if rate is not None and same_currency:
                raise _fail(
                    f"line {row.line} gives an exchange rate without a currency change",
                    row.at("exchange_rate"),
                    "unexpected_exchange_rate",
                )
            assert instructed is not None and instructed_ccy is not None
            tx = ParsedTransaction(
                line=row.line,
                provider_reference=str(row.ref("transfer_ref")),
                account_ref=str(row.ref("account_ref")),
                date=str(row.date("instruction_date")),
                direction=row.choice("direction", self.DIRECTIONS),
                amount=instructed,
                currency=instructed_ccy,
                counterparty=Counterparty(
                    str(row.text("counterparty_name", required=True)),
                    row.ref("counterparty_account_ref", required=False),
                    row.country("counterparty_country"),
                ),
                narration=row.text("narration"),
            )
            conversion: dict[str, Any] | None = None
            if settled is not None and settled_ccy is not None:
                conversion = {
                    "instructed": money(instructed, instructed_ccy),
                    "settled": money(settled, settled_ccy),
                    "exchange_rate": str(rate) if rate is not None else None,
                    "rate_source": rate_source,
                    "check": "same_currency" if same_currency else "not_checked",
                }
                if not same_currency and rate is None:
                    # A currency change without a supplied rate: never guessed.
                    tx.gaps.append(
                        {
                            "code": "exchange_rate_not_supplied",
                            "interpretation": INCOMPLETE_DATA,
                            "currencies": [instructed_ccy, settled_ccy],
                        }
                    )
                elif rate is not None:
                    expected = convert(instructed, rate)
                    conversion["check"] = "consistent" if expected == settled else "inconsistent"
                    if expected != settled:
                        tx.contradictions.append(
                            {
                                "code": "booked_conversion_inconsistent",
                                "expected": money(expected, settled_ccy),
                                "booked": money(settled, settled_ccy),
                            }
                        )
            tx.details = {
                "settlement_date": row.date("settlement_date", required=False),
                "conversion": conversion,
                "intermediary_bank_ref": row.ref("intermediary_bank_ref", required=False),
            }
            out.append(tx)
        _single_account(out)
        return out


@dataclass(frozen=True)
class DigitalWalletAdapter:
    channel: str = "digital_wallet"
    adapter_id: str = "vati.payment_channel.digital_wallet_csv"
    adapter_version: str = ADAPTER_VERSION
    document_format: str = "digital_wallet_csv.v1"
    media_type: str = MEDIA_TYPE

    COLUMNS = (
        "wallet_ref",
        "transaction_ref",
        "transaction_date",
        "direction",
        "asset_kind",
        "asset_code",
        "quantity",
        "fiat_value",
        "fiat_currency",
        "valuation_source",
        "counterparty_name",
        "counterparty_wallet_ref",
        "narration",
    )
    DIRECTIONS = {"in": "inflow", "out": "outflow"}
    KINDS = frozenset({"fiat_balance", "virtual_asset"})

    def parse(self, document: bytes) -> list[ParsedTransaction]:
        out = []
        for row in read_rows(document, self.COLUMNS):
            kind = row.choice("asset_kind", self.KINDS)
            code = row.req("asset_code")
            quantity = row.req("quantity")
            fiat = row.amount("fiat_value", required=False)
            fiat_ccy = row.currency("fiat_currency", required=False)
            source = row.text("valuation_source")
            amount: Decimal | None
            currency: str | None
            gaps: list[dict[str, Any]] = []
            if kind == "fiat_balance":
                if not _CURRENCY.fullmatch(code):
                    raise _fail(
                        f"{row.at('asset_code')} must be ISO 4217 for a fiat balance",
                        row.at("asset_code"),
                        "invalid_currency",
                    )
                if fiat is not None or fiat_ccy is not None or source is not None:
                    raise _fail(
                        f"line {row.line} values a fiat balance a second time",
                        row.at("fiat_value"),
                        "unexpected_valuation",
                    )
                amount, currency = row.amount("quantity"), code
                valuation = None
            else:
                if not _ASSET_CODE.fullmatch(code):
                    raise _fail(
                        f"{row.at('asset_code')} is invalid", row.at("asset_code"), "invalid"
                    )
                if not _QUANTITY.fullmatch(quantity) or Decimal(quantity) == 0:
                    raise _fail(
                        f"{row.at('quantity')} must be a positive decimal string",
                        row.at("quantity"),
                        "invalid_quantity",
                    )
                supplied = [fiat is not None, fiat_ccy is not None, source is not None]
                if any(supplied) and not all(supplied):
                    raise _fail(
                        f"line {row.line} needs fiat_value, fiat_currency and valuation_source "
                        "together",
                        row.at("fiat_value"),
                        "incomplete_pair",
                    )
                amount, currency = fiat, fiat_ccy
                valuation = (
                    {"fiat_value": money(fiat, fiat_ccy), "valuation_source": source}
                    if fiat is not None and fiat_ccy is not None
                    else None
                )
                if valuation is None:
                    # No valuation was supplied: the quantity is kept, no price is looked up.
                    gaps.append(
                        {
                            "code": "fiat_value_not_supplied",
                            "interpretation": INCOMPLETE_DATA,
                            "asset_code": code,
                        }
                    )
            out.append(
                ParsedTransaction(
                    line=row.line,
                    provider_reference=str(row.ref("transaction_ref")),
                    account_ref=str(row.ref("wallet_ref")),
                    date=str(row.date("transaction_date")),
                    direction=row.choice("direction", self.DIRECTIONS),
                    amount=amount,
                    currency=currency,
                    counterparty=Counterparty(
                        str(row.text("counterparty_name", required=True)),
                        row.ref("counterparty_wallet_ref", required=False),
                    ),
                    narration=row.text("narration"),
                    details={
                        "asset_kind": kind,
                        "asset_code": code,
                        "quantity": quantity if kind == "virtual_asset" else None,
                        "valuation": valuation,
                    },
                    gaps=gaps,
                )
            )
        _single_account(out)
        return out


ADAPTERS: dict[str, PaymentChannelAdapter] = {
    a.channel: a
    for a in (
        BankStatementAdapter(),
        MobileMoneyAdapter(),
        ForeignPaymentAdapter(),
        DigitalWalletAdapter(),
    )
}
