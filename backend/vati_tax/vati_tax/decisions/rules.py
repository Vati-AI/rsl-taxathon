"""Decision, approval and evidence-request rules (TAX-046).

Pure functions and value objects with no Frappe import, so they run under plain
``unittest``. ``engine`` applies them to a repository and the Jube case
lifecycle adapter (TAX-034); ``store`` binds the repository to Frappe.

Vocabulary:

- An officer *proposes* a decision on a case: an outcome from a fixed allowlist,
  a written rationale, cited evidence/findings and the Jube status they saw.
- A supervisor who did not propose it *approves* or *rejects* it. Only an
  approved decision is executed in Jube, once.
- A service identity may only record an agent *draft*. A draft is never
  approved or executed; an officer must propose a decision in their own words.
- An officer *authorises an evidence request* to the taxpayer
  (``RequestEvidenceRequest`` in integration/api/openapi.json).

Outcomes never assess tax, approve, deny or pay a refund, accuse anyone of
fraud or refer anyone for prosecution. Those are not in the allowlist, and an
outcome naming them is refused with its own reason.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from vati_tax.ids.canonical import (
    CaseNotFound,
    InvalidMapping,
    MappingConflict,
    MappingError,
    canonical_uuid,
    optional_uuid,
    validate_idempotency_key,
)

# integration/api/openapi.json components.schemas.CaseStatus
CASE_STATUSES: Final = (
    "RECEIVED",
    "VALIDATING",
    "INVESTIGATING",
    "AWAITING_EVIDENCE",
    "AWAITING_HUMAN",
    "RESOLVED",
    "FAILED_PROCESSING",
)
# integration/api/openapi.json components.schemas.DocumentType
DOCUMENT_TYPES: Final = (
    "invoice",
    "contract",
    "payslip",
    "import_declaration",
    "bank_statement",
    "financial_statement",
    "other",
)

Operation = Literal["transition", "escalate"]


@dataclass(frozen=True)
class OutcomeRule:
    operation: Operation
    target_status: str  # CaseStatus the Jube case projects to afterwards
    closes_case: bool = False


# The only outcomes a decision can carry, and the Jube operation each one runs.
OUTCOMES: Final[dict[str, OutcomeRule]] = {
    "close_no_further_action": OutcomeRule("transition", "RESOLVED", closes_case=True),
    "close_after_correction": OutcomeRule("transition", "RESOLVED", closes_case=True),
    "request_further_evidence": OutcomeRule("transition", "AWAITING_EVIDENCE"),
    "continue_investigation": OutcomeRule("transition", "INVESTIGATING"),
    "escalate_to_supervisor": OutcomeRule("escalate", "AWAITING_HUMAN"),
}
# Outcomes outside Vati's authority (AGENTS.md). Named separately so a caller
# gets a precise refusal instead of a generic "unknown outcome".
PROHIBITED_OUTCOME = re.compile(
    r"assess|refund|pay|penalt|fraud|crimin|prosecut|enforce|deny|denial|seiz|garnish",
    re.IGNORECASE,
)

DECISION_STATUSES: Final = ("draft", "proposed", "approved", "rejected", "withdrawn")
SYNC_STATES: Final = ("not_started", "pending", "synced", "failed")
ORIGINS: Final = ("officer", "agent_draft")

RATIONALE_MIN: Final = 20
RATIONALE_MAX: Final = 4000
REASON_MAX: Final = 2000
MAX_CITATIONS: Final = 50
MAX_ITEMS: Final = 20
ITEM_DESCRIPTION_MAX: Final = 1000
MESSAGE_MAX: Final = 2000
MAX_DUE_DAYS: Final = 180
# A proposal is approved against the case as it was when proposed. After this
# long the reviewer must look again, so approval is refused as stale.
MAX_PROPOSAL_AGE: Final = datetime.timedelta(hours=72)
# An approved decision that has not reached Jube in this time is not retried;
# the case may have moved on, so a new decision is needed.
MAX_APPROVAL_AGE: Final = datetime.timedelta(hours=24)


# --- errors -------------------------------------------------------------------------


class VersionConflict(MappingError):
    """Stale request: the case or decision changed since the caller read it."""

    code = "version_conflict"
    http_status = 409

    def __init__(
        self,
        message: str,
        field: str | None = None,
        reason: str | None = None,
        *,
        current_version: int | None = None,
    ) -> None:
        super().__init__(message, field, reason)
        self.current_version = current_version


class DecisionRefused(MappingConflict):
    """A well-formed request the decision rules do not allow (422)."""


class DecisionNotFound(CaseNotFound):
    """Missing decision or request, or one outside the caller's tenant."""


class UpstreamUnavailable(MappingError):
    code = "upstream_unavailable"
    http_status = 503
    retryable = True


# --- helpers ------------------------------------------------------------------------


def sha256_text(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def digest(payload: Mapping[str, Any]) -> str:
    """Stable SHA-256 of a request, for idempotent replay checks."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def operation_key(operation: str, tenant_id: str, idempotency_key: str) -> str:
    """Unique per tenant and operation, so one key cannot collide across operations."""
    return sha256_text(operation, tenant_id, validate_idempotency_key(idempotency_key))


def open_decision_key(tenant_id: str, case_id: str) -> str:
    """Held by the one open (proposed, or approved and not yet settled) decision of a case."""
    return sha256_text("open-decision", tenant_id, case_id)


def sequence_key(tenant_id: str, case_id: str, sequence: int) -> str:
    """Unique per case review step; two writers cannot claim the same case version."""
    return sha256_text("case-sequence", tenant_id, case_id, str(sequence))


def _text(value: object, field: str, *, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise InvalidMapping(f"{field} must be text.", field, "invalid_type")
    text = value.strip()
    if len(text) < minimum:
        raise InvalidMapping(f"{field} is too short.", field, "too_short")
    if len(text) > maximum:
        raise InvalidMapping(f"{field} is too long.", field, "too_long")
    return text


def _uuid_list(value: object, field: str, *, minimum: int = 0) -> tuple[str, ...]:
    if value is None:
        value = []
    if not isinstance(value, list | tuple):
        raise InvalidMapping(f"{field} must be a list of UUIDs.", field, "invalid_type")
    if len(value) < minimum:
        raise InvalidMapping(f"{field} needs at least {minimum} entry.", field, "too_short")
    if len(value) > MAX_CITATIONS:
        raise InvalidMapping(f"{field} has too many entries.", field, "too_long")
    ids = tuple(canonical_uuid(item, field) for item in value)
    if len(set(ids)) != len(ids):
        raise InvalidMapping(f"{field} repeats an entry.", field, "duplicate")
    return ids


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidMapping(f"{field} must be a positive integer.", field, "invalid_version")
    return value


def validate_case_status(value: object, field: str = "expected_case_status") -> str:
    if value not in CASE_STATUSES:
        raise InvalidMapping(f"{field} must be a case status.", field, "unknown_status")
    return str(value)


def validate_outcome(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidMapping("outcome is required.", "outcome", "invalid_type")
    if value in OUTCOMES:
        return value
    if PROHIBITED_OUTCOME.search(value):
        raise DecisionRefused(
            "Decisions cannot assess tax, decide or pay refunds, accuse or refer for prosecution.",
            "outcome",
            "prohibited_outcome",
        )
    raise InvalidMapping("Unknown outcome.", "outcome", "unknown_outcome")


def validate_rating(value: object) -> int:
    """Jube needs a 1-5 investigator rating on every case update (ADR-009)."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        raise InvalidMapping("rating must be an integer from 1 to 5.", "rating", "out_of_range")
    return value


# --- proposals ----------------------------------------------------------------------


@dataclass(frozen=True)
class Proposal:
    tenant_id: str
    case_id: str
    outcome: str
    rationale: str
    evidence_ids: tuple[str, ...]
    finding_ids: tuple[str, ...]
    rating: int
    expected_case_status: str
    expected_version: int
    draft_id: str | None
    idempotency_key: str

    @classmethod
    def build(
        cls,
        *,
        tenant_id: str,
        case_id: object,
        outcome: object,
        rationale: object,
        evidence_ids: object,
        finding_ids: object,
        rating: object,
        expected_case_status: object,
        expected_version: object,
        idempotency_key: object,
        draft_id: object = None,
    ) -> Proposal:
        outcome_name = validate_outcome(outcome)
        evidence = _uuid_list(evidence_ids, "evidence_ids")
        findings = _uuid_list(finding_ids, "finding_ids")
        if OUTCOMES[outcome_name].closes_case and not (evidence or findings):
            raise DecisionRefused(
                "Closing a case must cite at least one evidence document or finding.",
                "evidence_ids",
                "citation_required",
            )
        return cls(
            tenant_id=canonical_uuid(tenant_id, "tenant_id"),
            case_id=canonical_uuid(case_id, "case_id"),
            outcome=outcome_name,
            rationale=_text(rationale, "rationale", minimum=RATIONALE_MIN, maximum=RATIONALE_MAX),
            evidence_ids=evidence,
            finding_ids=findings,
            rating=validate_rating(rating),
            expected_case_status=validate_case_status(expected_case_status),
            expected_version=_positive_int(expected_version, "expected_version"),
            draft_id=optional_uuid(draft_id, "draft_id"),
            idempotency_key=validate_idempotency_key(idempotency_key),
        )

    def digest(self) -> str:
        return digest(
            {
                "case_id": self.case_id,
                "outcome": self.outcome,
                "rationale": self.rationale,
                "evidence_ids": list(self.evidence_ids),
                "finding_ids": list(self.finding_ids),
                "rating": self.rating,
                "expected_case_status": self.expected_case_status,
                "expected_version": self.expected_version,
                "draft_id": self.draft_id,
            }
        )


@dataclass(frozen=True)
class Draft:
    """An agent's suggested decision. Recorded for review, never executed."""

    tenant_id: str
    case_id: str
    outcome: str
    rationale: str
    evidence_ids: tuple[str, ...]
    finding_ids: tuple[str, ...]
    idempotency_key: str

    @classmethod
    def build(
        cls,
        *,
        tenant_id: str,
        case_id: object,
        outcome: object,
        rationale: object,
        evidence_ids: object,
        finding_ids: object,
        idempotency_key: object,
    ) -> Draft:
        return cls(
            tenant_id=canonical_uuid(tenant_id, "tenant_id"),
            case_id=canonical_uuid(case_id, "case_id"),
            outcome=validate_outcome(outcome),
            rationale=_text(rationale, "rationale", minimum=RATIONALE_MIN, maximum=RATIONALE_MAX),
            evidence_ids=_uuid_list(evidence_ids, "evidence_ids"),
            finding_ids=_uuid_list(finding_ids, "finding_ids"),
            idempotency_key=validate_idempotency_key(idempotency_key),
        )

    def digest(self) -> str:
        return digest(
            {
                "case_id": self.case_id,
                "outcome": self.outcome,
                "rationale": self.rationale,
                "evidence_ids": list(self.evidence_ids),
                "finding_ids": list(self.finding_ids),
            }
        )


# --- review (approve / reject / withdraw) --------------------------------------------


@dataclass(frozen=True)
class Review:
    action: Literal["approve", "reject", "withdraw"]
    decision_id: str
    expected_version: int
    reason: str | None
    idempotency_key: str

    @classmethod
    def build(
        cls,
        *,
        action: Literal["approve", "reject", "withdraw"],
        decision_id: object,
        expected_version: object,
        idempotency_key: object,
        reason: object = None,
    ) -> Review:
        text: str | None = None
        if action == "reject" or reason not in (None, ""):
            text = _text(reason, "reason", minimum=RATIONALE_MIN, maximum=REASON_MAX)
        return cls(
            action=action,
            decision_id=canonical_uuid(decision_id, "decision_id"),
            expected_version=_positive_int(expected_version, "expected_version"),
            reason=text,
            idempotency_key=validate_idempotency_key(idempotency_key),
        )

    def digest(self) -> str:
        return digest(
            {
                "action": self.action,
                "decision_id": self.decision_id,
                "expected_version": self.expected_version,
                "reason": self.reason,
            }
        )


def check_reviewable(
    *,
    status: str,
    origin: str,
    proposer_role: str | None,
    action: str,
) -> None:
    """Refuse a review of anything but an officer's open proposal."""
    if status == "draft" or origin != "officer" or proposer_role in (None, "service"):
        raise DecisionRefused(
            "An agent draft cannot be approved or executed; an officer must propose a decision.",
            "decision_id",
            "agent_only_decision",
        )
    if status != "proposed":
        raise VersionConflict(
            f"Only a proposed decision can be {action}d; this one is {status}.",
            "decision_id",
            "not_pending",
        )


def check_fresh_proposal(proposed_at: datetime.datetime, now: datetime.datetime) -> None:
    if now - proposed_at > MAX_PROPOSAL_AGE:
        raise VersionConflict(
            "The proposal is too old to approve; review the case again and propose anew.",
            "decision_id",
            "proposal_expired",
        )


def check_case_unchanged(status_at_proposal: str, current_status: str) -> None:
    if status_at_proposal != current_status:
        raise VersionConflict(
            "The case status changed in Jube after this decision was proposed.",
            "decision_id",
            "case_state_changed",
        )


def approval_expired(approved_at: datetime.datetime, now: datetime.datetime) -> bool:
    return now - approved_at > MAX_APPROVAL_AGE


# --- evidence requests --------------------------------------------------------------


@dataclass(frozen=True)
class RequestedItem:
    document_type: str
    description: str


@dataclass(frozen=True)
class EvidenceRequestInput:
    """Validated ``RequestEvidenceRequest`` (integration/api/openapi.json)."""

    tenant_id: str
    case_id: str
    expected_version: int
    finding_ids: tuple[str, ...]
    items: tuple[RequestedItem, ...]
    due_date: datetime.date
    message_to_taxpayer: str
    idempotency_key: str

    @classmethod
    def build(
        cls,
        *,
        tenant_id: str,
        case_id: object,
        body: Mapping[str, Any],
        idempotency_key: object,
        today: datetime.date,
    ) -> EvidenceRequestInput:
        if not isinstance(body, Mapping):
            raise InvalidMapping("The request body must be an object.", "body", "invalid_type")
        allowed = {"expected_version", "finding_ids", "items", "due_date", "message_to_taxpayer"}
        extra = sorted(set(body) - allowed)
        if extra:
            raise InvalidMapping(f"Unknown field {extra[0]}.", extra[0], "unknown_field")
        for field in sorted(allowed):
            if field not in body:
                raise InvalidMapping(f"{field} is required.", field, "required")
        return cls(
            tenant_id=canonical_uuid(tenant_id, "tenant_id"),
            case_id=canonical_uuid(case_id, "case_id"),
            expected_version=_positive_int(body["expected_version"], "expected_version"),
            finding_ids=_uuid_list(body["finding_ids"], "finding_ids", minimum=1),
            items=_items(body["items"]),
            due_date=_due_date(body["due_date"], today),
            message_to_taxpayer=_text(
                body["message_to_taxpayer"], "message_to_taxpayer", minimum=1, maximum=MESSAGE_MAX
            ),
            idempotency_key=validate_idempotency_key(idempotency_key),
        )

    def digest(self) -> str:
        return digest(
            {
                "case_id": self.case_id,
                "expected_version": self.expected_version,
                "finding_ids": list(self.finding_ids),
                "items": [[i.document_type, i.description] for i in self.items],
                "due_date": self.due_date.isoformat(),
                "message_to_taxpayer": self.message_to_taxpayer,
            }
        )


def _items(value: object) -> tuple[RequestedItem, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise InvalidMapping("items must be a list.", "items", "invalid_type")
    if not 1 <= len(value) <= MAX_ITEMS:
        raise InvalidMapping(f"items needs 1 to {MAX_ITEMS} entries.", "items", "out_of_range")
    items = []
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != {"document_type", "description"}:
            raise InvalidMapping(
                "Each item needs document_type and description only.", "items", "invalid_item"
            )
        if raw["document_type"] not in DOCUMENT_TYPES:
            raise InvalidMapping("Unknown document_type.", "items", "unknown_document_type")
        items.append(
            RequestedItem(
                raw["document_type"],
                _text(raw["description"], "items", minimum=1, maximum=ITEM_DESCRIPTION_MAX),
            )
        )
    return tuple(items)


def _due_date(value: object, today: datetime.date) -> datetime.date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise InvalidMapping("due_date must be YYYY-MM-DD.", "due_date", "invalid_date")
    try:
        due = datetime.date.fromisoformat(value)
    except ValueError:
        raise InvalidMapping("due_date must be YYYY-MM-DD.", "due_date", "invalid_date") from None
    if due <= today:
        raise DecisionRefused("due_date must be after today.", "due_date", "due_date_past")
    if due > today + datetime.timedelta(days=MAX_DUE_DAYS):
        raise DecisionRefused(
            f"due_date must be within {MAX_DUE_DAYS} days.", "due_date", "due_date_too_far"
        )
    return due


# --- outward action result ----------------------------------------------------------


def classify_upstream_error(exc: BaseException) -> tuple[str, bool]:
    """(error_code, retryable) for a failed Jube call.

    ``JubeAdapterError`` carries an ``ErrorCode`` and ``retryable``. A
    ``version_conflict`` means Jube's case moved away from the status the
    decision was approved against, so resending would apply a stale approval.
    """
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or not code:
        return "upstream_unavailable", True
    if code == "version_conflict":
        return code, False
    retryable = getattr(exc, "retryable", False)
    return code[:64], bool(retryable)
