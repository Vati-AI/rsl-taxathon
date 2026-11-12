"""Durable outbox rules (TAX-033).

Pure functions with no Frappe import, so they are testable without a bench.
``vati_tax.outbox.store`` and ``vati_tax.outbox.worker`` apply them to the
``Vati Outbox Event``, ``Vati Outbox Attempt`` and ``Vati Outbox Effect`` DocTypes.

Model (see docs/decisions/ADR-008-durable-outbox.md):

- An event is written in the same database transaction as the domain change that
  caused it, so either both commit or neither does.
- A worker delivers committed events at least once. Every attempt is persisted
  before the handler runs, so a crash still counts against the retry budget.
- Handlers must be idempotent. Side effects run under stable idempotency keys, and
  an outward call to a non-idempotent upstream goes through the effect ledger: a
  replay whose earlier call has an unknown outcome is reconciled or dead-lettered,
  never blindly re-sent.
- Failures retry with bounded exponential backoff and jitter. A permanent error
  or an exhausted budget moves the event to ``dead_letter``. Only an operator's
  manual retry moves it back to ``pending``.
- Events of one aggregate (tenant + case) are delivered in enqueue order. A
  dead-lettered event blocks later events of the same case until it is retried.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from vati_tax.ids.canonical import (
    IdempotencyConflict,
    InvalidMapping,
    MappingError,
    canonical_uuid,
    validate_idempotency_key,
)

SCHEMA_VERSION = "1"

# EventEnvelope.type and source_service in integration/api/openapi.json.
EVENT_TYPES = (
    "case.registered",
    "case.status_projected",
    "evidence.received",
    "extraction.completed",
    "extraction.failed",
    "investigation.requested",
    "finding.recorded",
    "evidence_request.recorded",
    # TAX-121 (ADR-042): run the TAX-112-116 assurance checks for a sent refund claim.
    "assurance.requested",
    # TAX-124 (ADR-048): an officer-approved result goes back to an RSL system.
    "rsl_outbound.requested",
)
SOURCE_SERVICES = ("jube", "frappe", "openaleph", "yente", "docling", "langgraph", "vati")
PAYLOAD_VALUE_MAX = 255
PAYLOAD_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
EFFECT_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

Status = Literal["pending", "in_progress", "retry", "delivered", "dead_letter"]
STATUSES: tuple[Status, ...] = ("pending", "in_progress", "retry", "delivered", "dead_letter")
# A worker may claim these when due (in_progress only once its lease expired).
CLAIMABLE: tuple[Status, ...] = ("pending", "retry", "in_progress")
# The contract's SyncState for each internal status.
SYNC_STATE: dict[str, str] = {
    "pending": "pending",
    "in_progress": "pending",
    "retry": "pending",
    "delivered": "synced",
    "dead_letter": "failed",
}


@dataclass(frozen=True)
class RetryPolicy:
    """Delivery budget. Defaults are deliberately small for the prototype."""

    max_attempts: int = 5
    base_delay: timedelta = timedelta(seconds=30)
    max_delay: timedelta = timedelta(hours=1)
    lease: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20")
        if self.base_delay <= timedelta(0) or self.max_delay < self.base_delay:
            raise ValueError("delays must be positive and max_delay >= base_delay")
        if self.lease <= timedelta(0):
            raise ValueError("lease must be positive")


DEFAULT_POLICY = RetryPolicy()


# --- errors ------------------------------------------------------------------------


class DeliveryError(Exception):
    """Raised by a handler to classify a failure. ``code`` is stored, the text is not."""

    retryable = True

    def __init__(self, code: str, retry_after: float | None = None):
        if not ERROR_CODE.fullmatch(code):
            code = "handler_error"
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class RetryableDeliveryError(DeliveryError):
    """Transient failure (timeout, 429, 503): try again after a backoff."""


class PermanentDeliveryError(DeliveryError):
    """Failure that will not succeed on retry: dead-letter at once."""

    retryable = False


class EffectOutcomeUnknown(PermanentDeliveryError):
    """An earlier attempt started a non-idempotent call and its outcome is unknown.

    Re-sending could duplicate the outward action, so the event is dead-lettered
    for an operator to reconcile with the upstream before a manual retry.
    """

    def __init__(self) -> None:
        super().__init__("effect_outcome_unknown")


class IdempotencyInProgress(MappingError):
    """The event is being delivered right now; try again later (409)."""

    code = "idempotency_in_progress"
    http_status = 409
    retryable = True


class InvalidTransition(MappingError):
    """The requested state change is not allowed from the current status."""

    code = "invalid_domain_input"
    http_status = 422


@dataclass(frozen=True)
class Failure:
    code: str
    retryable: bool
    retry_after: float | None = None


def classify(exc: BaseException) -> Failure:
    """Map a handler exception to a stored failure without keeping its message.

    Messages can carry upstream bodies or document text, so only a stable code is
    persisted. Unexpected exceptions are retryable: the attempt budget bounds them.
    """
    if isinstance(exc, DeliveryError):
        return Failure(exc.code, exc.retryable, exc.retry_after)
    if isinstance(exc, MappingError):
        return Failure(exc.code, exc.retryable)
    if isinstance(exc, TimeoutError):
        return Failure("upstream_timeout", True)
    if isinstance(exc, ConnectionError):
        return Failure("upstream_unavailable", True)
    return Failure("handler_error", True)


# --- envelope ----------------------------------------------------------------------


def _payload(value: object) -> dict[str, str | int | bool | None]:
    if not isinstance(value, Mapping):
        raise InvalidMapping("payload must be an object.", "payload")
    out: dict[str, str | int | bool | None] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not PAYLOAD_KEY.fullmatch(key):
            raise InvalidMapping("payload keys must be snake_case.", "payload", "invalid_key")
        if isinstance(item, float) or not (item is None or isinstance(item, str | int | bool)):
            # Money travels as decimal strings; floats are refused, as in the contract.
            raise InvalidMapping(
                "payload values must be strings, integers, booleans or null.",
                "payload",
                "invalid_value",
            )
        if isinstance(item, str) and len(item) > PAYLOAD_VALUE_MAX:
            raise InvalidMapping(
                "payload strings are references, at most 255 characters.",
                "payload",
                "value_too_long",
            )
        out[key] = item
    return out


def utc(value: datetime) -> datetime:
    """Naive values are taken as UTC (Frappe stores naive datetimes)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def iso(value: datetime) -> str:
    return utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Envelope:
    """A validated ``EventEnvelope`` (openapi.json) with canonical values."""

    event_id: str
    tenant_id: str
    case_id: str
    correlation_id: str
    type: str
    occurred_at: datetime
    idempotency_key: str
    source_service: str
    payload: dict[str, str | int | bool | None]

    @classmethod
    def build(
        cls,
        *,
        tenant_id: object,
        case_id: object,
        type: object,
        payload: object,
        idempotency_key: object,
        correlation_id: object,
        source_service: object = "vati",
        event_id: object = None,
        occurred_at: datetime | None = None,
    ) -> Envelope:
        if type not in EVENT_TYPES:
            raise InvalidMapping("Unknown event type.", "type", "unknown_event_type")
        if source_service not in SOURCE_SERVICES:
            raise InvalidMapping("Unknown source_service.", "source_service", "unknown_service")
        return cls(
            canonical_uuid(event_id, "event_id") if event_id else str(uuid.uuid4()),
            canonical_uuid(tenant_id, "tenant_id"),
            canonical_uuid(case_id, "case_id"),
            canonical_uuid(correlation_id, "correlation_id"),
            str(type),
            utc(occurred_at or datetime.now(UTC)),
            validate_idempotency_key(idempotency_key),
            str(source_service),
            _payload(payload),
        )

    @property
    def dedupe_key(self) -> str:
        return dedupe_key(self.tenant_id, self.idempotency_key)

    @property
    def ordering_key(self) -> str:
        return ordering_key(self.tenant_id, self.case_id)

    @property
    def request_digest(self) -> str:
        """Digest of what the event asks for; event_id and timestamps are excluded."""
        return request_digest(self.case_id, self.type, self.source_service, self.payload)

    def as_dict(self) -> dict[str, Any]:
        """Contract shape of ``EventEnvelope``."""
        return {
            "schema_version": SCHEMA_VERSION,
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "case_id": self.case_id,
            "correlation_id": self.correlation_id,
            "type": self.type,
            "occurred_at": iso(self.occurred_at),
            "idempotency_key": self.idempotency_key,
            "source_service": self.source_service,
            "payload": dict(self.payload),
        }


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(("v1", *parts)).encode()).hexdigest()


def dedupe_key(tenant_id: str, idempotency_key: str) -> str:
    return _digest("outbox", tenant_id, idempotency_key)


def ordering_key(tenant_id: str, case_id: str) -> str:
    return _digest("aggregate", tenant_id, case_id)


def sequence_key(ordering: str, sequence: int) -> str:
    return f"{ordering}:{sequence:010d}"


def request_digest(case_id: str, type: str, source_service: str, payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json(
            {"case_id": case_id, "type": type, "source": source_service, "payload": payload}
        ).encode()
    ).hexdigest()


def decide_enqueue(request_digest: str, stored_digest: str | None) -> Literal["create", "replay"]:
    """Same idempotency key: replay when identical, conflict (422) when changed."""
    if stored_digest is None:
        return "create"
    if stored_digest != request_digest:
        raise IdempotencyConflict(
            "idempotency_key was already used for a different event.",
            "idempotency_key",
            "key_reused",
        )
    return "replay"


# --- retries -----------------------------------------------------------------------


def backoff(
    attempt: int,
    policy: RetryPolicy = DEFAULT_POLICY,
    rand: Callable[[], float] | None = None,
    retry_after: float | None = None,
) -> timedelta:
    """Delay before attempt ``attempt + 1``: capped exponential with equal jitter.

    The delay lies in [d/2, d] for d = min(max_delay, base_delay * 2**(attempt-1)),
    so retries spread out without ever firing immediately. A ``Retry-After`` hint
    from the upstream is honoured, up to ``max_delay``.
    """
    if attempt < 1:
        raise ValueError("attempt starts at 1")
    ceiling = policy.max_delay.total_seconds()
    full = min(ceiling, policy.base_delay.total_seconds() * 2 ** min(attempt - 1, 30))
    jitter = 0.5 if rand is None else min(max(rand(), 0.0), 1.0)
    delay = full / 2 + full / 2 * jitter
    if retry_after is not None and retry_after > 0:
        delay = max(delay, min(float(retry_after), ceiling))
    return timedelta(seconds=delay)


@dataclass(frozen=True)
class AfterFailure:
    status: Literal["retry", "dead_letter"]
    next_attempt_at: datetime | None


def after_failure(
    attempts: int,
    failure: Failure,
    now: datetime,
    policy: RetryPolicy = DEFAULT_POLICY,
    rand: Callable[[], float] | None = None,
) -> AfterFailure:
    """Retry with backoff while budget remains and the failure is retryable."""
    if not failure.retryable or attempts >= policy.max_attempts:
        return AfterFailure("dead_letter", None)
    return AfterFailure(
        "retry", utc(now) + backoff(attempts, policy, rand, retry_after=failure.retry_after)
    )


def is_claimable(
    status: str,
    next_attempt_at: datetime | None,
    lease_until: datetime | None,
    now: datetime,
) -> bool:
    """Whether a worker may take the event now (ordering aside)."""
    moment = utc(now)
    if status in ("pending", "retry"):
        return next_attempt_at is None or utc(next_attempt_at) <= moment
    if status == "in_progress":
        # The holder crashed or stalled: the lease expired, so the event is replayed.
        return lease_until is None or utc(lease_until) <= moment
    return False


def manual_retry_allowed(status: str, lease_until: datetime | None, now: datetime) -> None:
    """Only dead letters may be retried by hand; a live delivery is a 409."""
    if status == "dead_letter":
        return
    if status == "in_progress" and lease_until is not None and utc(lease_until) > utc(now):
        raise IdempotencyInProgress("The event is being delivered.", "status", "in_progress")
    raise InvalidTransition(
        "Only dead-lettered events can be retried manually.", "status", "not_dead_letter"
    )


# --- outward effects ---------------------------------------------------------------


EffectState = Literal["started", "completed"]


def effect_key(tenant_id: str, event_id: str, name: str) -> str:
    return _digest("effect", tenant_id, event_id, name)


def effect_idempotency_key(event_idempotency_key: str, name: str) -> str:
    """Stable key sent upstream; identical on every replay of the same event."""
    raw = f"{event_idempotency_key}:{name}"
    if len(raw) <= 255:
        return raw
    return "vati-effect:" + hashlib.sha256(raw.encode()).hexdigest()


def validate_effect_name(name: object) -> str:
    if not isinstance(name, str) or not EFFECT_NAME.fullmatch(name):
        raise InvalidMapping("effect name must be lower-case dotted.", "effect", "invalid_name")
    return name


def validate_result_ref(value: object) -> str | None:
    """An upstream reference (ID or URL-safe token), never response content."""
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > PAYLOAD_VALUE_MAX
        or value != value.strip()
        or any(ord(ch) < 32 for ch in value)
    ):
        raise InvalidMapping(
            "result reference must be 1-255 printable characters.", "result_ref", "invalid_ref"
        )
    return value


def decide_effect(
    stored: EffectState | None,
    *,
    idempotent: bool,
    can_reconcile: bool,
) -> Literal["perform", "reuse", "reconcile", "unknown"]:
    """How to run a named side effect given its ledger row from earlier attempts.

    - no row: perform it (the ledger records ``started`` durably first);
    - ``completed``: reuse the recorded result, never call again;
    - ``started`` (an earlier attempt crashed mid-call): an upstream that honours
      the idempotency key may be called again; otherwise look the result up by key
      (reconcile) or stop with an unknown outcome.
    """
    if stored is None:
        return "perform"
    if stored == "completed":
        return "reuse"
    if idempotent:
        return "perform"
    if can_reconcile:
        return "reconcile"
    return "unknown"
