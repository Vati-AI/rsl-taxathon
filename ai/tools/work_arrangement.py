"""Structure employment-versus-consultancy facts for specialist review (TAX-051).

The review groups cited statements about a working arrangement by factor and
marks each factor ``unknown``, ``consistent`` or ``conflicting`` across sources.
It never weighs factors towards a classification: the factor list is a neutral
checklist, not a legal test, and no Lesotho rule has been verified. The
classification always stays ``unresolved_pending_specialist`` and no tax is
assessed. Only an authenticated specialist can record an opinion (TAX-046
``specialist.opinion.record``), through the TAX-051 checkpointed workflow.

Optional record fields read here: ``record.work_facts``
``[{"contract_id", "factor", "value", "source", "evidence_ids"}]`` and
``record.documents`` ``[{"id", "kind"}]``. A contract is referred when its
``classification`` is ``unresolved`` or it has conflicting facts.
"""

import re
from collections.abc import Mapping
from decimal import Decimal

from ai.tools.refund_reconciliation import (
    RefundDataError,
    _items,
    _text,
    _unique_ids,
    parse_money,
)

REVIEW_VERSION = "tax051-work-arrangement-v1"
CLASSIFICATION_STATUS = "unresolved_pending_specialist"
MAX_FACTS = 200
# Neutral checklist of facts a specialist may want; not a weighted legal test.
FACTORS = (
    "direction_and_control",
    "integration_into_business",
    "exclusivity",
    "tools_and_equipment",
    "financial_risk",
    "payment_basis",
    "right_of_substitution",
    "duration_and_continuity",
)
SOURCES = frozenset(
    {"contract", "work_evidence", "taxpayer_statement", "worker_statement", "transaction_records"}
)
PROHIBITED_CONCLUSIONS = frozenset(
    {
        "employment_relationship_established",
        "consultancy_misclassification",
        "tax_evasion",
        "tax_assessment",
        "penalty",
        "proven_abuse",
    }
)
_VALUE = re.compile(r"[a-z][a-z0-9_]{0,63}")
FACTOR_QUESTIONS = {
    "direction_and_control": "who decided how, when and where the work was done",
    "integration_into_business": "how the work fitted into the business's own operations",
    "exclusivity": "whether the worker also provided services to other clients",
    "tools_and_equipment": "who supplied the tools, equipment and workspace used",
    "financial_risk": "who bore the cost if work had to be redone or was not paid for",
    "payment_basis": "how payment was calculated (for example per period or per deliverable)",
    "right_of_substitution": "whether someone else could do the work in the worker's place",
    "duration_and_continuity": "how long the arrangement has run and whether it is ongoing",
}


def _id_list(item: Mapping[str, object], key: str) -> list[str]:
    value = item.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise RefundDataError("invalid_record", f"{key} must be a list of evidence IDs")
    return list(value)


def _merged_evidence(statements: list[dict[str, object]]) -> list[str]:
    merged: list[str] = []
    for statement in statements:
        cited = statement.get("evidence_ids")
        if isinstance(cited, list):
            merged.extend(str(e) for e in cited if str(e) not in merged)
    return merged


def _derived_payment_fact(
    contract_id: str,
    invoices: list[tuple[str, Mapping[str, object]]],
) -> dict[str, object] | None:
    """Describe the billing pattern from transaction records without interpreting it."""
    if not invoices:
        return None
    amounts = [
        parse_money(inv.get("net_amount"), field=f"invoice {iid}.net_amount")
        for iid, inv in invoices
    ]
    if len(invoices) == 1:
        value = "single_invoice"
    elif len(set(amounts)) == 1:
        value = "repeated_equal_amounts"
    else:
        value = "varying_amounts"
    return {
        "contract_id": contract_id,
        "factor": "payment_basis",
        "value": value,
        "source": "transaction_records",
        "evidence_ids": [iid for iid, _ in invoices],
        "derived": True,
        "total_net": str(sum(amounts, Decimal("0.00"))),
    }


def review_work_arrangement(record: Mapping[str, object]) -> dict[str, object]:
    """Structure cited facts per factor; never classify. Raise ``RefundDataError`` if unsafe."""
    vat_return = record.get("return")
    if not isinstance(vat_return, Mapping):
        raise RefundDataError("invalid_record", "return must be an object")
    return_id = _text(vat_return, "id", field="return")
    contracts = _unique_ids(_items(record, "contracts"), "contract")
    invoices = _unique_ids(_items(record, "invoices"), "invoice")
    payments = _unique_ids(_items(record, "payments"), "payment")
    documents = _unique_ids(_items(record, "documents"), "document")
    known = frozenset({return_id, *contracts, *invoices, *payments, *documents})
    raw_facts = _items(record, "work_facts")
    if len(raw_facts) > MAX_FACTS:
        raise RefundDataError("fact_budget_exceeded", "Too many work facts for one review")

    facts: list[dict[str, object]] = []
    unsupplied: list[dict[str, object]] = []
    for raw in raw_facts:
        contract_id = _text(raw, "contract_id", field="work_fact")
        if contract_id not in contracts:
            raise RefundDataError("unresolved_citation", "Work fact refers to an unknown contract")
        factor = raw.get("factor")
        value = raw.get("value")
        source = raw.get("source")
        if factor not in FACTORS or not isinstance(value, str) or not _VALUE.fullmatch(value):
            raise RefundDataError(
                "invalid_record", "Work fact needs a known factor and a code value"
            )
        if source not in SOURCES:
            raise RefundDataError("invalid_record", "Work fact source is not recognised")
        cited = _id_list(raw, "evidence_ids")
        evidence = [item for item in cited if item in known]
        missing = [item for item in cited if item not in known]
        if missing:
            unsupplied.append(
                {
                    "code": "cited_evidence_not_supplied",
                    "evidence_ids": [contract_id],
                    "missing_ids": missing,
                }
            )
        if not evidence:
            continue  # an uncited statement is not a fact for the specialist
        facts.append(
            {
                "contract_id": contract_id,
                "factor": factor,
                "value": value,
                "source": source,
                "evidence_ids": evidence,
                "derived": False,
            }
        )

    referrals: list[dict[str, object]] = []
    gaps: list[dict[str, object]] = list(unsupplied)
    questions: list[dict[str, object]] = []
    for contract_id, contract in contracts.items():
        contract_invoices = [
            (iid, inv) for iid, inv in invoices.items() if inv.get("contract_id") == contract_id
        ]
        contract_facts = [f for f in facts if f["contract_id"] == contract_id]
        derived = _derived_payment_fact(contract_id, contract_invoices)
        if derived is not None:
            contract_facts.append(derived)
        unresolved = contract.get("classification") == "unresolved"
        factors: list[dict[str, object]] = []
        conflicts: list[dict[str, object]] = []
        for factor in FACTORS:
            statements = [f for f in contract_facts if f["factor"] == factor]
            values = sorted({str(f["value"]) for f in statements})
            status = (
                "unknown"
                if not statements
                else ("conflicting" if len(values) > 1 else "consistent")
            )
            entry: dict[str, object] = {
                "factor": factor,
                "status": status,
                "statements": statements,
            }
            factors.append(entry)
            if status == "conflicting":
                conflicts.append(
                    {
                        "code": "conflicting_work_facts",
                        "factor": factor,
                        "values": values,
                        "evidence_ids": _merged_evidence(statements),
                    }
                )
        if not (unresolved or conflicts):
            continue
        base = [contract_id, *(iid for iid, _ in contract_invoices)]
        gaps.append({"code": "service_classification_evidence_gap", "evidence_ids": base})
        unknown = [str(f["factor"]) for f in factors if f["status"] == "unknown"]
        for factor in unknown:
            questions.append(
                {
                    "question_id": f"{contract_id}:{factor}",
                    "topic": factor,
                    "addressed_to": "taxpayer",
                    "evidence_ids": base,
                    "text": f"For the arrangement under {contract_id}, please describe "
                    f"{FACTOR_QUESTIONS[factor]}, and provide any records that show this.",
                    "status": "draft_requires_officer_approval",
                }
            )
        referrals.append(
            {
                "contract_id": contract_id,
                "counterparty_id": contract.get("counterparty_id"),
                "declared_classification": contract.get("classification"),
                "evidence_ids": base,
                "factors": factors,
                "conflicts": conflicts,
                "unknown_factors": unknown,
                "classification": {
                    "status": CLASSIFICATION_STATUS,
                    "value": None,
                    "auto_classification_permitted": False,
                    "decided_by": None,
                },
            }
        )

    return {
        "case_ref": record.get("case_id"),
        "synthetic": record.get("synthetic") is True,
        "method": "deterministic_fact_structuring",
        "review_version": REVIEW_VERSION,
        "referrals": referrals,
        "evidence_gaps": gaps,
        "evidence_questions": questions,
        "classification_status": CLASSIFICATION_STATUS if referrals else "no_referral",
        "assessment": None,
        "legal_conclusion": None,
        "enforcement_conclusion": None,
    }
