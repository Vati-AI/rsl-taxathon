"""Recurring submission-error reminders (TAX-078), independent of Frappe.

A *correction reminder* tells one taxpayer, before its next VAT refund claim,
about an omission found in one of its earlier claims. An officer records it
from an approved TAX-046 decision that asked the taxpayer to correct or add
something (``close_after_correction`` or ``request_further_evidence``), with a
topic from a fixed list and a short explanation written for the taxpayer.

Each topic matches the TAX-044 pre-submission prompt codes for the same kind
of gap, so the portal can say "you were reminded about this before" next to a
prompt that recurs. Reminders are guidance only: they never block sending,
never change a claim and never say anything about risk, penalties or fraud.

A reminder stays correctable: staff can correct its topic or explanation (the
earlier wording is kept in its history) or withdraw it, each with a reason.
It is hidden from the taxpayer while the decision it came from is challenged
(TAX-069) or after any review overturned part of that decision.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from vati_tax.ids.canonical import (
    InvalidMapping,
    MappingError,
    canonical_uuid,
    validate_idempotency_key,
)


@dataclass(frozen=True)
class Topic:
    title: str
    # TAX-044 prompt codes (``portal.presubmission``) for the same kind of gap.
    prompt_codes: tuple[str, ...]


TOPICS: Final[dict[str, Topic]] = {
    "missing_invoices": Topic(
        "List every purchase invoice", ("refund_claim_exceeds_documented_input_vat",)
    ),
    "period_figures": Topic("Check the period's VAT figures", ("return_arithmetic_mismatch",)),
    "invoice_totals": Topic("Check each invoice's totals", ("invoice_total_mismatch",)),
    "payment_details": Topic(
        "Add what you paid for each invoice",
        ("payment_evidence_missing", "payment_total_differs_from_invoice"),
    ),
    "contract_documents": Topic(
        "Attach contracts or purchase orders", ("contract_evidence_missing",)
    ),
}
# Approved decision outcomes (TAX-046) that record an omission the taxpayer
# had to correct or supply. Other outcomes are not a reviewed omission.
SOURCE_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"close_after_correction", "request_further_evidence"}
)
STATUSES: Final[tuple[str, ...]] = ("active", "withdrawn")
# TAX-069 reviewed outcomes that mean part or all of a decision did not stand.
OVERTURNED: Final[frozenset[str]] = frozenset({"overturned", "partially_overturned"})

SourceState = Literal["stands", "under_review", "overturned"]

EXPLANATION_MIN: Final = 20
EXPLANATION_MAX: Final = 1000
REASON_MIN: Final = 20
REASON_MAX: Final = 2000
MAX_HISTORY: Final = 50


class ReminderNotFound(MappingError):
    code = "not_found"
    http_status = 404


class ReminderConflict(MappingError):
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


_NS: Final = uuid.UUID("3c1d7e52-6a0b-4f19-9e2d-5b8a7c6d0078")


def reminder_id(tenant_id: str, idempotency_key: str) -> str:
    return str(uuid.uuid5(_NS, f"{tenant_id}|{idempotency_key}"))


def reminder_key(tenant_id: str, decision_id: str, topic: str) -> str:
    """One reminder per decision and topic, whatever its status."""
    return digest(["reminder", tenant_id, decision_id, topic])


def check_topic(value: object) -> str:
    if not isinstance(value, str) or value not in TOPICS:
        raise InvalidMapping("Unknown reminder topic.", "topic", "unknown_topic")
    return value


def text(value: object, field: str, low: int, high: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidMapping(f"{field} is required.", field, "required")
    value = " ".join(value.split())
    if not low <= len(value) <= high:
        raise InvalidMapping(
            f"{field} must be {low} to {high} characters.", field, "invalid_length"
        )
    return value


def check_version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidMapping("expected_version must be an integer.", "expected_version", "required")
    return value


@dataclass(frozen=True)
class RecordRequest:
    decision_id: str
    topic: str
    explanation: str
    idempotency_key: str

    @classmethod
    def build(cls, body: Mapping[str, Any], idempotency_key: str) -> RecordRequest:
        return cls(
            decision_id=canonical_uuid(body.get("decision_id"), "decision_id"),
            topic=check_topic(body.get("topic")),
            explanation=text(
                body.get("explanation"), "explanation", EXPLANATION_MIN, EXPLANATION_MAX
            ),
            idempotency_key=validate_idempotency_key(idempotency_key),
        )

    def payload(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "topic": self.topic,
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class ReminderRecord:
    reminder_id: str
    tenant_id: str
    taxpayer_id: str
    case_id: str
    decision_id: str
    topic: str
    explanation: str
    request_digest: str
    recorded_by: str
    recorded_at: datetime.datetime
    status: str = "active"
    version: int = 1
    updated_by: str | None = None
    updated_at: datetime.datetime | None = None
    change_reason: str | None = None
    # Earlier wording, oldest first: {"version", "topic", "explanation", "status",
    # "changed_by", "changed_at", "reason"}. Never shown to the taxpayer.
    history: tuple[dict[str, Any], ...] = ()


def source_state(challenges: Iterable[Any]) -> SourceState:
    """Whether the decision a reminder came from still stands (TAX-069).

    Conservative on purpose: a reminder is shown only while no challenge to its
    decision is waiting for review and no review overturned any part of it.
    """
    state: SourceState = "stands"
    for challenge in challenges:
        if challenge.status == "lodged":
            return "under_review"
        if challenge.outcome in OVERTURNED:
            state = "overturned"
    return state


def visible_to_taxpayer(record: ReminderRecord, state: SourceState) -> bool:
    return record.status == "active" and state == "stands"


def taxpayer_view(record: ReminderRecord) -> dict[str, Any]:
    """Allowlisted taxpayer fields: no case, decision or staff identities."""
    return {
        "reminder_id": record.reminder_id,
        "topic": record.topic,
        "title": TOPICS[record.topic].title,
        "explanation": record.explanation,
        "prompt_codes": list(TOPICS[record.topic].prompt_codes),
        "recorded_on": record.recorded_at.date().isoformat(),
        "updated_on": record.updated_at.date().isoformat() if record.updated_at else None,
    }


def taxpayer_reminders(
    records: Iterable[ReminderRecord], states: Mapping[str, SourceState]
) -> list[dict[str, Any]]:
    """Visible reminders, newest first, one per topic (the most recent wording)."""
    visible = [r for r in records if visible_to_taxpayer(r, states.get(r.decision_id, "stands"))]
    visible.sort(key=lambda r: (r.updated_at or r.recorded_at, r.reminder_id), reverse=True)
    seen: set[str] = set()
    views = []
    for record in visible:
        if record.topic in seen:
            continue
        seen.add(record.topic)
        views.append(taxpayer_view(record))
    return views


def staff_view(record: ReminderRecord, state: SourceState) -> dict[str, Any]:
    return {
        **taxpayer_view(record),
        "tenant_id": record.tenant_id,
        "taxpayer_id": record.taxpayer_id,
        "case_id": record.case_id,
        "decision_id": record.decision_id,
        "status": record.status,
        "version": record.version,
        "recorded_by": record.recorded_by,
        "recorded_at": iso(record.recorded_at),
        "updated_by": record.updated_by,
        "updated_at": iso(record.updated_at),
        "change_reason": record.change_reason,
        "history": list(record.history),
        "source_state": state,
        "visible_to_taxpayer": visible_to_taxpayer(record, state),
    }


def annotate_prompts(
    prompts: Sequence[dict[str, Any]], reminders: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Mark TAX-044 prompts that a visible reminder already covered.

    Adds ``reminder_id`` (or ``None``) to each prompt; nothing else changes and
    no prompt is added or removed.
    """
    by_code: dict[str, str] = {}
    for reminder in reminders:
        for code in reminder["prompt_codes"]:
            by_code.setdefault(code, reminder["reminder_id"])
    return [{**prompt, "reminder_id": by_code.get(prompt.get("code", ""))} for prompt in prompts]
