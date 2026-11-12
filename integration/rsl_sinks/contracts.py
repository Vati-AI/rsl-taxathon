"""Versioned outbound contracts from Vati to RSL systems (TAX-124, ADR-043 §1, ADR-048).

RSL selects, Vati investigates, RSL acts. These are the messages that carry Vati's
officer-approved results back to RSL:

- ``findings_pack``: the approved findings and decision outcome, for the RSL audit
  case or the Online Refunds claim the Vati case came from;
- ``lane_recommendation``: the TAX-114 lane and the undisputed amount, for the Online
  Refunds Application (or PSRM) to act on;
- ``evidence_requested``: a notice that Vati asked the taxpayer for evidence, with a
  deep link into the taxpayer portal.

Every shape is an **assumption** until RSL supplies a specification: the contract IDs end
in ``-assumed`` and no real host, path or field name is claimed. The stated route is RSL's
WSO2 API Manager (ADR-040); nothing here opens a network connection.

Rules shared by every contract:

- a message names the RSL record it belongs to (``rsl_reference``) and the taxpayer TIN;
- ``authority`` says what the message is: findings, a recommendation or a notice. No
  contract has a field that sets an RSL status, pays, offsets or assesses, and a message
  with any field outside its contract is refused;
- money is a non-negative decimal string with at most two places; floats are refused.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

ASSUMED_SHAPE_NOTICE = (
    "Assumed message shape pending an RSL-supplied specification; not an RSL interface."
)
ROUTE = "wso2_api_manager"
SCHEMA_VERSION = "v0-assumed"

FINDINGS_PACK = "findings_pack"
LANE_RECOMMENDATION = "lane_recommendation"
EVIDENCE_REQUESTED = "evidence_requested"
KINDS = (FINDINGS_PACK, LANE_RECOMMENDATION, EVIDENCE_REQUESTED)

# RSL systems a message may go to (the TAX-111 system names).
AUDIT_SYSTEM = "audit_system"
ONLINE_REFUNDS = "online_refunds"
RSL_SYSTEMS = (AUDIT_SYSTEM, ONLINE_REFUNDS)

# What each kind is allowed to be. None of them is an instruction RSL must follow.
AUTHORITY = {
    FINDINGS_PACK: "findings_for_rsl_review",
    LANE_RECOMMENDATION: "recommendation_only",
    EVIDENCE_REQUESTED: "notice_only",
}
# Names that would make a message an action on RSL's records. No contract has them; they
# are listed so the refusal is explicit and tested, not only implied.
FORBIDDEN_FIELDS = frozenset(
    {
        "status",
        "rsl_status",
        "refund_status",
        "claim_status",
        "set_status",
        "pay",
        "payment",
        "payment_amount",
        "release_amount",
        "offset",
        "offset_amount",
        "assess",
        "assessment",
        "assessed_amount",
    }
)

_MONEY = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]{1,2})?")
_DATE = re.compile(r"\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])")
_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_PERIOD = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
_TIN = re.compile(r"\d{9}")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,79}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LINK = re.compile(r"https?://[A-Za-z0-9.\-]+(:\d{1,5})?/taxpayer/requests/[0-9a-f\-]{36}")
TEXT_MAX = 2000

FINDING_KINDS = frozenset(
    {
        "observed_fact",
        "inference",
        "missing_evidence",
        "unverified_evidence",
        "contradictory_evidence",
    }
)
CONFIDENCE = frozenset({"low", "medium", "high"})
LANES = frozenset({"fast", "standard", "investigation"})


class OutboundContractError(ValueError):
    """A message does not satisfy its outbound contract; carries a stable code and field."""

    def __init__(self, code: str, field: str, message: str = "") -> None:
        super().__init__(f"{field}: {message or code}")
        self.code = code
        self.field = field


@dataclass(frozen=True)
class OutboundContract:
    contract_id: str
    system: str
    kind: str
    fields: Mapping[str, Any]  # field -> rule name, nested contract dict or [item rules]
    optional: frozenset[str] = frozenset()


_REFERENCE = {"system": "rsl_system", "record_id": "ref"}
_COMMON = {
    "message_id": "uuid",
    "contract_id": "contract_id",
    "kind": "kind",
    "authority": "authority",
    "route": "route",
    "notice": "notice",
    "rsl_reference": _REFERENCE,
    "tin": "tin",
    "vati_case_id": "uuid",
    "vati_case_ref": "ref",
    "issued_at": "datetime",
}


def _contract(system: str, kind: str, fields: dict[str, Any], optional: set[str] | None = None):
    return OutboundContract(
        contract_id=f"rsl.{system}.{kind}.{SCHEMA_VERSION}",
        system=system,
        kind=kind,
        fields={**_COMMON, **fields},
        optional=frozenset(optional or ()),
    )


_FINDING = {
    "finding_id": "uuid",
    "revision": "int",
    "kind": "finding_kind",
    "category": "code",
    "code": "code",
    "confidence": "confidence",
    "legal_route_status": "code",
    "explanation": "text",
    "unresolved_amount": "money_or_null",
    "currency": "currency_or_null",
    "evidence_count": "int",
}
_DECISION = {
    "decision_id": "uuid",
    "outcome": "code",
    "approved_at": "datetime",
    "approver_role": "code",
}

CONTRACTS: dict[tuple[str, str], OutboundContract] = {
    (c.system, c.kind): c
    for c in (
        # The findings pack goes to the RSL record the case came from: the audit case of an
        # audit referral (TAX-123) or the claim read from the Online Refunds Application
        # (TAX-122), which RSL may attach to its own audit case.
        *(
            _contract(
                system,
                FINDINGS_PACK,
                {"decision": _DECISION, "findings": [_FINDING]},
            )
            for system in RSL_SYSTEMS
        ),
        # Lane and undisputed amount: a recommendation for a claim, so only Online Refunds.
        _contract(
            ONLINE_REFUNDS,
            LANE_RECOMMENDATION,
            {
                "period": "period",
                "lane": "lane",
                "claimed_amount": "money",
                "undisputed_amount": "money",
                "disputed_amount": "money",
                "currency": "currency",
                "decision_due": "date",
                "proposal_id": "uuid",
                "proposal_revision": "int",
                "approved_decision_id": "uuid",
            },
        ),
        *(
            _contract(
                system,
                EVIDENCE_REQUESTED,
                {
                    "request_id": "uuid",
                    "due_date": "date",
                    "item_count": "int",
                    "document_types": ["code"],
                    "portal_link": "portal_link",
                },
            )
            for system in RSL_SYSTEMS
        ),
    )
}


def contract_for(system: str, kind: str) -> OutboundContract:
    try:
        return CONTRACTS[(system, kind)]
    except KeyError:
        raise OutboundContractError("unknown_contract", f"{system}.{kind}", "no contract") from None


def _ok(rule: str, value: object, contract: OutboundContract) -> bool:
    if isinstance(value, float):
        raise OutboundContractError("float_value", rule, "numbers travel as strings or ints")
    text = isinstance(value, str)
    checks = {
        "uuid": lambda: text and bool(_UUID.fullmatch(value)),  # type: ignore[arg-type]
        "ref": lambda: text and bool(_REF.fullmatch(value)),  # type: ignore[arg-type]
        "code": lambda: text and bool(_CODE.fullmatch(value)),  # type: ignore[arg-type]
        "tin": lambda: text and bool(_TIN.fullmatch(value)),  # type: ignore[arg-type]
        "date": lambda: text and bool(_DATE.fullmatch(value)),  # type: ignore[arg-type]
        "datetime": lambda: text and bool(_DATETIME.fullmatch(value)),  # type: ignore[arg-type]
        "period": lambda: text and bool(_PERIOD.fullmatch(value)),  # type: ignore[arg-type]
        "money": lambda: text and bool(_MONEY.fullmatch(value)),  # type: ignore[arg-type]
        "money_or_null": lambda: value is None or (text and bool(_MONEY.fullmatch(value))),  # type: ignore[arg-type]
        "currency": lambda: text and bool(_CURRENCY.fullmatch(value)),  # type: ignore[arg-type]
        "currency_or_null": lambda: value is None or (text and bool(_CURRENCY.fullmatch(value))),  # type: ignore[arg-type]
        "int": lambda: isinstance(value, int) and not isinstance(value, bool) and value >= 0,
        "text": lambda: text and 0 < len(value) <= TEXT_MAX,  # type: ignore[arg-type]
        "finding_kind": lambda: value in FINDING_KINDS,
        "confidence": lambda: value in CONFIDENCE,
        "lane": lambda: value in LANES,
        "rsl_system": lambda: value == contract.system,
        "contract_id": lambda: value == contract.contract_id,
        "kind": lambda: value == contract.kind,
        "authority": lambda: value == AUTHORITY[contract.kind],
        "route": lambda: value == ROUTE,
        "notice": lambda: value == ASSUMED_SHAPE_NOTICE,
        "portal_link": lambda: text and bool(_LINK.fullmatch(value)),  # type: ignore[arg-type]
    }
    if rule not in checks:  # pragma: no cover - contract definition error
        raise AssertionError(rule)
    return bool(checks[rule]())


def _validate(
    rules: Mapping[str, Any], value: object, path: str, contract: OutboundContract
) -> None:
    if not isinstance(value, Mapping):
        raise OutboundContractError("invalid_message", path or contract.kind, "must be an object")
    forbidden = FORBIDDEN_FIELDS.intersection(value)
    if forbidden:
        # The message would act on an RSL record: Vati never sets status, pays or assesses.
        raise OutboundContractError("forbidden_field", _join(path, sorted(forbidden)[0]))
    extra = set(value) - set(rules)
    if extra:
        raise OutboundContractError("unexpected_field", _join(path, sorted(extra)[0]))
    for field, rule in rules.items():
        where = _join(path, field)
        if field not in value:
            if path == "" and field in contract.optional:
                continue
            raise OutboundContractError("missing_field", where)
        item = value[field]
        if isinstance(rule, Mapping):
            _validate(rule, item, where, contract)
        elif isinstance(rule, list):
            if not isinstance(item, list):
                raise OutboundContractError("invalid_field", where, "must be a list")
            for index, element in enumerate(item):
                if isinstance(rule[0], Mapping):
                    _validate(rule[0], element, f"{where}[{index}]", contract)
                elif not _ok(rule[0], element, contract):
                    raise OutboundContractError("invalid_field", f"{where}[{index}]")
        elif not _ok(rule, item, contract):
            raise OutboundContractError("invalid_field", where, f"does not satisfy {rule!r}")


def _join(path: str, field: str) -> str:
    return f"{path}.{field}" if path else field


def validate_message(message: object) -> dict[str, Any]:
    """Return a copy of ``message`` if it satisfies the contract it names; raise otherwise."""
    if not isinstance(message, Mapping):
        raise OutboundContractError("invalid_message", "message", "must be an object")
    reference = message.get("rsl_reference")
    system = reference.get("system") if isinstance(reference, Mapping) else None
    contract = contract_for(str(system), str(message.get("kind")))
    _validate(contract.fields, message, "", contract)
    return dict(message)
