"""Objection, appeal and reviewed-outcome rules (TAX-069), independent of Frappe.

A *challenge* disputes an approved TAX-046 decision. It is an ``objection`` to
the decision itself, or an ``appeal`` against the reviewed outcome of an
objection. A supervisor who neither proposed nor approved the decision reviews
it and records one outcome from a fixed list. The chain
``decision -> objection -> appeal -> final outcome`` stays linked and readable.

Nothing here rewrites history. The original decision, its evidence and its
ledger entries are never changed. Each challenge stores a digest of the
decision as it was when the challenge was lodged, so any later change would be
visible. An overturned outcome does not move the case in Jube or change the
decision. It asks for a new human decision through TAX-046 and raises a
review-only signal (TAX-059).

No objection or appeal deadline, statutory route or legal effect is modelled as
fact. RSL has not verified any, so every challenge carries
``legal_route_verified: false`` and an optional free-text route reference
supplied by a human.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from vati_tax.ids.canonical import (
    InvalidMapping,
    MappingError,
    canonical_uuid,
    optional_uuid,
    validate_idempotency_key,
)

Stage = Literal["objection", "appeal"]
STAGES: Final[tuple[Stage, ...]] = ("objection", "appeal")
STATUSES: Final[tuple[str, ...]] = ("lodged", "reviewed")

# Reviewed outcomes. None of them assesses tax, pays or denies a refund, or
# accuses anyone; they say whether the original decision stands.
OUTCOMES: Final[tuple[str, ...]] = (
    "upheld",  # the decision stands
    "partially_overturned",  # part of the decision does not stand
    "overturned",  # the decision does not stand
    "withdrawn",  # the taxpayer withdrew the challenge
)
OVERTURNED: Final[frozenset[str]] = frozenset({"overturned", "partially_overturned"})
# An appeal follows an objection the taxpayer is still dissatisfied with.
APPEALABLE: Final[frozenset[str]] = frozenset({"upheld", "partially_overturned"})

GROUNDS_MIN: Final = 20
GROUNDS_MAX: Final = 4000
REASON_MIN: Final = 20
REASON_MAX: Final = 4000
ROUTE_MAX: Final = 200
MAX_EVIDENCE: Final = 50

LEGAL_NOTE: Final = (
    "No objection or appeal deadline, route or legal effect has been verified by RSL. "
    "Recorded for review only; it does not change the original decision."
)


class ChallengeNotFound(MappingError):
    code = "not_found"
    http_status = 404


class ChallengeConflict(MappingError):
    code = "invalid_domain_input"
    http_status = 422


def utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(datetime.UTC).replace(microsecond=0)


def iso(value: datetime.datetime | None) -> str | None:
    return utc(value).isoformat().replace("+00:00", "Z") if value else None


def digest(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# The decision as an officer proposed and a supervisor approved it. Jube sync
# bookkeeping (attempts, retry state) is left out: it may legitimately change
# after a challenge is lodged without changing what was decided.
DECIDED_FIELDS: Final[tuple[str, ...]] = (
    "decision_id",
    "tenant_id",
    "case_id",
    "taxpayer_id",
    "status",
    "origin",
    "outcome",
    "rationale",
    "evidence_ids",
    "finding_ids",
    "rating",
    "proposed_by",
    "proposed_at",
    "reviewed_by",
    "reviewed_at",
    "review_reason",
)


def decision_digest(decision: Any) -> str:
    """SHA-256 over what was decided, so a later change to it is detectable."""
    return digest({name: getattr(decision, name) for name in DECIDED_FIELDS})


def required_text(body: Mapping[str, Any], field: str, low: int, high: int) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InvalidMapping(f"{field} is required.", field, "required")
    value = value.strip()
    if not low <= len(value) <= high:
        raise InvalidMapping(
            f"{field} must be {low} to {high} characters.", field, "invalid_length"
        )
    return value


_NS: Final = uuid.UUID("9b3f6c1e-0d2a-4e7b-8c55-1a2b3c4d0069")


def challenge_id(tenant_id: str, idempotency_key: str) -> str:
    return str(uuid.uuid5(_NS, f"{tenant_id}|{idempotency_key}"))


@dataclass(frozen=True)
class LodgeRequest:
    stage: Stage
    decision_id: str | None
    parent_challenge_id: str | None
    grounds: str
    evidence_ids: tuple[str, ...]
    route_reference: str | None
    idempotency_key: str

    @classmethod
    def build(cls, body: Mapping[str, Any], idempotency_key: str) -> LodgeRequest:
        stage = body.get("stage")
        if stage not in STAGES:
            raise InvalidMapping("stage must be objection or appeal.", "stage", "unknown_stage")
        decision = optional_uuid(body.get("decision_id"), "decision_id")
        parent = optional_uuid(body.get("parent_challenge_id"), "parent_challenge_id")
        if stage == "objection" and (decision is None or parent is not None):
            raise InvalidMapping(
                "An objection names the decision and no earlier challenge.",
                "decision_id",
                "required",
            )
        if stage == "appeal" and parent is None:
            raise InvalidMapping(
                "An appeal names the objection it follows.", "parent_challenge_id", "required"
            )
        raw = body.get("evidence_ids") or []
        if not isinstance(raw, (list, tuple)) or len(raw) > MAX_EVIDENCE:
            raise InvalidMapping("Invalid evidence list.", "evidence_ids", "invalid")
        ids = sorted({canonical_uuid(item, "evidence_ids") for item in raw})
        route = body.get("route_reference")
        if route is not None and (not isinstance(route, str) or len(route.strip()) > ROUTE_MAX):
            raise InvalidMapping("Invalid route reference.", "route_reference", "invalid")
        return cls(
            stage=stage,
            decision_id=decision,
            parent_challenge_id=parent,
            grounds=required_text(body, "grounds", GROUNDS_MIN, GROUNDS_MAX),
            evidence_ids=tuple(ids),
            route_reference=route.strip() or None if isinstance(route, str) else None,
            idempotency_key=validate_idempotency_key(idempotency_key),
        )

    def payload(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "decision_id": self.decision_id,
            "parent_challenge_id": self.parent_challenge_id,
            "grounds": self.grounds,
            "evidence_ids": list(self.evidence_ids),
            "route_reference": self.route_reference,
        }


@dataclass(frozen=True)
class ChallengeRecord:
    challenge_id: str
    tenant_id: str
    taxpayer_id: str
    case_id: str
    decision_id: str
    stage: Stage
    parent_challenge_id: str | None
    grounds: str
    evidence_ids: tuple[str, ...]
    route_reference: str | None
    decision_digest: str
    request_digest: str
    lodged_by: str
    lodger_role: str
    lodged_at: datetime.datetime
    status: str = "lodged"
    version: int = 1
    reviewed_by: str | None = None
    reviewed_at: datetime.datetime | None = None
    outcome: str | None = None
    review_reason: str | None = None


def check_reviewable(record: ChallengeRecord, expected_version: object) -> None:
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise InvalidMapping("expected_version must be an integer.", "expected_version", "required")
    if record.status != "lodged":
        raise ChallengeConflict(
            "The challenge has already been reviewed.", "challenge_id", "not_pending"
        )
    if expected_version != record.version:
        raise ChallengeConflict(
            "The challenge changed since you loaded it.", "expected_version", "stale_version"
        )


def check_outcome(value: object) -> str:
    if value not in OUTCOMES:
        raise InvalidMapping("Unknown outcome.", "outcome", "unknown_outcome")
    return str(value)


def challenge_view(record: ChallengeRecord, *, for_taxpayer: bool = False) -> dict[str, Any]:
    """A challenge as staff see it; the taxpayer view omits staff identities."""
    view: dict[str, Any] = {
        "challenge_id": record.challenge_id,
        "stage": record.stage,
        "decision_id": record.decision_id,
        "parent_challenge_id": record.parent_challenge_id,
        "case_id": record.case_id,
        "taxpayer_id": record.taxpayer_id,
        "grounds": record.grounds,
        "evidence_ids": list(record.evidence_ids),
        "status": record.status,
        "version": record.version,
        "lodged_at": iso(record.lodged_at),
        "outcome": record.outcome,
        "reviewed_at": iso(record.reviewed_at),
        "review_reason": record.review_reason,
        "route_reference": record.route_reference,
        "legal_route_verified": False,
        "legal_note": LEGAL_NOTE,
        "follow_up": "new_decision_required" if record.outcome in OVERTURNED else None,
    }
    if not for_taxpayer:
        view.update(
            {
                "lodged_by": record.lodged_by,
                "lodger_role": record.lodger_role,
                "reviewed_by": record.reviewed_by,
                "decision_digest": record.decision_digest,
            }
        )
    return view
