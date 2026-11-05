"""Deterministic related-party service and persistent-loss evidence review (TAX-049).

For each disclosed related-party contract the review tests the relationship,
service delivery, benefit to the taxpayer, possible duplication, and the payment
trail. For a persistent loss pattern it looks for documented alternative
explanations. Every observation, gap, discrepancy and item of counter-evidence
cites evidence IDs that resolve to a record in the case.

A related party, a loss streak or missing documentation is never a legal
conclusion. Unsupported services are evidence gaps with neutral questions;
pricing, intent and treatment are not assessed (``legal_conclusion`` is always
``None``).

Optional record fields read here (absent fields are gaps, never failures):
``contract.relationship`` ``{"type", "evidence_ids"}``,
``contract.service_category``, ``contract|invoice.delivery_evidence_ids``,
``contract|invoice.benefit_evidence_ids``, ``record.loss_explanations``
``[{"code", "description", "evidence_ids"}]`` and ``record.documents``
``[{"id", "kind"}]`` for supporting documents that citations may refer to.
"""

import re
from collections.abc import Mapping
from decimal import Decimal

from ai.tools.refund_reconciliation import (
    DEFAULT_CURRENCY,
    RefundDataError,
    _consecutive_loss_periods,
    _items,
    _text,
    _unique_ids,
    money,
    parse_money,
    safe_reference,
)

REVIEW_VERSION = "tax049-related-party-v1"
PERSISTENT_LOSS_PERIODS = 3  # same prototype threshold as TAX-048 indicators
MAX_RELATED_CONTRACTS = 25

# Conclusions this review must never emit; humans and specialists decide.
PROHIBITED_CONCLUSIONS = frozenset(
    {
        "transfer_pricing_violation",
        "fraudulent_intent",
        "proven_abuse",
        "service_not_rendered_established",
        "tax_assessment",
        "penalty",
        "refund_denial",
    }
)
# Neutral prompts for the taxpayer; none presumes a reason for the losses.
LOSS_EXPLANATION_PROMPTS = (
    "start-up or expansion costs",
    "capital investment or depreciation",
    "seasonal or one-off trading conditions",
    "pricing or market pressure",
    "group restructuring or shared-cost arrangements",
)


def _normalise(text: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() if isinstance(text, str) else ""


def _id_list(item: Mapping[str, object], key: str) -> list[str]:
    value = item.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise RefundDataError("invalid_record", f"{key} must be a list of evidence IDs")
    return list(value)


def _question(
    question_id: str, topic: str, evidence_ids: list[str], text: str, records: list[str]
) -> dict[str, object]:
    return {
        "question_id": question_id,
        "topic": topic,
        "addressed_to": "taxpayer",
        "evidence_ids": evidence_ids,
        "text": text,
        "requested_records": records,
        "status": "draft_requires_officer_approval",
    }


class _Review:
    """Accumulates cited results; refuses citations that do not resolve in the case."""

    def __init__(self, known_ids: frozenset[str]) -> None:
        self.known_ids = known_ids
        self.observations: list[dict[str, object]] = []
        self.counter_evidence: list[dict[str, object]] = []
        self.gaps: list[dict[str, object]] = []
        self.discrepancies: list[dict[str, object]] = []
        self.questions: list[dict[str, object]] = []

    def cite(self, evidence_ids: list[str]) -> list[str]:
        unknown = sorted(set(evidence_ids) - self.known_ids)
        if unknown:
            raise RefundDataError("unresolved_citation", "Cited evidence is not in the case")
        return list(dict.fromkeys(evidence_ids))

    def add(
        self, bucket: list[dict[str, object]], code: str, evidence_ids: list[str], **extra: object
    ) -> dict[str, object]:
        entry: dict[str, object] = {"code": code, "evidence_ids": self.cite(evidence_ids), **extra}
        bucket.append(entry)
        return entry


def _supporting(
    review: _Review,
    contract: Mapping[str, object],
    invoices: list[tuple[str, Mapping[str, object]]],
    key: str,
) -> tuple[list[str], list[str]]:
    """Return (resolvable, unresolvable) supporting document IDs for one test."""
    cited = _id_list(contract, key)
    for _, invoice in invoices:
        cited.extend(_id_list(invoice, key))
    cited = list(dict.fromkeys(cited))
    found = [item for item in cited if item in review.known_ids]
    return found, [item for item in cited if item not in review.known_ids]


def _test_contract(
    review: _Review,
    contract_id: str,
    contract: Mapping[str, object],
    contracts: Mapping[str, Mapping[str, object]],
    invoices: list[tuple[str, Mapping[str, object]]],
    payments: Mapping[str, list[tuple[str, Mapping[str, object]]]],
    *,
    period: str,
    currency: str,
) -> dict[str, object]:
    # The counterparty ID is untrusted record text: only a well-formed identifier
    # is repeated in draft questions or the output (TAX-056 finding F3).
    counterparty = safe_reference(contract.get("counterparty_id"))
    party = counterparty or "the related party"
    invoice_ids = [invoice_id for invoice_id, _ in invoices]
    base = [contract_id, *invoice_ids]
    tests: list[dict[str, object]] = []

    # Relationship: what is disclosed and what documents it.
    relationship = contract.get("relationship")
    rel_evidence = [contract_id]
    rel_type = "disclosed_related_party"
    if isinstance(relationship, Mapping):
        cited = _id_list(relationship, "evidence_ids")
        rel_evidence += [item for item in cited if item in review.known_ids]
        missing = [item for item in cited if item not in review.known_ids]
        if missing:
            review.add(
                review.gaps, "cited_evidence_not_supplied", [contract_id], missing_ids=missing
            )
        if isinstance(relationship.get("type"), str):
            rel_type = str(relationship["type"])
    review.add(review.observations, "related_party_transaction", [*rel_evidence, *invoice_ids])
    tests.append(
        {
            "test": "relationship",
            "result": "observed",
            "relationship_type": rel_type,
            "evidence_ids": review.cite(rel_evidence),
        }
    )

    # Service delivery and benefit: supported, or an evidence gap with a question.
    for test, key, gap_code, topic, text, records in (
        (
            "service_delivery",
            "delivery_evidence_ids",
            "service_delivery_evidence_missing",
            "service_delivery",
            f"Please provide records showing the services under {contract_id} that {party} "
            f"delivered during {period}, such as deliverables, activity logs or service reports.",
            ["deliverables or service reports", "activity or time records"],
        ),
        (
            "benefit",
            "benefit_evidence_ids",
            "service_benefit_evidence_missing",
            "service_benefit",
            f"Please describe how the business used the services billed under {contract_id} "
            "and provide any records showing that use.",
            ["internal requests or approvals", "records of how the services were used"],
        ),
    ):
        found, missing = _supporting(review, contract, invoices, key)
        if found:
            review.add(review.counter_evidence, f"{test}_documented", found)
            tests.append({"test": test, "result": "supported", "evidence_ids": found})
        else:
            review.add(review.gaps, gap_code, base)
            review.questions.append(_question(f"{contract_id}:{topic}", topic, base, text, records))
            tests.append({"test": test, "result": "evidence_gap", "evidence_ids": base})
        if missing:
            review.add(review.gaps, "cited_evidence_not_supplied", base, missing_ids=missing)

    # Duplication: same service category/description elsewhere, or repeated billing.
    category = _normalise(contract.get("service_category")) or _normalise(
        contract.get("description")
    )
    overlapping = [
        other_id
        for other_id, other in contracts.items()
        if other_id != contract_id
        and category
        and category
        in (_normalise(other.get("service_category")), _normalise(other.get("description")))
    ]
    seen: dict[tuple[Decimal, Decimal], str] = {}
    repeated: list[str] = []
    for invoice_id, invoice in invoices:
        net = parse_money(invoice.get("net_amount"), field=f"invoice {invoice_id}.net_amount")
        vat = parse_money(invoice.get("vat_amount"), field=f"invoice {invoice_id}.vat_amount")
        if (net, vat) in seen:
            repeated += [seen[(net, vat)], invoice_id]
        seen.setdefault((net, vat), invoice_id)
    if overlapping or repeated:
        evidence = [contract_id, *overlapping, *dict.fromkeys(repeated)]
        review.add(review.observations, "possible_service_duplication", evidence)
        review.questions.append(
            _question(
                f"{contract_id}:duplication",
                "duplication",
                review.cite(evidence),
                f"Please explain how the services under {contract_id} differ from the other "
                "services or invoices listed, and provide any scope documents.",
                ["scope of work for each arrangement"],
            )
        )
        tests.append({"test": "duplication", "result": "needs_review", "evidence_ids": evidence})
    else:
        compared = [contract_id, *(other for other in contracts if other != contract_id)]
        review.add(review.counter_evidence, "no_duplication_observed", compared)
        tests.append({"test": "duplication", "result": "none_observed", "evidence_ids": compared})

    # Payment trail for each related-party invoice.
    for invoice_id, invoice in invoices:
        gross = parse_money(invoice.get("gross_amount"), field=f"invoice {invoice_id}.gross_amount")
        paid_items = payments.get(invoice_id, [])
        paid = sum(
            (parse_money(p.get("amount"), field=f"payment {pid}.amount") for pid, p in paid_items),
            Decimal("0.00"),
        )
        evidence = [invoice_id, *(pid for pid, _ in paid_items)]
        if not paid_items:
            review.add(review.gaps, "payment_evidence_missing", evidence)
            review.questions.append(
                _question(
                    f"{invoice_id}:payment",
                    "payment",
                    evidence,
                    f"Please provide proof of payment for invoice {invoice_id} to {party}.",
                    ["bank statement or remittance advice"],
                )
            )
            result = "evidence_gap"
        elif paid != gross:
            review.add(
                review.discrepancies,
                "payment_total_differs_from_invoice",
                evidence,
                invoice_gross=money(gross, currency),
                paid=money(paid, currency),
            )
            result = "discrepancy"
        else:
            review.add(review.counter_evidence, "payment_trail_consistent", evidence)
            result = "consistent"
        tests.append(
            {
                "test": "payment",
                "invoice_id": invoice_id,
                "result": result,
                "evidence_ids": evidence,
            }
        )

    return {
        "contract_id": contract_id,
        "counterparty_id": counterparty,
        "invoice_ids": invoice_ids,
        "tests": tests,
    }


def review_related_party(
    record: Mapping[str, object], *, currency: str = DEFAULT_CURRENCY
) -> dict[str, object]:
    """Test related-party services and loss explanations; raise ``RefundDataError`` if unsafe."""
    if record.get("currency", currency) != currency:
        raise RefundDataError(
            "currency_mismatch", "Record currency differs from requested currency"
        )
    vat_return = record.get("return")
    if not isinstance(vat_return, Mapping):
        raise RefundDataError("invalid_record", "return must be an object")
    return_id = _text(vat_return, "id", field="return")
    period = record.get("period")
    period_label = period if isinstance(period, str) and period else "the period under review"
    contracts = _unique_ids(_items(record, "contracts"), "contract")
    invoices = _unique_ids(_items(record, "invoices"), "invoice")
    payments = _unique_ids(_items(record, "payments"), "payment")
    documents = _unique_ids(_items(record, "documents"), "document")
    known = frozenset({return_id, *contracts, *invoices, *payments, *documents})
    review = _Review(known)

    by_invoice: dict[str, list[tuple[str, Mapping[str, object]]]] = {}
    for payment_id, payment in payments.items():
        by_invoice.setdefault(_text(payment, "invoice_id", field="payment"), []).append(
            (payment_id, payment)
        )
    related = [(cid, c) for cid, c in contracts.items() if c.get("related_party") is True]
    if len(related) > MAX_RELATED_CONTRACTS:
        raise RefundDataError("contract_budget_exceeded", "Too many related-party contracts")
    relationships = [
        _test_contract(
            review,
            contract_id,
            contract,
            contracts,
            [(iid, inv) for iid, inv in invoices.items() if inv.get("contract_id") == contract_id],
            by_invoice,
            period=period_label,
            currency=currency,
        )
        for contract_id, contract in related
    ]

    loss_review: dict[str, object] = {"persistent_loss": False, "consecutive_periods": 0}
    streak = _consecutive_loss_periods(record.get("loss_periods"))
    if streak >= PERSISTENT_LOSS_PERIODS:
        review.add(review.observations, "consecutive_loss_periods", [return_id], periods=streak)
        explanations = record.get("loss_explanations", [])
        if not isinstance(explanations, list) or not all(
            isinstance(e, Mapping) for e in explanations
        ):
            raise RefundDataError("invalid_record", "loss_explanations must be a list of objects")
        documented = []
        for explanation in explanations:
            cited = _id_list(explanation, "evidence_ids")
            evidence = [item for item in cited if item in review.known_ids]
            missing = [item for item in cited if item not in review.known_ids]
            if missing:
                review.add(
                    review.gaps, "cited_evidence_not_supplied", [return_id], missing_ids=missing
                )
            if not evidence:
                continue  # an undocumented explanation is not counter-evidence
            code = explanation.get("code")
            documented.append(
                review.add(
                    review.counter_evidence,
                    "documented_alternative_explanation",
                    evidence,
                    explanation=code if isinstance(code, str) else "unspecified",
                )
            )
        if not documented:
            review.add(review.gaps, "loss_explanation_not_documented", [return_id])
            review.questions.append(
                _question(
                    f"{return_id}:loss_explanation",
                    "loss_explanation",
                    [return_id],
                    f"The returns show losses in {streak} consecutive periods up to "
                    f"{period_label}. Please explain the main reasons and provide supporting "
                    "records. Examples might include " + ", ".join(LOSS_EXPLANATION_PROMPTS) + ".",
                    [
                        "management accounts",
                        "board or budget papers",
                        "records supporting the reasons",
                    ],
                )
            )
        loss_review = {
            "persistent_loss": True,
            "consecutive_periods": streak,
            "documented_explanations": len(documented),
        }

    codes = {
        str(entry["code"]) for entry in [*review.observations, *review.gaps, *review.discrepancies]
    }
    if codes & PROHIBITED_CONCLUSIONS:  # pragma: no cover - guarded by construction
        raise RefundDataError("prohibited_conclusion", "Review produced a prohibited conclusion")

    return {
        "case_ref": record.get("case_id"),
        "synthetic": record.get("synthetic") is True,
        "method": "deterministic_record_tests",
        "review_version": REVIEW_VERSION,
        "relationships": relationships,
        "loss_review": loss_review,
        "observations": review.observations,
        "counter_evidence": review.counter_evidence,
        "evidence_gaps": review.gaps,
        "discrepancies": review.discrepancies,
        "evidence_questions": review.questions,
        "legal_conclusion": None,
        "enforcement_conclusion": None,
    }
