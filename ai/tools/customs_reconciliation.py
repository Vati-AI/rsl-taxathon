"""Deterministic tax/customs multi-value reconciliation (TAX-073).

One imported transaction can legitimately carry several values: the supplier
invoice, the customs value, the accounting entry, the payment(s) and, for
related parties, a transfer-pricing value. This module maps those values side
by side with their currency and date context and compares each basis with an
anchor basis. It never merges different bases into one number.

Rules (prototype choices documented here, not RSL or customs policy):

- Amounts are ``decimal.Decimal`` parsed from strings via the TAX-048 helpers;
  floats are rejected. Exchange rates are positive decimal strings with at most
  six places. Conversion to the reporting currency is ``amount * rate`` rounded
  half-up to cents, using only an exchange-rate record supplied with the case
  (cited by ``rate_id`` or dated exactly on the value's date). No rate is ever
  guessed; without one the value is not comparable and a gap is recorded.
- Two bases are compared in their shared transaction currency when they have
  one, so later payment-date exchange movements do not look like value
  differences; otherwise both reporting-currency conversions are compared.
- Cited adjustment records (freight, insurance, discounts...) may bridge two
  bases. A residual within the absolute tolerance (default 1.00 in the compared
  currency) counts as rounding.
- A residual between bases that are legitimately different (transfer pricing,
  or customs against a value on another or unknown valuation basis) is a
  ``basis_difference_unreconciled`` gap that needs bridging evidence, not a
  discrepancy. Payments short of the anchor are a ``partially_evidenced`` gap.
- A missing source is a gap with ``interpretation: incomplete_data``. Only
  contradictions between supplied records (same-reference copies with different
  amounts, conflicting rates, inconsistent booked conversions, cited rates for
  the wrong currency, unexplained differences on the same basis) are
  discrepancies, and those remain items for officer review.

The output is advisory evidence with cited record IDs. It is never a decision,
assessment or finding of misdeclaration; no model is consulted for numbers.
"""

import datetime
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from ai.tools.refund_reconciliation import (
    CENT,
    DEFAULT_CURRENCY,
    RefundDataError,
    money,
    parse_money,
)

CALCULATION_VERSION = "tax073-customs-values-v1"
DEFAULT_TOLERANCE = Decimal("1.00")
# Anchor preference: the first present basis is compared with every other one.
BASES = (
    "supplier_invoice",
    "accounting_value",
    "payment",
    "customs_value",
    "transfer_pricing_value",
)
# Bases whose absence is reported as a gap; transfer pricing applies only to
# related-party transactions and is optional.
EXPECTED_BASES = ("supplier_invoice", "customs_value", "accounting_value", "payment")
ADJUSTMENT_EFFECTS = {"add": Decimal("1"), "subtract": Decimal("-1")}
INCOMPLETE_DATA = "incomplete_data"
NOTICE = (
    "Advisory reconciliation of supplied synthetic records. Differences between "
    "legitimate value bases and missing records are not findings of misdeclaration; "
    "an officer reviews every item."
)

_CURRENCY = re.compile(r"[A-Z]{3}")
_RATE = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]{1,6})?")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class CustomsDataError(RefundDataError):
    """Customs records cannot be reconciled safely; carries a stable error code."""


@dataclass(frozen=True)
class _Rate:
    id: str
    from_currency: str
    rate: Decimal
    date: str
    source: str | None


@dataclass
class _Value:
    id: str
    basis: str
    amount: Decimal
    currency: str
    date: str
    txn_amount: Decimal
    txn_currency: str
    reporting: Decimal | None
    rate: _Rate | None
    reference: str | None
    valuation_basis: str | None
    source: str | None
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class _Basis:
    basis: str
    values: list[_Value]
    contradicted: bool
    txn_amount: Decimal | None
    txn_currency: str | None
    reporting: Decimal | None
    valuation_bases: list[str]
    evidence_ids: list[str]


def _text(record: Mapping[str, object], key: str, *, field_name: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CustomsDataError("invalid_record", f"{field_name}.{key} must be a non-empty string")
    return value


def _optional_text(record: Mapping[str, object], key: str, *, field_name: str) -> str | None:
    if record.get(key) is None:
        return None
    return _text(record, key, field_name=field_name)


def _items(record: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    value = record.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise CustomsDataError("invalid_record", f"{key} must be a list of objects")
    return value


def _currency(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not _CURRENCY.fullmatch(value):
        raise CustomsDataError("invalid_currency", f"{field_name} must be an ISO 4217 code")
    return value


def _date(value: object, *, field_name: str) -> str:
    if isinstance(value, str) and _DATE.fullmatch(value):
        try:
            datetime.date.fromisoformat(value)
            return value
        except ValueError:
            pass
    raise CustomsDataError("invalid_date", f"{field_name} must be an ISO date YYYY-MM-DD")


def _amount(value: object, *, field_name: str) -> Decimal:
    try:
        return parse_money(value, field=field_name)
    except RefundDataError as error:
        raise CustomsDataError(error.code, str(error)) from None


def parse_rate(value: object, *, field_name: str) -> Decimal:
    """Parse a positive exchange rate string with at most six decimal places."""
    if not isinstance(value, str) or not _RATE.fullmatch(value) or Decimal(value) == 0:
        raise CustomsDataError(
            "invalid_exchange_rate",
            f"{field_name} must be a positive decimal string with at most 6 places",
        )
    return Decimal(value)


def convert(amount: Decimal, rate: Decimal) -> Decimal:
    """Convert to the reporting currency, rounding half-up to cents."""
    return (amount * rate).quantize(CENT, rounding=ROUND_HALF_UP)


class _Reconciler:
    def __init__(self, record: Mapping[str, object], reporting: str, tolerance: Decimal) -> None:
        self.record = record
        self.reporting = reporting
        self.tolerance = tolerance
        self.gaps: list[dict[str, object]] = []
        self.discrepancies: list[dict[str, object]] = []
        self.context: list[dict[str, object]] = []
        self.seen_ids: set[str] = set()
        self.rates: dict[str, _Rate] = {}
        self.statuses: dict[str, int] = {}

    def _claim_id(self, item: Mapping[str, object], kind: str) -> str:
        item_id = _text(item, "id", field_name=kind)
        if item_id in self.seen_ids:
            raise CustomsDataError("duplicate_record_id", f"Duplicate record id {item_id}")
        self.seen_ids.add(item_id)
        return item_id

    def _gap(self, code: str, txn_id: str, evidence: Sequence[str], **extra: object) -> None:
        self.gaps.append(
            {
                "code": code,
                "transaction_id": txn_id,
                "interpretation": INCOMPLETE_DATA,
                "evidence_ids": sorted(set(evidence)),
                **extra,
            }
        )

    def _discrepancy(
        self, code: str, txn_id: str, evidence: Sequence[str], **extra: object
    ) -> None:
        self.discrepancies.append(
            {
                "code": code,
                "transaction_id": txn_id,
                "requires_officer_review": True,
                "evidence_ids": sorted(set(evidence)),
                **extra,
            }
        )

    def load_rates(self) -> None:
        for item in _items(self.record, "exchange_rates"):
            rate_id = self._claim_id(item, "exchange_rate")
            from_currency = _currency(item.get("from_currency"), field_name=f"{rate_id}.from")
            to_currency = _currency(item.get("to_currency"), field_name=f"{rate_id}.to")
            if to_currency != self.reporting or from_currency == to_currency:
                raise CustomsDataError(
                    "unsupported_exchange_rate",
                    f"{rate_id} must convert a foreign currency into {self.reporting}",
                )
            self.rates[rate_id] = _Rate(
                id=rate_id,
                from_currency=from_currency,
                rate=parse_rate(item.get("rate"), field_name=f"{rate_id}.rate"),
                date=_date(item.get("date"), field_name=f"{rate_id}.date"),
                source=_optional_text(item, "source", field_name=rate_id),
            )

    def _resolve_rate(
        self,
        item_id: str,
        currency: str,
        date: str,
        rate_id: str | None,
        txn_id: str,
        *,
        optional: bool = False,
    ) -> _Rate | None:
        """Find the supplied rate for ``currency`` -> reporting; record a gap when absent.

        ``optional`` (adjustments) skips the missing-rate gap: an adjustment only
        needs a rate if it must be bridged in the reporting currency, and that
        case is reported as ``adjustment_not_comparable`` instead.
        """
        if rate_id is not None:
            rate = self.rates.get(rate_id)
            if rate is None:
                self._gap("exchange_rate_missing", txn_id, [item_id], cited_rate_id=rate_id)
                return None
            if rate.from_currency != currency:
                self._discrepancy("exchange_rate_currency_mismatch", txn_id, [item_id, rate_id])
                return None
            if rate.date != date:
                self.context.append(
                    {
                        "code": "exchange_rate_date_differs_from_value_date",
                        "transaction_id": txn_id,
                        "value_date": date,
                        "rate_date": rate.date,
                        "evidence_ids": [item_id, rate_id],
                    }
                )
            return rate
        candidates = [
            r for r in self.rates.values() if r.from_currency == currency and r.date == date
        ]
        if not candidates:
            if optional:
                return None
            self._gap("exchange_rate_missing", txn_id, [item_id], currency=currency, date=date)
            return None
        if len({r.rate for r in candidates}) > 1:
            ids = sorted(r.id for r in candidates)
            self._discrepancy("conflicting_exchange_rates", txn_id, [item_id, *ids])
            return None
        return min(candidates, key=lambda r: r.id)

    def _value(self, item: Mapping[str, object], txn_id: str, kind: str) -> _Value:
        value_id = self._claim_id(item, kind)
        basis = _text(item, "basis", field_name=value_id) if kind == "value" else "adjustment"
        if kind == "value" and basis not in BASES:
            raise CustomsDataError("invalid_basis", f"{value_id}.basis is not a supported basis")
        amount = _amount(item.get("amount"), field_name=f"{value_id}.amount")
        currency = _currency(item.get("currency"), field_name=f"{value_id}.currency")
        date = _date(item.get("date"), field_name=f"{value_id}.date")
        rate_id = _optional_text(item, "rate_id", field_name=value_id)
        txn_amount, txn_currency = amount, currency
        rate: _Rate | None = None
        reporting: Decimal | None = amount if currency == self.reporting else None
        evidence = [value_id]

        if item.get("original_amount") is not None:
            # A reporting-currency booking of a foreign amount at a cited rate.
            txn_amount = _amount(item.get("original_amount"), field_name=f"{value_id}.original")
            txn_currency = _currency(
                item.get("original_currency"), field_name=f"{value_id}.original_currency"
            )
            if currency != self.reporting or txn_currency == currency:
                raise CustomsDataError(
                    "invalid_record",
                    f"{value_id} original amounts must be booked in {self.reporting}",
                )
            rate = self._resolve_rate(value_id, txn_currency, date, rate_id, txn_id)
            if rate is not None:
                evidence.append(rate.id)
                if convert(txn_amount, rate.rate) != amount:
                    self._discrepancy(
                        "booked_conversion_inconsistent",
                        txn_id,
                        evidence,
                        expected=money(convert(txn_amount, rate.rate), self.reporting),
                        booked=money(amount, self.reporting),
                    )
        elif currency != self.reporting:
            rate = self._resolve_rate(
                value_id, currency, date, rate_id, txn_id, optional=kind == "adjustment"
            )
            if rate is not None:
                reporting = convert(amount, rate.rate)
                evidence.append(rate.id)

        return _Value(
            id=value_id,
            basis=basis,
            amount=amount,
            currency=currency,
            date=date,
            txn_amount=txn_amount,
            txn_currency=txn_currency,
            reporting=reporting,
            rate=rate,
            reference=_optional_text(item, "reference", field_name=value_id),
            valuation_basis=_optional_text(item, "valuation_basis", field_name=value_id),
            source=_optional_text(item, "source", field_name=value_id),
            evidence_ids=evidence,
        )

    def _basis(self, basis: str, values: list[_Value], txn_id: str) -> _Basis:
        """Total one basis, counting same-reference copies once and flagging conflicts."""
        counted: list[_Value] = []
        contradicted = False
        by_reference: dict[str, list[_Value]] = {}
        for value in values:
            if value.reference is None:
                counted.append(value)
            else:
                by_reference.setdefault(value.reference, []).append(value)
        for reference, copies in sorted(by_reference.items()):
            ids = [v.id for v in copies]
            if len({(v.txn_amount, v.txn_currency) for v in copies}) > 1:
                contradicted = True
                self._discrepancy(
                    "conflicting_values_for_same_reference",
                    txn_id,
                    ids,
                    basis=basis,
                    reference=reference,
                )
            elif len(copies) > 1:
                self.context.append(
                    {
                        "code": "duplicate_reference_counted_once",
                        "transaction_id": txn_id,
                        "basis": basis,
                        "reference": reference,
                        "evidence_ids": sorted(ids),
                    }
                )
            counted.append(copies[0])
        currencies = {v.txn_currency for v in counted}
        single = len(currencies) == 1 and not contradicted
        reportings = [v.reporting for v in counted]
        return _Basis(
            basis=basis,
            values=values,
            contradicted=contradicted,
            txn_amount=sum((v.txn_amount for v in counted), Decimal("0.00")) if single else None,
            txn_currency=next(iter(currencies)) if single else None,
            reporting=(
                sum((r for r in reportings if r is not None), Decimal("0.00"))
                if not contradicted and all(r is not None for r in reportings)
                else None
            ),
            valuation_bases=sorted({v.valuation_basis for v in values if v.valuation_basis}),
            evidence_ids=sorted({e for v in values for e in v.evidence_ids}),
        )

    def _adjustments(
        self,
        anchor: _Basis,
        other: _Basis,
        adjustments: list[tuple[_Value, str, str, Decimal]],
        currency: str,
        txn_id: str,
    ) -> tuple[Decimal, list[str]]:
        """Signed total of adjustments bridging anchor -> other in ``currency``."""
        total = Decimal("0.00")
        used: list[str] = []
        for adj, from_basis, to_basis, sign in adjustments:
            if (from_basis, to_basis) == (anchor.basis, other.basis):
                direction = sign
            elif (from_basis, to_basis) == (other.basis, anchor.basis):
                direction = -sign
            else:
                continue
            if adj.txn_currency == currency:
                amount: Decimal | None = adj.txn_amount
            elif currency == self.reporting:
                amount = adj.reporting
            else:
                amount = None
            if amount is None:
                self._gap("adjustment_not_comparable", txn_id, adj.evidence_ids)
                continue
            total += direction * amount
            used.extend(adj.evidence_ids)
        return total, sorted(set(used))

    def _compare(
        self,
        anchor: _Basis,
        other: _Basis,
        adjustments: list[tuple[_Value, str, str, Decimal]],
        txn_id: str,
    ) -> dict[str, object]:
        evidence = sorted({*anchor.evidence_ids, *other.evidence_ids})
        result: dict[str, object] = {
            "anchor_basis": anchor.basis,
            "basis": other.basis,
            "compared_in": None,
            "difference": None,
            "adjustments_total": None,
            "residual": None,
            "tolerance": None,
            "reporting_difference": None,
            "adjustment_ids": [],
            "evidence_ids": evidence,
        }
        if anchor.contradicted or other.contradicted:
            return {**result, "status": "not_comparable", "reason": "conflicting_source_values"}
        if anchor.reporting is not None and other.reporting is not None:
            result["reporting_difference"] = money(
                other.reporting - anchor.reporting, self.reporting
            )
        if anchor.txn_currency is not None and anchor.txn_currency == other.txn_currency:
            currency = anchor.txn_currency
            assert anchor.txn_amount is not None and other.txn_amount is not None
            difference = other.txn_amount - anchor.txn_amount
        elif anchor.reporting is not None and other.reporting is not None:
            currency = self.reporting
            difference = other.reporting - anchor.reporting
        else:
            self._gap("values_not_comparable", txn_id, evidence, bases=[anchor.basis, other.basis])
            return {**result, "status": "not_comparable", "reason": "no_common_currency_or_rate"}

        adjusted, used = self._adjustments(anchor, other, adjustments, currency, txn_id)
        residual = difference - adjusted
        evidence = sorted({*evidence, *used})
        result.update(
            evidence_ids=evidence,
            compared_in=currency,
            difference=money(difference, currency),
            adjustments_total=money(adjusted, currency),
            residual=money(residual, currency),
            tolerance=money(self.tolerance, currency),
            adjustment_ids=used,
        )
        if abs(residual) <= self.tolerance:
            status = "matched_with_adjustments" if used else "matched"
            # Equal foreign amounts converted on different dates: a legitimate
            # exchange-rate timing effect, recorded as context only.
            if (
                currency != self.reporting
                and not used
                and anchor.reporting is not None
                and other.reporting is not None
                and abs(other.reporting - anchor.reporting) > self.tolerance
            ):
                self.context.append(
                    {
                        "code": "exchange_rate_timing_difference",
                        "transaction_id": txn_id,
                        "bases": [anchor.basis, other.basis],
                        "reporting_difference": money(
                            other.reporting - anchor.reporting, self.reporting
                        ),
                        "evidence_ids": evidence,
                    }
                )
            return {**result, "status": status}

        customs_pair = "customs_value" in (anchor.basis, other.basis)
        valuation_known = bool(anchor.valuation_bases) and bool(other.valuation_bases)
        if "transfer_pricing_value" in (anchor.basis, other.basis):
            status, reason = "basis_difference_unreconciled", "transfer_pricing_basis"
        elif customs_pair and not valuation_known:
            status, reason = "basis_difference_unreconciled", "valuation_basis_missing"
        elif customs_pair and anchor.valuation_bases != other.valuation_bases:
            status, reason = "basis_difference_unreconciled", "different_valuation_basis"
        elif other.basis == "payment" and residual < 0:
            status, reason = "partially_evidenced", "payments_below_anchor_value"
        else:
            status, reason = "difference_unexplained", "same_basis_values_differ"
        if status == "difference_unexplained":
            self._discrepancy(
                "values_differ_beyond_tolerance",
                txn_id,
                evidence,
                bases=[anchor.basis, other.basis],
                residual=money(residual, currency),
            )
        else:
            self._gap(
                "bridging_evidence_needed"
                if status != "partially_evidenced"
                else "payment_evidence_incomplete",
                txn_id,
                evidence,
                bases=[anchor.basis, other.basis],
                reason=reason,
                residual=money(residual, currency),
            )
        return {**result, "status": status, "reason": reason}

    def transaction(self, txn: Mapping[str, object]) -> dict[str, object]:
        txn_id = self._claim_id(txn, "transaction")
        values = [self._value(item, txn_id, "value") for item in _items(txn, "values")]
        adjustments: list[tuple[_Value, str, str, Decimal]] = []
        for item in _items(txn, "adjustments"):
            adj = self._value(item, txn_id, "adjustment")
            adj.source = _text(item, "kind", field_name=adj.id)
            from_basis = _text(item, "from_basis", field_name=adj.id)
            to_basis = _text(item, "to_basis", field_name=adj.id)
            effect = item.get("effect")
            if from_basis not in BASES or to_basis not in BASES or effect not in ADJUSTMENT_EFFECTS:
                raise CustomsDataError("invalid_adjustment", f"{adj.id} has an invalid bridge")
            assert isinstance(effect, str)
            adjustments.append((adj, from_basis, to_basis, ADJUSTMENT_EFFECTS[effect]))
        adjustments.sort(key=lambda a: a[0].id)

        grouped: dict[str, list[_Value]] = {}
        for value in sorted(values, key=lambda v: (BASES.index(v.basis), v.date, v.id)):
            grouped.setdefault(value.basis, []).append(value)
        for basis in EXPECTED_BASES:
            if basis not in grouped:
                self._gap(f"{basis}_record_missing", txn_id, [txn_id])
        bases = [self._basis(b, grouped[b], txn_id) for b in BASES if b in grouped]
        comparisons = (
            [self._compare(bases[0], other, adjustments, txn_id) for other in bases[1:]]
            if bases
            else []
        )
        for comparison in comparisons:
            status = str(comparison["status"])
            self.statuses[status] = self.statuses.get(status, 0) + 1
        return {
            "transaction_id": txn_id,
            "description": txn.get("description"),
            "anchor_basis": bases[0].basis if bases else None,
            "values": [self._render_value(v) for b in bases for v in b.values],
            "adjustments": [
                {
                    "record_id": adj.id,
                    "kind": adj.source,
                    "from_basis": from_basis,
                    "to_basis": to_basis,
                    "effect": "add" if sign > 0 else "subtract",
                    "amount": money(adj.txn_amount, adj.txn_currency),
                    "date": adj.date,
                }
                for adj, from_basis, to_basis, sign in adjustments
            ],
            "basis_totals": [
                {
                    "basis": b.basis,
                    "transaction_amount": (
                        money(b.txn_amount, b.txn_currency)
                        if b.txn_amount is not None and b.txn_currency is not None
                        else None
                    ),
                    "reporting_amount": (
                        money(b.reporting, self.reporting) if b.reporting is not None else None
                    ),
                    "valuation_bases": b.valuation_bases,
                    "contradicted": b.contradicted,
                    "record_ids": sorted(v.id for v in b.values),
                }
                for b in bases
            ],
            "comparisons": comparisons,
        }

    def _render_value(self, value: _Value) -> dict[str, object]:
        return {
            "record_id": value.id,
            "basis": value.basis,
            "source": value.source,
            "reference": value.reference,
            "date": value.date,
            "valuation_basis": value.valuation_basis,
            "amount": money(value.amount, value.currency),
            "transaction_amount": money(value.txn_amount, value.txn_currency),
            "reporting_amount": (
                money(value.reporting, self.reporting) if value.reporting is not None else None
            ),
            "exchange_rate": (
                None
                if value.rate is None
                else {
                    "rate_id": value.rate.id,
                    "from_currency": value.rate.from_currency,
                    "to_currency": self.reporting,
                    "rate": str(value.rate.rate),
                    "date": value.rate.date,
                    "source": value.rate.source,
                }
            ),
            "evidence_ids": value.evidence_ids,
        }


def _ordered(items: list[dict[str, object]]) -> list[dict[str, object]]:
    """Order findings independently of input list order so replays are identical."""
    return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))


def reconcile_customs_values(
    record: Mapping[str, object],
    *,
    reporting_currency: str = DEFAULT_CURRENCY,
    tolerance: Decimal = DEFAULT_TOLERANCE,
) -> dict[str, object]:
    """Map and compare the customs, invoice, accounting, payment and transfer-pricing
    values of each imported transaction in ``record``; advisory output only."""
    if isinstance(tolerance, bool) or not isinstance(tolerance, Decimal):
        raise CustomsDataError("invalid_policy", "tolerance must be a Decimal")
    if not tolerance.is_finite() or tolerance < 0 or tolerance != tolerance.quantize(CENT):
        raise CustomsDataError("invalid_policy", "tolerance must be non-negative cents")
    reporting = _currency(reporting_currency, field_name="reporting_currency")
    if record.get("reporting_currency", reporting) != reporting:
        raise CustomsDataError(
            "currency_mismatch", "Record reporting currency differs from requested currency"
        )
    reconciler = _Reconciler(record, reporting, tolerance)
    reconciler.load_rates()
    transactions = sorted(_items(record, "transactions"), key=lambda t: str(t.get("id", "")))
    rendered = [reconciler.transaction(txn) for txn in transactions]
    if not rendered:
        reconciler.gaps.append(
            {
                "code": "no_import_transactions_supplied",
                "transaction_id": None,
                "interpretation": INCOMPLETE_DATA,
                "evidence_ids": [],
            }
        )
    return {
        "kind": "customs_value_reconciliation",
        "case_ref": record.get("case_id"),
        "synthetic": record.get("synthetic") is True,
        "method": "deterministic_decimal",
        "calculation_version": CALCULATION_VERSION,
        "reporting_currency": reporting,
        "tolerance": str(tolerance),
        "rounding": "ROUND_HALF_UP to 0.01 after amount * rate",
        "advisory": True,
        "requires_officer_review": True,
        "decision": None,
        "transactions": rendered,
        "comparison_status_counts": dict(sorted(reconciler.statuses.items())),
        "gaps": _ordered(reconciler.gaps),
        "discrepancies": _ordered(reconciler.discrepancies),
        "context": _ordered(reconciler.context),
        "evidence_ids": sorted(reconciler.seen_ids),
        "notice": NOTICE,
    }
