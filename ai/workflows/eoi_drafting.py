"""EOI evidence-gap and request drafting (TAX-071). Drafts only; nothing is sent.

Builds an exchange-of-information (EOI) *draft package* for competent-authority
review from one case's records:

- foreign counterparties come from the TAX-050 imported-services review (explicit
  ``supplier_residence: non_resident`` or the TAX-024 treatment marker; never inferred
  from names), with an optional explicit ``supplier_jurisdiction`` (ISO 3166 alpha-2);
- every domestic fact in the package cites a record that exists in the case
  (contract, invoice, payment, return);
- missing foreign facts are listed per counterparty with the reason they matter,
  skipping facts the domestic records already establish;
- open domestic questions (TAX-050 taxpayer questions) are listed as outstanding,
  because a foreign request normally follows domestic means;
- the legal basis is looked up in the TAX-052 registry for ``eoi_request``; without a
  reviewer-verified route it is reported as unverified/no route for the competent
  authority to decide. The package never asserts a treaty or instrument.

There is no transmission capability: the package is ``status: draft`` with
``transmission.enabled: False``, ``transmit`` always refuses, and this module imports
no network library. TAX-072 owns approval, lawful channel references and responses.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date
from typing import Final

from ai.tools.evidence_search import check_read_access
from ai.tools.imported_services import review_imported_services
from ai.workflows.legal_routes import LegalRouteRegistry

DRAFT_VERSION: Final = "tax071-eoi-draft-v1"
LEGAL_BASIS_CODE: Final = "eoi_request"
_COUNTRY = re.compile(r"[A-Z]{2}")
NOTICE: Final = (
    "Draft for competent-authority review only. It has not been sent and cannot be sent "
    "from this system. It identifies missing foreign facts; it does not allege wrongdoing "
    "by the taxpayer or the foreign party, and the legal basis must be confirmed by the "
    "competent authority."
)
# Foreign facts typically only obtainable from the other jurisdiction (neutral wording).
FOREIGN_FACTS: Final[tuple[tuple[str, str], ...]] = (
    (
        "supplier_identity_and_registration",
        "Legal name, registration and tax identification of the supplier",
    ),
    ("supplier_tax_residence", "Tax residence of the supplier during the period"),
    (
        "services_performed_and_location",
        "Nature of the services performed and where they were performed",
    ),
    ("consideration_received", "Amount the supplier recorded as received for these services"),
    (
        "relationship_to_taxpayer",
        "Any ownership or control relationship between the supplier and the taxpayer",
    ),
)


class EoiDraftError(ValueError):
    """Input is unsafe to draft from, or a forbidden action was requested."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _known_ids(record: Mapping[str, object]) -> set[str]:
    ids: set[str] = set()
    vat_return = record.get("return")
    if isinstance(vat_return, Mapping) and isinstance(vat_return.get("id"), str):
        ids.add(str(vat_return["id"]))
    for key in ("contracts", "invoices", "payments", "documents"):
        items = record.get(key)
        for item in items if isinstance(items, list) else []:
            if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                ids.add(str(item["id"]))
    return ids


def _by_id(record: Mapping[str, object], key: str) -> dict[str, Mapping[str, object]]:
    items = record.get(key)
    return {
        str(i["id"]): i
        for i in (items if isinstance(items, list) else [])
        if isinstance(i, Mapping) and isinstance(i.get("id"), str)
    }


def _legal_basis(registry: LegalRouteRegistry, as_of: date) -> dict[str, object]:
    routes = registry.routes_for(LEGAL_BASIS_CODE, as_of)
    verified = [r for r in routes if r.is_verified]
    if verified:
        status = "verified_route_for_human_review"
    elif routes:
        status = "unverified_route"
    else:
        status = "no_verified_route"
    return {
        "status": status,
        "authorities": [r.audit_record(registry.registry_version) for r in routes],
        "instrument": None,
        "note": "The competent authority must confirm the applicable treaty or agreement "
        "and the lawful channel; this draft does not assert one.",
    }


def draft_eoi_package(
    record: Mapping[str, object],
    *,
    actor: Mapping[str, object],
    tenant_id: str,
    registry: LegalRouteRegistry,
    as_of: date,
) -> dict[str, object] | None:
    """Return a draft EOI package for the case, or ``None`` if no foreign party is involved."""
    check_read_access(actor, tenant_id)  # refuses before any record is read
    if record.get("tenant_id", tenant_id) != tenant_id:
        raise EoiDraftError("tenant_mismatch")
    case_id = record.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise EoiDraftError("invalid_case")
    review = review_imported_services(record)
    charges = review.get("charges")
    if not isinstance(charges, list) or not charges:
        return None
    known = _known_ids(record)
    contracts = _by_id(record, "contracts")
    invoices = _by_id(record, "invoices")

    jurisdictions: dict[str, str | None] = {}
    transactions: dict[str, list[dict[str, object]]] = {}
    cited: dict[str, list[str]] = {}
    contract_ids: dict[str, list[str]] = {}
    outstanding_domestic: list[dict[str, object]] = []
    for charge in charges:
        if not isinstance(charge, Mapping):
            raise EoiDraftError("invalid_review")
        supplier = charge.get("supplier_id")
        invoice_id = str(charge.get("invoice_id"))
        if not isinstance(supplier, str) or not supplier:
            raise EoiDraftError("supplier_unidentified")
        raw_evidence = charge.get("evidence_ids")
        evidence = [
            str(e)
            for e in (raw_evidence if isinstance(raw_evidence, list) else [])
            if str(e) in known
        ]
        if invoice_id not in evidence:
            raise EoiDraftError("unresolved_citation")
        invoice = invoices.get(invoice_id, {})
        contract_id = charge.get("contract_id")
        contract = contracts.get(str(contract_id), {}) if contract_id else {}
        declared = invoice.get("supplier_jurisdiction") or contract.get("supplier_jurisdiction")
        if supplier not in jurisdictions or jurisdictions[supplier] is None:
            jurisdictions[supplier] = (
                declared if isinstance(declared, str) and _COUNTRY.fullmatch(declared) else None
            )
        transactions.setdefault(supplier, []).append(
            {
                "invoice_id": invoice_id,
                "contract_id": contract_id,
                "payment_ids": charge.get("payment_ids", []),
                "gross": charge.get("gross"),
                "paid": charge.get("paid"),
                "trail_status": charge.get("trail_status"),
            }
        )
        if isinstance(contract_id, str):
            contract_ids.setdefault(supplier, []).append(contract_id)
        mine = cited.setdefault(supplier, [])
        mine.extend(e for e in evidence if e not in mine)
        questions = charge.get("evidence_questions")
        for question in questions if isinstance(questions, list) else []:
            if isinstance(question, Mapping):
                outstanding_domestic.append(
                    {
                        "question_id": question.get("question_id"),
                        "topic": question.get("topic"),
                        "addressed_to": "taxpayer",
                        "evidence_ids": question.get("evidence_ids"),
                    }
                )

    counterparties: dict[str, dict[str, object]] = {}
    for supplier in sorted(transactions):
        located = bool(contract_ids.get(supplier)) and all(
            contracts.get(cid, {}).get("service_location") for cid in contract_ids[supplier]
        )
        facts: list[dict[str, object]] = []
        if jurisdictions.get(supplier) is None:
            facts.append(
                {
                    "fact": "supplier_jurisdiction",
                    "description": "Jurisdiction in which the supplier is established",
                    "relates_to": cited[supplier],
                    "status": "missing",
                }
            )
        for code, description in FOREIGN_FACTS:
            if code == "services_performed_and_location" and located:
                continue  # already established domestically
            facts.append(
                {
                    "fact": code,
                    "description": description,
                    "relates_to": cited[supplier],
                    "status": "missing",
                }
            )
        counterparties[supplier] = {
            "counterparty_id": supplier,
            "jurisdiction": jurisdictions.get(supplier),
            "transactions": transactions[supplier],
            "domestic_evidence_ids": cited[supplier],
            "missing_foreign_facts": facts,
        }

    body = {"tenant_id": tenant_id, "case_id": case_id, "counterparties": sorted(counterparties)}
    package_id = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    return {
        "kind": "eoi_draft_package",
        "package_id": f"eoi-{package_id}",
        "draft_version": DRAFT_VERSION,
        "tenant_id": tenant_id,
        "case_id": case_id,
        "taxpayer_ref": record.get("taxpayer_id"),
        "period": record.get("period"),
        "status": "draft",
        "for_review_by": "competent_authority",
        "requires_human_approval": True,
        "approved_by": None,
        "transmission": {"enabled": False, "channel": None, "sent_at": None},
        "counterparties": list(counterparties.values()),
        "domestic_steps_outstanding": outstanding_domestic,
        "domestic_means_note": "Open taxpayer questions are listed; the competent authority "
        "decides whether domestic means are sufficiently exhausted before any request.",
        "legal_basis": _legal_basis(registry, as_of),
        "statutory_treatment": review.get("statutory_treatment"),
        "enforcement_conclusion": None,
        "notice": NOTICE,
    }


def transmit(package: Mapping[str, object]) -> None:
    """No transmission tool is enabled; sending is an authorised human action (TAX-072)."""
    raise EoiDraftError("transmission_not_enabled")
