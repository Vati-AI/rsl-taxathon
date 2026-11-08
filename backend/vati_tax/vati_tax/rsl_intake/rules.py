"""Pure rules for reading RSL refund claims into Vati cases (TAX-122, ADR-043).

No Frappe import, so the rules are testable without a bench; ``store`` applies them.

A claim is filed in RSL's Online Refunds Application. Vati reads it through the TAX-111
contract ``rsl.online_refunds.claim`` (fixture until RSL supplies a specification) and
records one ``Vati Submission`` (source ``rsl_online_refunds``) and one ``Vati Refund
Claim`` per RSL ``claim_id``. The existing assurance, case-registration and evidence
steps then run exactly as for any sent claim.

What Vati keeps is a reference, not a copy: the RSL claim ID, the contract, the record's
SHA-256 and the IDs of the claim lines and documents RSL holds. The checks read the lines
from RSL when they run. A second read of the same record replays; a different record
under the same claim ID is a conflict and changes nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Final

SYSTEM: Final = "online_refunds"
KIND: Final = "claim"
CONTRACT_PREFIX: Final = "rsl.online_refunds.claim."
SOURCE: Final = "rsl_online_refunds"  # submissions.domain.SOURCE_RSL_ONLINE_REFUNDS
KEY_PREFIX: Final = "rsl:online-refunds:"
CLAIM_REASON: Final = "other"
CENT: Final = Decimal("0.01")

# Outcomes per claim and per run.
CREATED: Final = "created"
REPLAYED: Final = "replayed"
CONFLICT: Final = "conflict"
REFUSED: Final = "refused"
COMPLETED: Final = "completed"
PARTIAL: Final = "partial"
UNAVAILABLE: Final = "unavailable"


class InvalidIntake(ValueError):
    """An RSL envelope or record cannot be taken in; carries a stable reason code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class IntakeClaim:
    """One RSL claim, ready to record."""

    claim_ref: str
    tin: str
    period: str
    claimed_amount: str
    contract_id: str
    record_sha256: str
    idempotency_key: str
    fixture: bool
    source_refs: dict[str, Any] = field(default_factory=dict)
    refused: str | None = None  # a reason code when the claim cannot be recorded


def idempotency_key(claim_ref: str) -> str:
    """The submission's idempotency key: one Vati submission per RSL claim ID."""
    return KEY_PREFIX + claim_ref


def explanation(claim_ref: str) -> str:
    """The claim's explanation: the reason itself stays in RSL's record."""
    return (
        f"Filed in RSL's Online Refunds Application as claim {claim_ref}. "
        "The claim's reason and lines are held in RSL's record."
    )


def _amount(value: object) -> str | None:
    try:
        amount = Decimal(str(value)).quantize(CENT)
    except (InvalidOperation, ValueError):
        return None
    return format(amount, "f")


def plan_claims(envelope: Mapping[str, Any], *, tin: str) -> tuple[str, list[IntakeClaim]]:
    """Read one taxpayer's Online Refunds envelope into claims to record.

    Returns ``(status, claims)``. ``status`` is ``unavailable`` (and no claims) when RSL's
    system did not answer: the caller reports it, never "no claims". Raises
    ``InvalidIntake`` for an envelope that is not an Online Refunds claim read, or for a
    record of another taxpayer.
    """
    if envelope.get("source_system") != SYSTEM or envelope.get("kind") != KIND:
        raise InvalidIntake("wrong_source", "envelope is not an Online Refunds claim read")
    contract_id = str(envelope.get("contract_id") or "")
    if not contract_id.startswith(CONTRACT_PREFIX):
        raise InvalidIntake("wrong_contract", "envelope does not name the claim contract")
    if str(envelope.get("taxpayer")) != tin:
        raise InvalidIntake("wrong_taxpayer", "envelope was read for another taxpayer")
    status = envelope.get("status")
    if status == UNAVAILABLE:
        return UNAVAILABLE, []
    if status != "ok":
        raise InvalidIntake("unknown_status", "envelope status is neither ok nor unavailable")
    fixture = envelope.get("fixture") is True
    digests = {
        str(ref.get("record_id")): str(ref.get("record_sha256") or "")
        for ref in envelope.get("references") or []
    }
    claims = []
    for record in envelope.get("records") or []:
        claim_ref = str(record.get("claim_id") or "")
        if str(record.get("tin")) != tin:
            raise InvalidIntake("wrong_taxpayer", "record belongs to another taxpayer")
        record_sha256 = digests.get(claim_ref, "")
        if not claim_ref or len(record_sha256) != 64:
            raise InvalidIntake("missing_reference", "record has no ID or reference digest")
        amount = _amount(record.get("claimed_amount"))
        refused = None
        if amount is None or Decimal(amount) <= 0:
            refused = "claimed_amount_not_positive"
        claims.append(
            IntakeClaim(
                claim_ref=claim_ref,
                tin=tin,
                period=str(record.get("period")),
                claimed_amount=amount or "0.00",
                contract_id=contract_id,
                record_sha256=record_sha256,
                idempotency_key=idempotency_key(claim_ref),
                fixture=fixture,
                source_refs=source_refs(envelope, record, record_sha256),
                refused=refused,
            )
        )
    return "ok", claims


def source_refs(
    envelope: Mapping[str, Any], record: Mapping[str, Any], record_sha256: str
) -> dict[str, Any]:
    """What RSL holds for the claim: IDs and hashes only, never the figures or files."""
    return {
        "source_system": SYSTEM,
        "record_id": str(record.get("claim_id")),
        "contract_id": envelope.get("contract_id"),
        "record_sha256": record_sha256,
        "retrieved_at": envelope.get("retrieved_at"),
        "return_id": record.get("return_id"),
        "submitted_on": record.get("submitted_on"),
        "fixture": envelope.get("fixture") is True,
        "adapter_id": envelope.get("adapter_id"),
        "adapter_version": envelope.get("adapter_version"),
        "lines": [
            {"line_id": line.get("line_id"), "kind": line.get("kind")}
            for line in record.get("lines") or []
        ],
        "documents": [
            {
                "document_id": doc.get("document_id"),
                "line_id": doc.get("line_id"),
                "kind": doc.get("kind"),
                "held_by": "RSL",
            }
            for doc in record.get("uploaded_documents") or []
        ],
    }


def replay_outcome(stored_sha256: str | None, claim: IntakeClaim) -> str:
    """A claim already recorded: the same record replays, a different one conflicts."""
    return REPLAYED if stored_sha256 == claim.record_sha256 else CONFLICT


def run_status(taxpayer_statuses: Iterable[str]) -> str:
    """``completed`` when every taxpayer's read answered, ``partial`` when some did not."""
    statuses = list(taxpayer_statuses)
    if statuses and all(s == UNAVAILABLE for s in statuses):
        return UNAVAILABLE
    return PARTIAL if UNAVAILABLE in statuses else COMPLETED
