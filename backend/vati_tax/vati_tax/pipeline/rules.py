"""Rules for the TAX-087 event wiring (ADR-036). Pure functions, no Frappe import.

The pipeline after a taxpayer sends a claim (or evidence reaches a case):

1. ``case.registered`` (TAX-039 bridge, unchanged): Jube risk evaluation and case.
2. ``evidence.received``: one per evidence document linked to the case. The handler
   converts the stored original with Docling and records an extraction revision.
3. ``extraction.completed`` / ``extraction.failed``: one per conversion. The handler
   maps the case's Frappe records and evidence to FollowTheMoney entities, writes new
   ones to the site's OpenAleph collection and asks yente for candidates for every
   party not yet screened on the current dataset version.

Every step is replay-safe. Event keys are derived from the records, so enqueueing the
same step twice replays; ledgers (``Vati Entity Index``, ``Vati Screening Result``)
make indexing and screening skip work already done. This module holds the parts that
need no database: payloads, keys, the entity bundle and error classification.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from vati_tax.ids.canonical import InvalidMapping
from vati_tax.outbox import policy as outbox_policy

EVIDENCE_RECEIVED = "evidence.received"
EXTRACTION_COMPLETED = "extraction.completed"
EXTRACTION_FAILED = "extraction.failed"
SOURCE_FRAPPE = "frappe"
SOURCE_DOCLING = "docling"

EVIDENCE_DOCTYPE = "Vati Evidence"
BUNDLE_VERSION = "tax_entity_bundle.v1"
# Schemata yente matches (integration/yente/candidate_review.py QUERY_PROPERTIES).
SCREENED_SCHEMATA = frozenset({"Company", "Organization", "LegalEntity", "Person"})
EXTRACTED = frozenset({"success", "partial"})

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RECORD_ID = re.compile(r"[^A-Za-z0-9._:/-]")
_ERROR_CODE = re.compile(r"[^a-z0-9_]")
_TWO = Decimal("0.01")


def _uuid(value: object, field: str) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError
        canonical = str(uuid.UUID(value))
    except ValueError:
        raise InvalidMapping(f"{field} must be a UUID.", field) from None
    if canonical != value:
        raise InvalidMapping(f"{field} must be a canonical UUID.", field)
    return canonical


def _sha(value: object, field: str = "document_sha256") -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise InvalidMapping(f"{field} must be a SHA-256 hex digest.", field)
    return value


def _key(*parts: str) -> str:
    """An outbox idempotency key: stable for the same record, 16-255 safe characters."""
    return "vati-pipeline:" + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


# --- events -------------------------------------------------------------------------


def evidence_event(
    case_id: str, evidence_id: str, document_sha256: str
) -> tuple[str, dict[str, Any]]:
    """Key and payload of the ``evidence.received`` event for one document of a case."""
    evidence = _uuid(evidence_id, "evidence_id")
    sha = _sha(document_sha256)
    key = _key(EVIDENCE_RECEIVED, _uuid(case_id, "case_id"), evidence, sha)
    return key, {"evidence_id": evidence, "document_sha256": sha}


def parse_evidence_event(payload: Mapping[str, Any]) -> tuple[str, str]:
    if set(payload) != {"evidence_id", "document_sha256"}:
        raise InvalidMapping("Unexpected evidence.received payload.", "payload")
    return _uuid(payload["evidence_id"], "evidence_id"), _sha(payload["document_sha256"])


@dataclass(frozen=True)
class Settled:
    evidence_id: str
    extraction_id: str
    status: str  # success | partial | failure

    @property
    def event_type(self) -> str:
        return EXTRACTION_COMPLETED if self.status in EXTRACTED else EXTRACTION_FAILED


def settled_event(case_id: str, settled: Settled) -> tuple[str, str, dict[str, Any]]:
    """Type, key and payload of the event that follows one recorded extraction revision."""
    if settled.status not in (*EXTRACTED, "failure"):
        raise InvalidMapping("status must be success, partial or failure.", "status")
    evidence = _uuid(settled.evidence_id, "evidence_id")
    extraction = _uuid(settled.extraction_id, "extraction_id")
    key = _key(settled.event_type, _uuid(case_id, "case_id"), extraction)
    payload = {"evidence_id": evidence, "extraction_id": extraction, "status": settled.status}
    return settled.event_type, key, payload


def parse_settled_event(payload: Mapping[str, Any]) -> Settled:
    if set(payload) != {"evidence_id", "extraction_id", "status"}:
        raise InvalidMapping("Unexpected extraction payload.", "payload")
    status = payload["status"]
    if status not in (*EXTRACTED, "failure"):
        raise InvalidMapping("status must be success, partial or failure.", "status")
    return Settled(
        _uuid(payload["evidence_id"], "evidence_id"),
        _uuid(payload["extraction_id"], "extraction_id"),
        str(status),
    )


def claim_evidence_ids(evidence_ids: object, rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """Every evidence document a refund claim refers to: its list plus the schedule rows."""
    found: list[str] = []
    for value in evidence_ids if isinstance(evidence_ids, list) else []:
        if isinstance(value, str):
            found.append(value)
    for row in rows:
        for field in ("invoice_evidence_id", "contract_evidence_id"):
            value = row.get(field)
            if isinstance(value, str) and value:
                found.append(value)
    out: list[str] = []
    for value in found:
        try:
            canonical = _uuid(value, "evidence_id")
        except InvalidMapping:
            continue  # the claim controller already refused malformed references
        if canonical not in out:
            out.append(canonical)
    return out


# --- errors -------------------------------------------------------------------------


def error_code(service: str, exc: BaseException) -> str:
    """A short stored code for an adapter failure: ``<service>_<reason or code>``."""
    reason = str(getattr(exc, "reason", None) or getattr(exc, "code", None) or "error").lower()
    # ServiceNotConfigured reasons already name the service (``docling_not_configured``).
    raw = reason if reason.startswith(f"{service}_") else f"{service}_{reason}"
    code = _ERROR_CODE.sub("_", raw)[:64]
    return code if code[:1].isalpha() else "handler_error"


def classify(service: str, exc: BaseException) -> outbox_policy.DeliveryError:
    """Map an adapter error (code, reason, retryable) to an outbox delivery error.

    Only the code is kept: adapter messages can carry upstream text. A
    ``ServiceNotConfigured`` (TAX-085) is retryable, so an unconfigured or
    unreachable service retries with backoff and then dead-letters for an operator.
    """
    if isinstance(exc, outbox_policy.DeliveryError):
        return exc
    code = error_code(service, exc)
    if getattr(exc, "retryable", False):
        return outbox_policy.RetryableDeliveryError(code)
    return outbox_policy.PermanentDeliveryError(code)


# --- FollowTheMoney bundle ------------------------------------------------------------


def money(value: object) -> str:
    """A Frappe Currency value (float or string) as an exact two-decimal string."""
    try:
        amount = Decimal(str(value if value is not None else "0"))
    except InvalidOperation:
        raise InvalidMapping("Amount is not a number.", "amount") from None
    return str(amount.quantize(_TWO))


def record_id(value: str) -> str:
    """A bundle record ID (``[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}``) from a Frappe name."""
    cleaned = _RECORD_ID.sub("-", value.strip())[:128]
    if not cleaned or not cleaned[0].isalnum():
        cleaned = ("r" + cleaned)[:128]
    return cleaned


def supplier_id(name: str, tin: str | None) -> str:
    """One party per supplier name and TIN, stable across claims of the tenant."""
    raw = f"{name.strip().casefold()}\x1f{(tin or '').strip()}"
    return "supplier:" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _party_kind(taxpayer_type: str | None) -> str:
    if taxpayer_type == "individual":
        return "person"
    if taxpayer_type == "company":
        return "company"
    return "legal_entity"


def _source(record_type: str, name: str) -> dict[str, str]:
    return {"system": "frappe", "record_type": record_type, "record_id": record_id(name)}


def _ref(evidence: Mapping[str, str], evidence_id: str | None, record: str) -> list[dict[str, Any]]:
    if not evidence_id or evidence_id not in evidence:
        return []
    return [
        {
            "evidence_id": evidence_id,
            "document_sha256": evidence[evidence_id],
            "locator": {"page": None, "record": record[:255]},
        }
    ]


def claim_bundle(
    *,
    tenant_id: str,
    taxpayer: Mapping[str, Any],
    currency: str,
    rows: Iterable[Mapping[str, Any]],
    evidence: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """A ``tax_entity_bundle.v1`` (integration/followthemoney) for one refund claim.

    ``taxpayer`` is the ``Vati Taxpayer`` (name, display_name, taxpayer_type, tin);
    ``rows`` the claim's purchase-invoice schedule; ``evidence`` the case's documents
    (evidence_id, document_sha256, media_type, size_bytes, original_filename,
    document_type, received_at). Invoices cite their invoice and contract documents
    by SHA-256 so every mapped amount points back to the stored original.
    """
    docs = sorted(evidence, key=lambda e: str(e["evidence_id"]))
    hashes = {str(e["evidence_id"]): str(e["document_sha256"]) for e in docs}
    taxpayer_party = record_id(str(taxpayer["name"]))
    parties: dict[str, dict[str, Any]] = {
        taxpayer_party: {
            "id": taxpayer_party,
            "kind": _party_kind(taxpayer.get("taxpayer_type")),
            "name": str(taxpayer["display_name"]),
            "role": "taxpayer",
            "country": "ls",
            "tax_number": taxpayer.get("tin") or None,
            "source": _source("vati_taxpayer", str(taxpayer["name"])),
        }
    }
    invoices: list[dict[str, Any]] = []
    payments: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: int(r.get("idx") or 0)):
        supplier = supplier_id(str(row["supplier_name"]), row.get("supplier_tin"))
        parties.setdefault(
            supplier,
            {
                "id": supplier,
                "kind": "legal_entity",
                "name": str(row["supplier_name"]),
                "role": "counterparty",
                "tax_number": row.get("supplier_tin") or None,
                "source": _source("vati_refund_claim_invoice", str(row["name"])),
            },
        )
        invoice = record_id(str(row["name"]))
        number = f"invoice:{row.get('invoice_number') or invoice}"
        citations = _ref(hashes, row.get("invoice_evidence_id"), number)
        if row.get("contract_evidence_id") != row.get("invoice_evidence_id"):
            citations += _ref(hashes, row.get("contract_evidence_id"), number)
        invoices.append(
            {
                "id": invoice,
                "direction": "purchase",
                "supplier_id": supplier,
                "buyer_id": taxpayer_party,
                "currency": currency,
                "net_amount": money(row.get("net_amount")),
                "vat_amount": money(row.get("vat_amount")),
                "gross_amount": money(row.get("gross_amount")),
                "issue_date": str(row["invoice_date"])[:10] if row.get("invoice_date") else None,
                "evidence": citations,
                "source": _source("vati_refund_claim_invoice", str(row["name"])),
            }
        )
        if row.get("amount_paid") not in (None, ""):
            payments.append(
                {
                    "id": record_id(f"{row['name']}:payment"),
                    "invoice_id": invoice,
                    "payer_id": taxpayer_party,
                    "beneficiary_id": supplier,
                    "amount": money(row.get("amount_paid")),
                    "currency": currency,
                    "evidence": citations,
                    "source": _source("vati_refund_claim_invoice", str(row["name"])),
                }
            )
    return {
        "version": BUNDLE_VERSION,
        "tenant_id": tenant_id,
        "parties": [parties[key] for key in sorted(parties)],
        "contracts": [],
        "invoices": invoices,
        "payments": payments,
        "ownership": [],
        "evidence": [
            {
                "evidence_id": str(e["evidence_id"]),
                "document_sha256": str(e["document_sha256"]),
                "media_type": str(e["media_type"]),
                "size": int(e["size_bytes"]),
                "file_name": str(e.get("original_filename") or e["evidence_id"]),
                "title": str(e.get("document_type") or "") or None,
                "received_on": str(e["received_at"])[:10] if e.get("received_at") else None,
            }
            for e in docs
        ],
    }


# --- ledgers ------------------------------------------------------------------------


def index_key(tenant_id: str, collection_id: int, entity_id: str) -> str:
    """Unique key of one entity written to one OpenAleph collection of a tenant."""
    return hashlib.sha256(
        f"index\x1f{tenant_id}\x1f{collection_id}\x1f{entity_id}".encode()
    ).hexdigest()


def screening_key(tenant_id: str, case_id: str, entity_id: str, dataset: str, version: str) -> str:
    """Unique key of one party screened for one case on one yente dataset version."""
    raw = f"screen\x1f{tenant_id}\x1f{case_id}\x1f{entity_id}\x1f{dataset}\x1f{version}"
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class IndexPlan:
    """Entities to send, already written unchanged, or changed since they were written."""

    new: list[dict[str, Any]]
    unchanged: list[str]
    changed: list[str]


def index_plan(
    entities: Iterable[Mapping[str, Any]], digests: Mapping[str, str], ledger: Mapping[str, str]
) -> IndexPlan:
    """Split mapped entities against the collection ledger (same rule as entity_ingest.plan).

    A changed entity is never re-sent: OpenAleph needs a reviewed re-index for it
    (TAX-036). It is reported to operators instead of blocking the rest of the case.
    """
    new: list[dict[str, Any]] = []
    unchanged: list[str] = []
    changed: list[str] = []
    for entity in entities:
        entity_id = str(entity["id"])
        previous = ledger.get(entity_id)
        if previous is None:
            new.append(dict(entity))
        elif previous == digests[entity_id]:
            unchanged.append(entity_id)
        else:
            changed.append(entity_id)
    return IndexPlan(new, sorted(unchanged), sorted(changed))


def screening_targets(entities: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Party entities yente can match, in a stable order."""
    return sorted(
        (dict(e) for e in entities if e.get("schema") in SCREENED_SCHEMATA),
        key=lambda e: str(e["id"]),
    )


def document_entities(entities: Iterable[Mapping[str, Any]]) -> list[str]:
    """IDs of the FtM ``Document`` entities (one per evidence document)."""
    return sorted(str(e["id"]) for e in entities if e.get("schema") == "Document")
