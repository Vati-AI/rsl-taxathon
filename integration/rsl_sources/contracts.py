"""Versioned read contracts for RSL source systems (TAX-111, ADR-040).

One contract per RSL system record kind. Every shape is an **assumption** until RSL
supplies a specification: the contract IDs end in ``-assumed`` and no real host, path or
field name is claimed. Vati reads these records; it never becomes a second store of them.

Rules shared by every contract:

- money is a non-negative decimal string with at most two places (Maloti unless the
  record names another ISO 4217 currency); floats are refused;
- dates are ISO ``YYYY-MM-DD`` and VAT periods ``YYYY-MM``;
- every record names the taxpayer it belongs to, so a read can be scoped to one
  taxpayer of one tenant;
- an RSL model score stays RSL's: it carries the model ID and version and is never
  relabelled as a Vati score.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

ASSUMED_SHAPE_NOTICE = (
    "Assumed record shape pending an RSL-supplied specification; not an RSL interface."
)

_MONEY = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]{1,2})?")
_DATE = re.compile(r"\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])")
_PERIOD = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
_TIN = re.compile(r"\d{9}")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,79}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_SCORE = re.compile(r"(0(\.\d{1,4})?|1(\.0{1,4})?)")
# Claim statuses RSL's Online Refunds Application is publicly reported to show (October 2025
# launch coverage, docs/reference/RSL_LANDSCAPE.md). Assumed codes, not RSL's own values.
CLAIM_STATUSES = frozenset(
    {"processing", "pending", "audit_in_progress", "processed", "rejected", "cancelled"}
)


class SourceContractError(ValueError):
    """A record does not satisfy its source contract; carries a stable code and field."""

    def __init__(self, code: str, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.code = code
        self.field = field


@dataclass(frozen=True)
class SourceContract:
    """Field rules for one RSL record kind."""

    contract_id: str
    system: str
    kind: str
    taxpayer_field: str
    id_field: str
    fields: Mapping[str, str]  # field -> rule name
    optional: frozenset[str] = frozenset()
    lists: Mapping[str, Mapping[str, str]] | None = None  # list field -> item rules
    optional_rules: Mapping[str, str] | None = None  # optional field -> rule name


def _c(system: str, kind: str, taxpayer: str, ident: str, fields: dict[str, str], **kw: Any):
    return SourceContract(
        contract_id=f"rsl.{system}.{kind}.v0-assumed",
        system=system,
        kind=kind,
        taxpayer_field=taxpayer,
        id_field=ident,
        fields=fields,
        **kw,
    )


CONTRACTS: dict[tuple[str, str], SourceContract] = {
    (c.system, c.kind): c
    for c in (
        _c(
            "etax",
            "vat_return",
            "tin",
            "return_id",
            {
                "return_id": "ref",
                "tin": "tin",
                "period": "period",
                "standard_rated_sales": "money",
                "zero_rated_sales": "money",
                "exempt_sales": "money",
                "output_vat": "money",
                "input_vat_claimed": "money",
                "imported_services_vat_declared": "money",
                "refund_claim": "money",
                "filed_on": "date",
            },
        ),
        _c(
            "etax",
            "wht_return",
            "tin",
            "wht_return_id",
            {"wht_return_id": "ref", "tin": "tin", "period": "period", "filed_on": "date"},
            lists={
                "lines": {
                    "line_id": "ref",
                    "payee_ref": "ref",
                    "payment_type": "code",
                    "gross_amount": "money",
                    "wht_amount": "money",
                    "treaty_relief_claimed": "bool",
                }
            },
        ),
        _c(
            "etax",
            "cit_return",
            "tin",
            "cit_return_id",
            {
                "cit_return_id": "ref",
                "tin": "tin",
                "year": "year",
                "revenue": "money",
                "related_party_fees": "money",
                "other_expenses": "money",
                "interest_expense": "money",
                "taxable_income_sign": "sign",
                "taxable_income_abs": "money",
            },
        ),
        _c(
            "etax",
            "paye_roster",
            "tin",
            "roster_id",
            {"roster_id": "ref", "tin": "tin", "year": "year"},
            lists={"employees": {"person_ref": "ref"}},
        ),
        _c(
            "idms",
            "invoice",
            "buyer_tin",
            "idms_id",
            {
                "idms_id": "ref",
                "invoice_number": "ref",
                "supplier_tin": "tin",
                "buyer_tin": "tin",
                "issue_date": "date",
                "net_amount": "money",
                "vat_amount": "money",
                "supplier_output_declared": "bool",
            },
        ),
        _c(
            "idms",
            "supplier_integration",
            "supplier_tin",
            "supplier_tin",
            {"supplier_tin": "tin", "integrated": "bool"},
        ),
        _c(
            "online_refunds",
            "claim",
            "tin",
            "claim_id",
            {
                "claim_id": "ref",
                "tin": "tin",
                "period": "period",
                "return_id": "ref",
                "claimed_amount": "money",
                "submitted_on": "date",
            },
            # TAX-125: the status RSL shows the taxpayer, when RSL supplies it.
            optional_rules={"status": "claim_status"},
            lists={
                "lines": {
                    "line_id": "ref",
                    "kind": "code",
                    "supplier_ref": "ref",
                    "invoice_number": "ref",
                    "invoice_date": "date",
                    "net_amount": "money",
                    "vat_amount": "money",
                },
                "uploaded_documents": {"document_id": "ref", "line_id": "ref", "kind": "code"},
            },
        ),
        _c(
            "rsl_fraud_model",
            "score",
            "tin",
            "claim_id",
            {
                "claim_id": "ref",
                "tin": "tin",
                "score": "score",
                "model_id": "ref",
                "model_version": "ref",
                "owner": "owner_rsl",
            },
        ),
        # TAX-123 (ADR-043 §1): a referral from RSL's risk-based audit system on PSRM. RSL
        # selects; Vati investigates. The risk sets and their scores are RSL's own; each
        # carries owner RSL and is never relabelled as a Vati score.
        _c(
            "audit_system",
            "referral",
            "tin",
            "audit_case_ref",
            {
                "audit_case_ref": "ref",
                "tin": "tin",
                "referred_on": "date",
                "selection_reason": "code",
            },
            # The VAT period under audit, when RSL names one.
            optional_rules={"period": "period"},
            lists={
                "risk_sets": {
                    "risk_set_id": "ref",
                    "risk_set_version": "ref",
                    "score": "score",
                    "owner": "owner_rsl",
                }
            },
        ),
        _c(
            "asycuda",
            "entry",
            "importer_tin",
            "entry_id",
            {
                "entry_id": "ref",
                "importer_tin": "tin",
                "registered_on": "date",
                "origin_country": "country",
                "customs_value": "money",
                "import_vat": "money",
                "currency": "currency",
            },
        ),
        _c(
            "registry",
            "entity",
            "entity_id",
            "entity_id",
            {
                "entity_id": "ref",
                "country": "country",
                "kind": "code",
                "vat_registered": "bool",
            },
            optional=frozenset({"tin", "group_id"}),
        ),
        _c(
            "central_bank",
            "cross_border_payment",
            "resident_tin",
            "report_id",
            {
                "report_id": "ref",
                "resident_tin": "tin",
                "value_date": "date",
                "direction": "code",
                "amount": "money",
                "currency": "currency",
                "counterparty_country": "country",
            },
        ),
    )
}


def _check(rule: str, value: object, field: str) -> None:
    ok: bool
    if rule == "money":
        ok = isinstance(value, str) and bool(_MONEY.fullmatch(value))
        if isinstance(value, float):
            raise SourceContractError("float_money", field, "money must be a decimal string")
    elif rule == "date":
        ok = isinstance(value, str) and bool(_DATE.fullmatch(value))
    elif rule == "period":
        ok = isinstance(value, str) and bool(_PERIOD.fullmatch(value))
    elif rule == "year":
        ok = isinstance(value, int) and not isinstance(value, bool) and 1990 <= value <= 2100
    elif rule == "tin":
        ok = isinstance(value, str) and bool(_TIN.fullmatch(value))
    elif rule in {"ref", "code"}:
        ok = isinstance(value, str) and bool(_REF.fullmatch(value))
    elif rule == "bool":
        ok = isinstance(value, bool)
    elif rule == "score":
        ok = isinstance(value, str) and bool(_SCORE.fullmatch(value))
    elif rule == "sign":
        ok = value in {"profit", "loss", "nil"}
    elif rule == "country":
        ok = isinstance(value, str) and bool(re.fullmatch(r"[a-z]{2}", value))
    elif rule == "currency":
        ok = isinstance(value, str) and bool(_CURRENCY.fullmatch(value))
    elif rule == "owner_rsl":
        ok = value == "RSL"
    elif rule == "claim_status":
        ok = value in CLAIM_STATUSES
    else:  # pragma: no cover - contract definition error
        raise AssertionError(rule)
    if not ok:
        raise SourceContractError("invalid_field", field, f"does not satisfy rule {rule!r}")


def validate_record(contract: SourceContract, record: object) -> dict[str, Any]:
    """Return a copy of ``record`` if it satisfies ``contract``; raise otherwise."""
    if not isinstance(record, Mapping):
        raise SourceContractError("invalid_record", contract.kind, "record must be an object")
    allowed = (
        set(contract.fields)
        | set(contract.optional)
        | set(contract.lists or {})
        | set(contract.optional_rules or {})
    )
    extra = set(record) - allowed
    if extra:
        raise SourceContractError("unexpected_field", sorted(extra)[0], "not in the contract")
    for field, rule in contract.fields.items():
        if field not in record:
            raise SourceContractError("missing_field", field, "required by the contract")
        _check(rule, record[field], field)
    for field in contract.optional:
        if record.get(field) is not None:
            _check("tin" if field == "tin" else "ref", record[field], field)
    for field, rule in (contract.optional_rules or {}).items():
        if record.get(field) is not None:
            _check(rule, record[field], field)
    for field, rules in (contract.lists or {}).items():
        items = record.get(field)
        if not isinstance(items, list):
            raise SourceContractError("missing_field", field, "must be a list")
        for index, item in enumerate(items):
            if not isinstance(item, Mapping) or set(item) - set(rules):
                raise SourceContractError("invalid_field", f"{field}[{index}]", "bad item")
            for key, rule in rules.items():
                if key not in item:
                    raise SourceContractError("missing_field", f"{field}[{index}].{key}", "")
                _check(rule, item[key], f"{field}[{index}].{key}")
    return dict(record)


def contract_for(system: str, kind: str) -> SourceContract:
    try:
        return CONTRACTS[(system, kind)]
    except KeyError:
        raise SourceContractError("unknown_contract", f"{system}.{kind}", "no contract") from None
