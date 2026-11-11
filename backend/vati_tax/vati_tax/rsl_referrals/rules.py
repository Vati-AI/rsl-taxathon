"""Pure rules for RSL audit referrals (TAX-123, ADR-043 §1, ADR-047).

No Frappe import, so the rules are testable without a bench; ``store`` applies them.

RSL's risk-based audit system (on PSRM) selects a taxpayer for audit and refers the case.
Vati reads the referral through the assumed contract
``rsl.audit_system.referral.v0-assumed`` (a fixture until RSL supplies a specification)
and opens one Vati case for the RSL audit case reference, or reuses it when the same
referral is read again.

RSL's risk sets and their scores are RSL's own. They are stored as RSL-owned inputs
(``rsl_inputs``: every entry carries ``owner: RSL`` and the RSL risk-set ID and version),
never relabelled as a Vati score, finding or decision. What Vati keeps is a reference:
the audit case reference, the contract, the record's SHA-256 and the retrieval time.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

SYSTEM: Final = "audit_system"
KIND: Final = "referral"
CONTRACT_PREFIX: Final = "rsl.audit_system.referral."
OWNER: Final = "RSL"
KEY_PREFIX: Final = "rsl:audit-referral:"

# Outcomes per referral and per run (the same words as the TAX-122 claim intake).
CREATED: Final = "created"
REPLAYED: Final = "replayed"
CONFLICT: Final = "conflict"
REFUSED: Final = "refused"
COMPLETED: Final = "completed"
PARTIAL: Final = "partial"
UNAVAILABLE: Final = "unavailable"


class InvalidReferral(ValueError):
    """An RSL envelope or record cannot be taken in; carries a stable reason code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Referral:
    """One RSL audit referral, ready to record."""

    audit_case_ref: str
    tin: str
    referred_on: str
    selection_reason: str
    period: str | None
    rsl_inputs: dict[str, Any]
    contract_id: str
    record_sha256: str
    retrieved_at: str
    idempotency_key: str
    fixture: bool
    adapter_id: str | None


def idempotency_key(audit_case_ref: str) -> str:
    """The case registration key: one Vati case per RSL audit case reference."""
    return KEY_PREFIX + audit_case_ref


def rsl_inputs(record: Mapping[str, Any]) -> dict[str, Any]:
    """RSL's risk sets and scores, kept as RSL's inputs and labelled as RSL's.

    Raises ``InvalidReferral`` if any risk set is not owned by RSL: a score of any other
    owner is not an RSL input and is never taken in under RSL's name."""
    risk_sets = []
    for item in record.get("risk_sets") or []:
        if item.get("owner") != OWNER:
            raise InvalidReferral("score_not_rsl_owned", "a risk-set score is not owned by RSL")
        risk_sets.append(
            {
                "risk_set_id": str(item["risk_set_id"]),
                "risk_set_version": str(item["risk_set_version"]),
                "score": str(item["score"]),
                "owner": OWNER,
            }
        )
    return {
        "owner": OWNER,
        "source_system": SYSTEM,
        "selection_reason": str(record.get("selection_reason")),
        "risk_sets": sorted(risk_sets, key=lambda r: (r["risk_set_id"], r["risk_set_version"])),
        "note": "RSL's own selection inputs. Not a Vati score, finding or decision.",
    }


def plan_referrals(envelope: Mapping[str, Any], *, tin: str) -> tuple[str, list[Referral]]:
    """Read one taxpayer's audit-referral envelope into referrals to record.

    Returns ``(status, referrals)``. ``status`` is ``unavailable`` (and no referrals) when
    RSL's system did not answer: the caller reports it, never "no referrals". Raises
    ``InvalidReferral`` for an envelope that is not an audit-referral read, a record of
    another taxpayer, a record without a reference digest, or a score not owned by RSL.
    """
    if envelope.get("source_system") != SYSTEM or envelope.get("kind") != KIND:
        raise InvalidReferral("wrong_source", "envelope is not an audit-referral read")
    contract_id = str(envelope.get("contract_id") or "")
    if not contract_id.startswith(CONTRACT_PREFIX):
        raise InvalidReferral("wrong_contract", "envelope does not name the referral contract")
    if str(envelope.get("taxpayer")) != tin:
        raise InvalidReferral("wrong_taxpayer", "envelope was read for another taxpayer")
    status = envelope.get("status")
    if status == UNAVAILABLE:
        return UNAVAILABLE, []
    if status != "ok":
        raise InvalidReferral("unknown_status", "envelope status is neither ok nor unavailable")
    digests = {
        str(ref.get("record_id")): str(ref.get("record_sha256") or "")
        for ref in envelope.get("references") or []
    }
    referrals = []
    for record in envelope.get("records") or []:
        ref = str(record.get("audit_case_ref") or "")
        if str(record.get("tin")) != tin:
            raise InvalidReferral("wrong_taxpayer", "record belongs to another taxpayer")
        record_sha256 = digests.get(ref, "")
        if not ref or len(record_sha256) != 64:
            raise InvalidReferral("missing_reference", "record has no ID or reference digest")
        referrals.append(
            Referral(
                audit_case_ref=ref,
                tin=tin,
                referred_on=str(record.get("referred_on")),
                selection_reason=str(record.get("selection_reason")),
                period=str(record["period"]) if record.get("period") else None,
                rsl_inputs=rsl_inputs(record),
                contract_id=contract_id,
                record_sha256=record_sha256,
                retrieved_at=str(envelope.get("retrieved_at") or ""),
                idempotency_key=idempotency_key(ref),
                fixture=envelope.get("fixture") is True,
                adapter_id=envelope.get("adapter_id"),
            )
        )
    return "ok", referrals


def replay_outcome(stored_sha256: str | None, referral: Referral) -> str:
    """A referral already recorded: the same record replays, a different one conflicts."""
    return REPLAYED if stored_sha256 == referral.record_sha256 else CONFLICT


def run_status(taxpayer_statuses: Iterable[str]) -> str:
    """``completed`` when every taxpayer's read answered, ``partial`` when some did not."""
    statuses = list(taxpayer_statuses)
    if statuses and all(s == UNAVAILABLE for s in statuses):
        return UNAVAILABLE
    return PARTIAL if UNAVAILABLE in statuses else COMPLETED


def referral_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """An officer's view of a stored referral: RSL's reference and RSL-owned inputs."""
    return {
        "referral_id": row["referral_id"],
        "tenant_id": row["tenant_id"],
        "case_id": row["case_id"],
        "taxpayer_id": row["taxpayer_id"],
        "audit_case_ref": row["audit_case_ref"],
        "referred_on": str(row["referred_on"]),
        "selection_reason": row["selection_reason"],
        "period": row.get("period") or None,
        "rsl_inputs": row["rsl_inputs"],
        "source": {
            "source_system": SYSTEM,
            "contract_id": row["contract_id"],
            "record_sha256": row["record_sha256"],
            "retrieved_at": row["retrieved_at"],
            "fixture": bool(row["fixture"]),
            "adapter_id": row.get("adapter_id") or None,
        },
    }
