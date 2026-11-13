"""Frappe to Jube bridge rules (TAX-039).

Pure functions with no Frappe import, so they are testable without a bench.
``vati_tax.bridge.store`` applies them; ADR-028 explains the design.

Two directions, one authority each (ADR-001):

- **Outward.** When a taxpayer sends a submission (``draft`` or
  ``correction_needed`` -> ``submitted``), Frappe registers the canonical case and
  enqueues one ``case.registered`` outbox event in the same transaction. The
  worker invokes the Jube risk model with the declared figures and asks the
  TAX-034 adapter for the case. Both calls are replay-safe.
- **Inward.** Jube's case state is mirrored into a staff-only sync record and a
  small taxpayer-facing ``review_status`` on the submission. Jube sends no
  webhooks (ADR-009), so observations come from the outbox handler, the poller,
  or a service identity posting a ``case.status_projected`` envelope.

Rules enforced here:

- **Replay.** An envelope's ``event_id`` with the same content is a replay
  (no-op); with different content it is ``idempotency_conflict``.
- **Order.** Jube has no case version, so each observation carries the time it
  was read from Jube. Older observations are refused as stale
  (``version_conflict``); a later one with the same projection only moves the
  watermark.
- **No loop.** Mirror writes never change the submission ``status``, never run
  the submission controller and never enqueue an outbox event. Only a taxpayer's
  own send to ``submitted`` enqueues one.
- **Taxpayer-safe.** The taxpayer sees one of three review statuses. Jube status
  names, escalation, assignment, risk inputs, rule names and activations never
  reach a taxpayer-readable field, and an observation that carries any field
  outside the allowlist is refused rather than filtered.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final, Literal

from vati_tax.ids.canonical import (
    IdempotencyConflict,
    InvalidMapping,
    MappingConflict,
    MappingError,
    canonical_uuid,
)
from vati_tax.submissions import domain

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

# Taxpayer-facing review statuses; the same words the taxpayer portal already
# uses (apps/taxpayer-web/src/lib/format.ts). Nothing finer is ever shown.
UNDER_REVIEW: Final = "under_review"
EVIDENCE_REQUESTED: Final = "evidence_requested"
COMPLETED: Final = "completed"
REVIEW_STATUSES: Final = (UNDER_REVIEW, EVIDENCE_REQUESTED, COMPLETED)

# Every operational state maps to exactly one review status. Internal failures,
# escalation and validation are all just "under review" to the taxpayer.
REVIEW_STATUS: Final[dict[str, str]] = {
    "RECEIVED": UNDER_REVIEW,
    "VALIDATING": UNDER_REVIEW,
    "INVESTIGATING": UNDER_REVIEW,
    "AWAITING_EVIDENCE": EVIDENCE_REQUESTED,
    "AWAITING_HUMAN": UNDER_REVIEW,
    "RESOLVED": COMPLETED,
    "FAILED_PROCESSING": UNDER_REVIEW,
}

# The only submission fields the mirror writes, and the only bridge fields a
# taxpayer can read. Tests check the taxpayer view never grows beyond this.
TAXPAYER_FIELDS: Final = frozenset({"review_status", "review_status_at"})

EVENT_REGISTERED: Final = "case.registered"
EVENT_OBSERVED: Final = "case.status_projected"
SCHEMA_VERSION: Final = "1"
SOURCE_SUBMISSION: Final = "frappe"
SOURCE_OBSERVATION: Final = "jube"
# The payload of an observation: nothing else is accepted.
OBSERVATION_FIELDS: Final = frozenset(
    {"jube_case_id", "status", "escalated", "closed", "observed_at"}
)
_JUBE_ID = re.compile(r"^[1-9][0-9]{0,18}$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
_NAMESPACE = uuid.UUID("7c1d4a62-0b39-4f8e-9a51-6f3e2d0c9b17")  # TAX-039 observation IDs

Origin = Literal[
    "taxpayer", "staff", "operator", "jube_mirror", "rsl_online_refunds", "not_referred"
]
ORIGIN_MIRROR: Final = "jube_mirror"
ORIGIN_TAXPAYER: Final = "taxpayer"
# TAX-122 (ADR-043): a claim filed in RSL's Online Refunds Application, sent by the intake.
ORIGIN_RSL_ONLINE_REFUNDS: Final = "rsl_online_refunds"
# TAX-131 (ADR-043 §5): a send RSL did not refer never opens a case or reaches Jube's rules.
ORIGIN_NOT_REFERRED: Final = "not_referred"
SENDING_ORIGINS: Final = frozenset({ORIGIN_TAXPAYER, ORIGIN_RSL_ONLINE_REFUNDS})


class StaleUpdate(MappingError):
    """An older observation than the one already applied (409 ``version_conflict``)."""

    code = "version_conflict"
    http_status = 409


# --- outward: submission -> case.registered ---------------------------------------


def submission_origin(mirror: bool, source: str | None, *, simulator: bool) -> str:
    """Who sent a submission change: the Jube mirror, RSL's intake or the taxpayer.

    A taxpayer send counts only where the Online Refunds simulator stands in for RSL
    (``submissions.domain.referred_source``); otherwise it is ``not_referred``.
    """
    if mirror:
        return ORIGIN_MIRROR
    if source == ORIGIN_RSL_ONLINE_REFUNDS:
        return ORIGIN_RSL_ONLINE_REFUNDS
    if not domain.referred_source(source, simulator=simulator):
        return ORIGIN_NOT_REFERRED
    return ORIGIN_TAXPAYER


def should_register(before_status: str | None, after_status: str, origin: str) -> bool:
    """Whether a submission change sends work to Jube.

    A send (``draft``/``correction_needed`` -> ``submitted``) does when the taxpayer
    sent it from the portal or RSL's intake sent a claim filed in the Online Refunds
    Application (TAX-122). A mirror write never changes ``status``; it is refused here
    too, so a future mirror bug cannot start a loop. Any other origin is refused.
    """
    if origin not in SENDING_ORIGINS:
        return False
    return after_status == "submitted" and before_status in ("draft", "correction_needed")


def case_registration_key(submission_id: str) -> str:
    """One canonical case per submission, whatever the number of sends."""
    return f"vati:submission-case:{canonical_uuid(submission_id, 'submission_id')}"


def registration_event(submission_id: str, version: int) -> tuple[str, dict[str, Any]]:
    """Idempotency key and payload of the ``case.registered`` event for one send.

    The payload holds references only: no amounts, names or explanations.
    """
    submission = canonical_uuid(submission_id, "submission_id")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise InvalidMapping("version must be a positive integer.", "version")
    key = f"vati:submission-sent:{submission}:v{version}"
    return key, {"submission_id": submission, "submission_version": version}


def parse_registration(payload: Mapping[str, Any]) -> tuple[str, int]:
    submission = canonical_uuid(payload.get("submission_id"), "submission_id")
    version = payload.get("submission_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise InvalidMapping("submission_version must be a positive integer.", "payload")
    return submission, version


# --- declared risk facts ----------------------------------------------------------

# Rule inputs of integration/jube/rules/tax_risk_rules.v1.json. The Jube side
# checks this set equals the ruleset's features, so a ruleset change fails loudly.
FACT_NAMES: Final = (
    "refund_claim",
    "claimed_input_vat",
    "documented_input_vat",
    "unsupported_input_vat",
    "related_party_purchase_net",
    "consecutive_loss_periods",
    "imported_service_unverified_count",
)
UNVERIFIED_TREATMENTS: Final = frozenset({"not_declared", "unsure"})
_CENT = Decimal("0.01")


def _amount(value: Decimal | None) -> Decimal:
    return (value or Decimal("0")).quantize(_CENT)


def declared_facts(
    *,
    refund_claimed: Decimal | None,
    input_vat_claimed: Decimal | None,
    imported_treatments: Iterable[str | None],
) -> dict[str, str]:
    """Rule inputs from what the taxpayer declared, as decimal strings.

    This first evaluation happens before any document is extracted, so no input
    VAT is documented yet: ``documented_input_vat`` is 0 and all claimed input VAT
    counts as unsupported. That is literally true at this point and only routes
    the case to review; document-derived inputs (documented VAT, related-party
    purchases, loss history) come from later pipeline steps (TAX-054). Signals
    are never assessments, refund decisions or findings.
    """
    refund = _amount(refund_claimed)
    claimed = _amount(input_vat_claimed)
    documented = Decimal("0.00")
    unverified = sum(1 for t in imported_treatments if t in UNVERIFIED_TREATMENTS)
    facts = {
        "refund_claim": f"{refund}",
        "claimed_input_vat": f"{claimed}",
        "documented_input_vat": f"{documented}",
        "unsupported_input_vat": f"{(claimed - documented).quantize(_CENT)}",
        "related_party_purchase_net": "0.00",
        "consecutive_loss_periods": "0",
        "imported_service_unverified_count": str(unverified),
    }
    assert tuple(facts) == FACT_NAMES
    return facts


# --- inward: observations ---------------------------------------------------------


def parse_time(value: object, field: str = "observed_at") -> datetime:
    if not isinstance(value, str) or not _ISO.fullmatch(value):
        raise InvalidMapping(f"{field} must be UTC ISO 8601 (Z).", field, "invalid_datetime")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def iso(value: datetime) -> str:
    moment = value if value.tzinfo else value.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class Projection:
    """What the mirror keeps of one Jube case. Every field was read from Jube."""

    jube_case_id: str
    status: str
    escalated: bool
    closed: bool

    @property
    def review_status(self) -> str:
        return REVIEW_STATUS[self.status]


@dataclass(frozen=True)
class Observation:
    tenant_id: str
    case_id: str
    event_id: str
    projection: Projection
    observed_at: datetime

    @property
    def digest(self) -> str:
        """Content digest for replay detection (event_id excluded)."""
        body = {
            "case_id": self.case_id,
            "jube_case_id": self.projection.jube_case_id,
            "status": self.projection.status,
            "escalated": self.projection.escalated,
            "closed": self.projection.closed,
            "observed_at": iso(self.observed_at),
        }
        text = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()


def projection(
    jube_case_id: object, status: object, escalated: object, closed: object
) -> Projection:
    text = str(jube_case_id) if isinstance(jube_case_id, int | str) else ""
    if isinstance(jube_case_id, bool) or not _JUBE_ID.fullmatch(text):
        raise InvalidMapping("jube_case_id must be a Jube case number.", "jube_case_id")
    if status not in CASE_STATUSES:
        raise InvalidMapping("status must be a CaseStatus.", "status", "unknown_status")
    if not isinstance(escalated, bool) or not isinstance(closed, bool):
        raise InvalidMapping("escalated and closed must be booleans.", "payload", "invalid_value")
    if closed and status != "RESOLVED":
        # The workflow closes a case only in Resolved (case_workflow.v1.json).
        raise InvalidMapping("Only a resolved case is closed.", "closed", "inconsistent")
    return Projection(text, str(status), escalated, closed)


def observation_id(tenant_id: str, case_id: str, seen: Projection, at: datetime) -> str:
    """Deterministic event_id for an observation Vati made itself."""
    name = "|".join(
        (tenant_id, case_id, seen.jube_case_id, seen.status, str(seen.escalated), iso(at))
    )
    return str(uuid.uuid5(_NAMESPACE, name))


def observe(tenant_id: str, case_id: str, seen: Projection, at: datetime) -> Observation:
    tenant = canonical_uuid(tenant_id, "tenant_id")
    case = canonical_uuid(case_id, "case_id")
    moment = at if at.tzinfo else at.replace(tzinfo=UTC)
    return Observation(
        tenant, case, observation_id(tenant, case, seen, moment), seen, moment.astimezone(UTC)
    )


def parse_envelope(envelope: object) -> Observation:
    """Validate a posted ``case.status_projected`` EventEnvelope.

    Anything outside the allowlisted payload is refused, so risk scores, rule
    names, officer IDs or free text can never ride along into Frappe.
    """
    if not isinstance(envelope, Mapping):
        raise InvalidMapping("The event must be an object.", "event")
    if envelope.get("schema_version") != SCHEMA_VERSION:
        raise InvalidMapping("Unsupported schema_version.", "schema_version")
    if envelope.get("type") != EVENT_OBSERVED:
        raise InvalidMapping("Only case.status_projected is accepted.", "type", "unknown_type")
    if envelope.get("source_service") != SOURCE_OBSERVATION:
        raise InvalidMapping(
            "Case status may only be reported for Jube.", "source_service", "wrong_source"
        )
    payload = envelope.get("payload")
    if not isinstance(payload, Mapping):
        raise InvalidMapping("payload must be an object.", "payload")
    extra = set(payload) - OBSERVATION_FIELDS
    if extra:
        raise InvalidMapping(
            "payload carries fields the bridge does not accept.", "payload", "unexpected_field"
        )
    missing = OBSERVATION_FIELDS - set(payload)
    if missing:
        raise InvalidMapping("payload is incomplete.", "payload", "missing_field")
    seen = projection(
        payload["jube_case_id"], payload["status"], payload["escalated"], payload["closed"]
    )
    return Observation(
        canonical_uuid(envelope.get("tenant_id"), "tenant_id"),
        canonical_uuid(envelope.get("case_id"), "case_id"),
        canonical_uuid(envelope.get("event_id"), "event_id"),
        seen,
        parse_time(payload["observed_at"]),
    )


@dataclass(frozen=True)
class Mirror:
    """Stored mirror state of one case (``Vati Case Sync``)."""

    jube_case_id: str | None
    projection: Projection | None
    observed_at: datetime | None


Decision = Literal["apply", "unchanged"]


def decide_observation(
    seen: Observation, stored: Mirror | None, linked_jube_case: str | None
) -> Decision:
    """Apply a newer observation; refuse an older or contradictory one.

    ``unchanged``: newer but the same projection (only the watermark moves).
    """
    if linked_jube_case is not None and linked_jube_case != seen.projection.jube_case_id:
        raise MappingConflict(
            "The observation names a different Jube case.", "jube_case_id", "jube_case_mismatch"
        )
    if stored is None or stored.observed_at is None:
        return "apply"
    if stored.jube_case_id and stored.jube_case_id != seen.projection.jube_case_id:
        raise MappingConflict(
            "The observation names a different Jube case.", "jube_case_id", "jube_case_mismatch"
        )
    if seen.observed_at < stored.observed_at:
        raise StaleUpdate("A newer case state was already applied.", "observed_at", "stale")
    same = stored.projection == seen.projection
    if seen.observed_at == stored.observed_at:
        if same:
            return "unchanged"
        raise StaleUpdate(
            "Two different states were observed at the same moment.", "observed_at", "conflicting"
        )
    return "unchanged" if same else "apply"


# --- inbound ledger ---------------------------------------------------------------


def inbound_key(tenant_id: str, event_id: str) -> str:
    text = "\x1f".join(("v1", "bridge-inbound", tenant_id, event_id))
    return hashlib.sha256(text.encode()).hexdigest()


def decide_inbound(digest: str, stored_digest: str | None) -> Literal["new", "replay"]:
    """Same event_id: replay when identical, ``idempotency_conflict`` when changed."""
    if stored_digest is None:
        return "new"
    if stored_digest != digest:
        raise IdempotencyConflict(
            "event_id was already used for a different observation.", "event_id", "key_reused"
        )
    return "replay"


# --- taxpayer view ----------------------------------------------------------------


def taxpayer_status(submission_status: str, review_status: str | None) -> str:
    """The one status word a taxpayer sees for a submission.

    Frappe owns ``draft``, ``correction_needed`` and ``withdrawn``. Once sent, the
    mirrored review status is shown, and ``under_review`` until Jube reports.
    """
    if submission_status != "submitted":
        return submission_status
    return review_status if review_status in REVIEW_STATUSES else UNDER_REVIEW
