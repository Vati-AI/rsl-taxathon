"""Deterministic VAT refund reconciliation and advisory lane proposal (TAX-048).

Every amount is a ``decimal.Decimal`` parsed from a decimal string; floats are
rejected and nothing is rounded silently. No model is consulted for numbers.

The lane is a proposal only. It never records a decision, pays or denies a
refund, or labels abuse; an officer must approve or change it. Thresholds are
prototype choices documented here, not RSL policy.
"""

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Literal, TypeAlias

CALCULATION_VERSION = "tax048-reconciliation-v1"
LANE_POLICY_VERSION = "tax048-lanes-v1"
DEFAULT_CURRENCY = "LSL"
CENT = Decimal("0.01")
SHARE_QUANTUM = Decimal("0.0001")
# Standard lane requires most of the claim to be explained: the unresolved
# share must not exceed this fraction of the claimed refund (prototype choice).
STANDARD_MAX_UNRESOLVED_SHARE = Decimal("0.50")

# Two decimal places at most (cents); no exponent, sign or whitespace forms.
_MONEY = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]{1,2})?")
_CURRENCY = re.compile(r"[A-Z]{3}")
# A record identifier that is safe to repeat in officer- or taxpayer-facing text.
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}")

Lane: TypeAlias = Literal["fast", "standard", "targeted"]

# Indicators that require specialist investigation rather than an evidence request.
ESCALATING_INDICATORS = frozenset({"related_party_transaction"})
# Indicators that need targeted information but no specialist escalation by themselves.
REVIEW_INDICATORS = frozenset(
    {
        "imported_service_treatment_unverified",
        "service_classification_evidence_gap",
        "consecutive_loss_periods",
        "economic_context_requires_human_policy_review",
    }
)


class RefundDataError(ValueError):
    """Input records cannot be reconciled safely; carries a stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_money(value: object, *, field: str) -> Decimal:
    """Parse a non-negative cent amount from a string; reject floats and sub-cent values."""
    if not isinstance(value, str) or not _MONEY.fullmatch(value):
        raise RefundDataError(
            "invalid_money", f"{field} must be a non-negative decimal string with at most 2 places"
        )
    return Decimal(value).quantize(CENT)


def safe_reference(value: object) -> str | None:
    """Return ``value`` only if it is a well-formed record identifier, else ``None``.

    Identifiers such as counterparty IDs come from submitted records and are
    untrusted. Free text (spaces, punctuation, instructions) is never copied
    into draft questions or findings; callers use a neutral phrase instead.
    """
    return value if isinstance(value, str) and _REFERENCE.fullmatch(value) else None


def money(amount: Decimal, currency: str) -> dict[str, str]:
    """Render a TAX-018 ``Money`` object with exactly two decimal places."""
    return {"amount": str(amount.quantize(CENT)), "currency": currency}


def _text(record: Mapping[str, object], key: str, *, field: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RefundDataError("invalid_record", f"{field}.{key} must be a non-empty string")
    return value


def _items(record: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    value = record.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RefundDataError("invalid_record", f"{key} must be a list of objects")
    return value


def _unique_ids(
    items: Sequence[Mapping[str, object]], kind: str
) -> dict[str, Mapping[str, object]]:
    by_id: dict[str, Mapping[str, object]] = {}
    for item in items:
        item_id = _text(item, "id", field=kind)
        if item_id in by_id:
            raise RefundDataError("duplicate_record_id", f"Duplicate {kind} id {item_id}")
        by_id[item_id] = item
    return by_id


def _consecutive_loss_periods(periods: object) -> int:
    """Return the longest run of consecutive quarters in ``YYYY-Qn`` labels."""
    if not isinstance(periods, list):
        return 0
    indexes = []
    for label in periods:
        match = re.fullmatch(r"([0-9]{4})-Q([1-4])", label) if isinstance(label, str) else None
        if match:
            indexes.append(int(match.group(1)) * 4 + int(match.group(2)) - 1)
    longest = run = 0
    previous: int | None = None
    for index in sorted(set(indexes)):
        run = run + 1 if previous is not None and index == previous + 1 else 1
        longest = max(longest, run)
        previous = index
    return longest


def reconcile_refund(
    record: Mapping[str, object], *, currency: str = DEFAULT_CURRENCY
) -> dict[str, object]:
    """Compute claimed, explained and unresolved refund amounts from synthetic records.

    An invoice explains input VAT only when it is a purchase charging VAT, links
    to a supplied contract, its net + VAT equals gross, and linked payments
    total its gross. Missing records are gaps; contradictory records are
    discrepancies. Explained refund = min(claim, max(0, explained VAT - output VAT)).
    """
    if not _CURRENCY.fullmatch(currency):
        raise RefundDataError("invalid_currency", "currency must be an ISO 4217 code")
    record_currency = record.get("currency", currency)
    if record_currency != currency:
        raise RefundDataError(
            "currency_mismatch", "Record currency differs from requested currency"
        )
    vat_return = record.get("return")
    if not isinstance(vat_return, dict):
        raise RefundDataError("invalid_record", "return must be an object")
    return_id = _text(vat_return, "id", field="return")
    claimed = parse_money(vat_return.get("refund_claim"), field="return.refund_claim")
    input_claimed = parse_money(
        vat_return.get("input_vat_claimed"), field="return.input_vat_claimed"
    )
    output_vat = parse_money(vat_return.get("output_vat"), field="return.output_vat")

    contracts = _unique_ids(_items(record, "contracts"), "contract")
    invoices = _unique_ids(_items(record, "invoices"), "invoice")
    payments = _unique_ids(_items(record, "payments"), "payment")

    paid: dict[str, Decimal] = {}
    payment_ids: dict[str, list[str]] = {}
    discrepancies: list[dict[str, object]] = []
    gaps: list[dict[str, object]] = []
    indicators: list[dict[str, object]] = []
    for payment_id, payment in payments.items():
        invoice_id = _text(payment, "invoice_id", field="payment")
        amount = parse_money(payment.get("amount"), field=f"payment {payment_id}.amount")
        if invoice_id not in invoices:
            discrepancies.append({"code": "payment_without_invoice", "evidence_ids": [payment_id]})
            continue
        paid[invoice_id] = paid.get(invoice_id, Decimal("0.00")) + amount
        payment_ids.setdefault(invoice_id, []).append(payment_id)

    if claimed != max(Decimal("0.00"), input_claimed - output_vat):
        discrepancies.append({"code": "return_arithmetic_mismatch", "evidence_ids": [return_id]})

    documented_vat = Decimal("0.00")
    explained_vat = Decimal("0.00")
    line_items: list[dict[str, object]] = []
    explaining_ids: list[str] = []
    related_ids: list[str] = []
    # Purchase invoices that document input VAT: cited with the return wherever a
    # finding compares the claim with the documented VAT (TAX-024 ground truth).
    documented_ids: list[str] = []
    for invoice_id, invoice in invoices.items():
        if invoice.get("direction") != "purchase":
            continue
        net = parse_money(invoice.get("net_amount"), field=f"invoice {invoice_id}.net_amount")
        vat = parse_money(invoice.get("vat_amount"), field=f"invoice {invoice_id}.vat_amount")
        gross = parse_money(invoice.get("gross_amount"), field=f"invoice {invoice_id}.gross_amount")
        contract_id = invoice.get("contract_id")
        contract = contracts.get(contract_id) if isinstance(contract_id, str) else None
        evidence: list[str] = [invoice_id]
        if contract is not None and isinstance(contract_id, str):
            evidence.append(contract_id)
        evidence.extend(payment_ids.get(invoice_id, []))
        vat_status = invoice.get("vat_status")
        if vat_status == "treatment_unverified":
            indicators.append(
                {"code": "imported_service_treatment_unverified", "evidence_ids": evidence}
            )
        if contract and contract.get("classification") == "unresolved":
            indicators.append(
                {"code": "service_classification_evidence_gap", "evidence_ids": evidence}
            )
        if contract and contract.get("related_party") is True:
            related_ids.extend(evidence)
        if vat == 0:
            line_items.append(
                {
                    "invoice_id": invoice_id,
                    "vat": money(vat, currency),
                    "status": "no_vat_charged",
                    "reasons": [],
                    "evidence_ids": evidence,
                }
            )
            continue
        documented_vat += vat
        documented_ids.append(invoice_id)
        reasons: list[str] = []
        status = "explained"
        if net + vat != gross:
            reasons.append("invoice_total_mismatch")
        if invoice_id in paid and paid[invoice_id] != gross:
            reasons.append("payment_total_differs_from_invoice")
        if reasons:
            status = "mismatch"
            discrepancies.extend({"code": code, "evidence_ids": evidence} for code in reasons)
        if contract is None:
            reasons.append("contract_evidence_missing")
        if invoice_id not in paid:
            reasons.append("payment_evidence_missing")
        missing = [code for code in reasons if code.endswith("_missing")]
        if missing:
            gaps.extend({"code": code, "evidence_ids": evidence} for code in missing)
            if status == "explained":
                status = "missing_evidence"
        if status == "explained":
            explained_vat += vat
            explaining_ids.extend(evidence)
        line_items.append(
            {
                "invoice_id": invoice_id,
                "vat": money(vat, currency),
                "status": status,
                "reasons": reasons,
                "evidence_ids": evidence,
            }
        )

    if related_ids:
        indicators.append({"code": "related_party_transaction", "evidence_ids": related_ids})
    if _consecutive_loss_periods(record.get("loss_periods")) >= 3:
        indicators.append({"code": "consecutive_loss_periods", "evidence_ids": [return_id]})
    context = record.get("economic_context")
    if isinstance(context, dict) and context.get("legal_route") != "verified":
        indicators.append(
            {
                "code": "economic_context_requires_human_policy_review",
                "evidence_ids": [return_id, *documented_ids],
            }
        )

    supported_refund = max(Decimal("0.00"), explained_vat - output_vat)
    explained = min(claimed, supported_refund)
    unresolved = claimed - explained
    undocumented = max(Decimal("0.00"), claimed - max(Decimal("0.00"), documented_vat - output_vat))
    if undocumented > 0:
        gaps.append(
            {
                "code": "refund_claim_exceeds_documented_input_vat",
                "amount": money(undocumented, currency),
                "evidence_ids": [return_id, *documented_ids],
            }
        )
    share = (explained / claimed).quantize(SHARE_QUANTUM) if claimed else None

    return {
        "case_ref": record.get("case_id"),
        "return_id": return_id,
        "synthetic": record.get("synthetic") is True,
        "method": "deterministic_decimal",
        "calculation_version": CALCULATION_VERSION,
        "currency": currency,
        "claimed_refund": money(claimed, currency),
        "output_vat": money(output_vat, currency),
        "documented_input_vat": money(documented_vat, currency),
        "explained_input_vat": money(explained_vat, currency),
        "explained_amount": money(explained, currency),
        "unresolved_amount": money(unresolved, currency),
        "explained_share": None if share is None else str(share),
        "line_items": line_items,
        "gaps": gaps,
        "discrepancies": discrepancies,
        "indicators": indicators,
        "evidence_ids": sorted({return_id, *explaining_ids}),
    }


def _entries(reconciliation: Mapping[str, object], key: str) -> list[dict[str, object]]:
    value = reconciliation.get(key, [])
    if not isinstance(value, list):
        raise RefundDataError("invalid_reconciliation", f"{key} must be a list")
    result = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str):
            raise RefundDataError("invalid_reconciliation", f"{key} entries need a code")
        evidence = item.get("evidence_ids", [])
        if not isinstance(evidence, list):
            raise RefundDataError("invalid_reconciliation", f"{key} evidence_ids must be a list")
        result.append({"code": item["code"], "evidence_ids": [str(e) for e in evidence]})
    return result


def propose_refund_lane(
    reconciliation: Mapping[str, object],
    *,
    max_unresolved_share: Decimal = STANDARD_MAX_UNRESOLVED_SHARE,
) -> dict[str, object]:
    """Propose fast/standard/targeted from a reconciliation; advisory until officer approval.

    - no lane: nothing is claimed (the case may still need non-refund review);
    - targeted: any discrepancy, an escalating indicator (related party), or an
      unresolved share above ``max_unresolved_share`` of the claim;
    - standard: some amount unresolved or a review indicator outstanding;
    - fast: fully explained with no gap, discrepancy or indicator.
    """
    if isinstance(max_unresolved_share, bool) or not isinstance(max_unresolved_share, Decimal):
        raise RefundDataError("invalid_policy", "max_unresolved_share must be a Decimal")
    if not Decimal("0") <= max_unresolved_share <= Decimal("1"):
        raise RefundDataError("invalid_policy", "max_unresolved_share must be between 0 and 1")

    def amount(key: str) -> Decimal:
        value = reconciliation.get(key)
        if not isinstance(value, dict):
            raise RefundDataError("invalid_reconciliation", f"{key} must be a Money object")
        return parse_money(value.get("amount"), field=key)

    claimed = amount("claimed_refund")
    unresolved = amount("unresolved_amount")
    return_id = reconciliation.get("return_id")
    if not isinstance(return_id, str) or not return_id.strip():
        raise RefundDataError("invalid_reconciliation", "return_id is required")
    explaining = reconciliation.get("evidence_ids")
    if not isinstance(explaining, list):
        raise RefundDataError("invalid_reconciliation", "evidence_ids must be a list")
    line_items = reconciliation.get("line_items", [])
    if not isinstance(line_items, list):
        raise RefundDataError("invalid_reconciliation", "line_items must be a list")
    # Invoices whose VAT is documented but not explained: why an amount is unresolved.
    unexplained = [
        str(item["invoice_id"])
        for item in line_items
        if isinstance(item, dict)
        and item.get("status") in ("mismatch", "missing_evidence")
        and isinstance(item.get("invoice_id"), str)
    ]
    entries = {
        key: _entries(reconciliation, key) for key in ("discrepancies", "indicators", "gaps")
    }
    codes = {str(item["code"]) for items in entries.values() for item in items}
    reasons: list[dict[str, object]] = [
        {"code": item["code"], "evidence_ids": item["evidence_ids"]}
        for items in entries.values()
        for item in items
    ]

    lane: Lane | None
    # Every lane-level reason cites the records it rests on (TAX-056 citation coverage).
    if claimed == 0:
        lane = None
        reasons.insert(0, {"code": "no_refund_claimed", "evidence_ids": [return_id]})
    elif entries["discrepancies"] or codes & ESCALATING_INDICATORS:
        lane = "targeted"
    elif unresolved > claimed * max_unresolved_share:
        lane = "targeted"
        reasons.insert(
            0, {"code": "material_unresolved_amount", "evidence_ids": [return_id, *unexplained]}
        )
    elif unresolved > 0 or codes & REVIEW_INDICATORS:
        lane = "standard"
    else:
        lane = "fast"
        reasons.insert(
            0,
            {
                "code": "claim_fully_explained",
                "evidence_ids": [return_id, *(str(e) for e in explaining if e != return_id)],
            },
        )

    return {
        "kind": "refund_lane_proposal",
        "proposed_lane": lane,
        "status": "proposed",
        "advisory": True,
        "requires_officer_approval": True,
        "decision": None,
        "approved_by": None,
        "policy_version": LANE_POLICY_VERSION,
        "calculation_version": reconciliation.get("calculation_version"),
        "thresholds": {"max_unresolved_share_for_standard": str(max_unresolved_share)},
        "claimed_refund": reconciliation.get("claimed_refund"),
        "explained_amount": reconciliation.get("explained_amount"),
        "unresolved_amount": reconciliation.get("unresolved_amount"),
        "reasons": reasons,
        "notice": (
            "Advisory proposal from deterministic reconciliation. It is not a refund "
            "decision, denial, payment or abuse finding; an officer must approve it."
        ),
    }
