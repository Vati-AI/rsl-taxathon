"""Tenant audit ledger and review-only integrity signals (TAX-059), independent of Frappe.

The ledger is the TAX-046 ``Vati Audit Event`` stream, sealed into one hash
chain per tenant. Every entry records who did what (actor, role), to which
subject, when, with which correlation ID, and in which category:

- ``read``: a case, evidence or the ledger itself was read;
- ``write``: a record was created or changed (proposal, draft, execution, request);
- ``approval``: a supervisor approved or rejected a proposal;
- ``override``: a supervisor recorded an authorised exception to a named control;
- ``denied``: an action was refused by the policy (for example self-approval).

Each entry stores ``prev_hash`` (the previous entry's ``entry_hash`` in the same
tenant, or ``GENESIS``) and ``entry_hash`` (SHA-256 over its own fields and
``prev_hash``). ``verify_chain`` recomputes the chain, so an edited, removed or
reordered row is detected even when it was changed directly in the database.

Integrity signals are review prompts for an authorised supervisor. They count
patterns (repeated overrides, repeated refused separation-of-duties attempts, a
broken chain); they are never findings against an officer and trigger nothing.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Final, Literal, Protocol

GENESIS: Final = "0" * 64

Category = Literal["read", "write", "approval", "override", "denied"]
CATEGORIES: Final[tuple[Category, ...]] = ("read", "write", "approval", "override", "denied")

SUBJECT_TYPES: Final[tuple[str, ...]] = (
    "decision",
    "evidence_request",
    "evidence",
    "case",
    "ledger",
    "control",
    "export",  # TAX-062 evidence export requests
)

# Decision and evidence-request actions (TAX-046) and their ledger category.
_ACTION_CATEGORY: Final[dict[str, Category]] = {
    "decision.drafted": "write",
    "decision.proposed": "write",
    "decision.approved": "approval",
    "decision.rejected": "approval",
    "decision.withdrawn": "write",
    "decision.executed": "write",
    "decision.execution_failed": "write",
    "evidence_request.recorded": "write",
    # TAX-062: an export is requested (write) and authorised or refused (approval).
    "export.requested": "write",
    "export.approved": "approval",
    "export.rejected": "approval",
}

# Refused attempts that break separation of duties when repeated.
SOD_DENIALS: Final[frozenset[str]] = frozenset(
    {"decision.approve", "override.record", "export.approve"}
)

REVIEW_ONLY_NOTE: Final = (
    "A review signal for an authorised supervisor. It is not a finding against any "
    "officer and does not change any case, decision or access."
)

DEFAULT_WINDOW_DAYS: Final = 30
OVERRIDE_THRESHOLD: Final = 3
SOD_ATTEMPT_THRESHOLD: Final = 2


def category_for(action: str) -> Category:
    if action in _ACTION_CATEGORY:
        return _ACTION_CATEGORY[action]
    if action.endswith(".read"):
        return "read"
    if action.endswith(".denied"):
        return "denied"
    if action.startswith("override."):
        return "override"
    return "write"


def digest(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chain_key(tenant_id: str, chain_seq: int) -> str:
    """Unique per (tenant, position): two writers cannot take the same position."""
    return hashlib.sha256(f"ledger|{tenant_id}|{chain_seq}".encode()).hexdigest()


def _utc_seconds(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC).replace(microsecond=0)


def iso(value: datetime.datetime | None) -> str | None:
    if value is None:
        return None
    return _utc_seconds(value).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- records ------------------------------------------------------------------------


@dataclass(frozen=True)
class Unsealed:
    """An entry before it takes its position in the tenant chain."""

    event_id: str
    tenant_id: str
    action: str
    outcome: str
    actor: str
    actor_role: str
    occurred_at: datetime.datetime
    correlation_id: str
    subject_type: str
    subject_id: str
    payload_hash: str
    case_id: str | None = None
    taxpayer_id: str | None = None
    sequence: int | None = None
    reason: str | None = None
    target_actor: str | None = None
    category: Category | None = None

    def resolved_category(self) -> Category:
        return self.category or category_for(self.action)


@dataclass(frozen=True)
class LedgerEntry:
    event_id: str
    tenant_id: str
    chain_seq: int
    prev_hash: str
    entry_hash: str
    category: Category
    action: str
    outcome: str
    actor: str
    actor_role: str
    occurred_at: datetime.datetime
    correlation_id: str
    subject_type: str
    subject_id: str
    payload_hash: str
    case_id: str | None = None
    taxpayer_id: str | None = None
    sequence: int | None = None
    reason: str | None = None
    target_actor: str | None = None


_HASHED_FIELDS: Final[tuple[str, ...]] = (
    "event_id",
    "tenant_id",
    "chain_seq",
    "prev_hash",
    "category",
    "action",
    "outcome",
    "actor",
    "actor_role",
    "occurred_at",
    "correlation_id",
    "subject_type",
    "subject_id",
    "payload_hash",
    "case_id",
    "taxpayer_id",
    "sequence",
    "reason",
    "target_actor",
)


def compute_hash(entry: LedgerEntry) -> str:
    values = asdict(entry)
    material = {name: values[name] for name in _HASHED_FIELDS}
    material["occurred_at"] = iso(entry.occurred_at)
    material["sequence"] = entry.sequence or None
    return digest(material)


def seal(record: Unsealed, head: LedgerEntry | None) -> LedgerEntry:
    """Place ``record`` after ``head`` (the tenant's last entry, or None)."""
    if head is not None and head.tenant_id != record.tenant_id:
        raise ValueError("A ledger chain never crosses tenants.")
    category = record.resolved_category()
    if category not in CATEGORIES:
        raise ValueError(f"Unknown ledger category: {category}")
    if record.subject_type not in SUBJECT_TYPES:
        raise ValueError(f"Unknown ledger subject type: {record.subject_type}")
    entry = LedgerEntry(
        event_id=record.event_id,
        tenant_id=record.tenant_id,
        chain_seq=(head.chain_seq + 1) if head else 1,
        prev_hash=head.entry_hash if head else GENESIS,
        entry_hash="",
        category=category,
        action=record.action,
        outcome=record.outcome,
        actor=record.actor,
        actor_role=record.actor_role,
        occurred_at=_utc_seconds(record.occurred_at),
        correlation_id=record.correlation_id,
        subject_type=record.subject_type,
        subject_id=record.subject_id,
        payload_hash=record.payload_hash,
        case_id=record.case_id,
        taxpayer_id=record.taxpayer_id,
        sequence=record.sequence or None,
        reason=record.reason,
        target_actor=record.target_actor,
    )
    return replace(entry, entry_hash=compute_hash(entry))


# --- verification -------------------------------------------------------------------


def verify_chain(entries: Sequence[LedgerEntry]) -> dict[str, Any]:
    """Recompute one tenant's chain. ``intact`` is False on any edit, gap or reorder.

    Problems are reported by position and code only (``hash_mismatch``,
    ``broken_link``, ``missing_entry``, ``duplicate_position``,
    ``mixed_tenants``); entry content is never echoed.
    """
    ordered = sorted(entries, key=lambda e: e.chain_seq)
    problems: list[dict[str, Any]] = []
    tenants = {e.tenant_id for e in ordered}
    if len(tenants) > 1:
        problems.append({"chain_seq": None, "code": "mixed_tenants"})
    expected_seq = 1
    prev_hash = GENESIS
    seen: set[int] = set()
    for entry in ordered:
        if entry.chain_seq in seen:
            problems.append({"chain_seq": entry.chain_seq, "code": "duplicate_position"})
            continue
        seen.add(entry.chain_seq)
        if entry.chain_seq != expected_seq:
            problems.append({"chain_seq": expected_seq, "code": "missing_entry"})
        elif entry.prev_hash != prev_hash:
            problems.append({"chain_seq": entry.chain_seq, "code": "broken_link"})
        if compute_hash(entry) != entry.entry_hash:
            problems.append({"chain_seq": entry.chain_seq, "code": "hash_mismatch"})
        expected_seq = entry.chain_seq + 1
        prev_hash = entry.entry_hash
    head = ordered[-1] if ordered else None
    return {
        "intact": not problems,
        "entries_checked": len(ordered),
        "head_seq": head.chain_seq if head else 0,
        "head_hash": head.entry_hash if head else GENESIS,
        "problems": problems,
    }


# --- review-only integrity signals --------------------------------------------------


def _signal(
    kind: str,
    *,
    actor: str | None,
    hits: list[LedgerEntry],
    threshold: int,
    window_start: datetime.datetime,
    window_end: datetime.datetime,
) -> dict[str, Any]:
    return {
        "signal_id": digest([kind, actor, iso(window_start), iso(window_end)])[:32],
        "kind": kind,
        "status": "review_only",
        "subject_actor": actor,
        "count": len(hits),
        "threshold": threshold,
        "window_start": iso(window_start),
        "window_end": iso(window_end),
        "first_at": iso(hits[0].occurred_at) if hits else None,
        "last_at": iso(hits[-1].occurred_at) if hits else None,
        "event_ids": [e.event_id for e in hits],
        "note": REVIEW_ONLY_NOTE,
    }


def integrity_signals(
    entries: Sequence[LedgerEntry],
    *,
    now: datetime.datetime,
    viewer: str,
    window_days: int = DEFAULT_WINDOW_DAYS,
    override_threshold: int = OVERRIDE_THRESHOLD,
    sod_threshold: int = SOD_ATTEMPT_THRESHOLD,
) -> dict[str, Any]:
    """Patterns for a supervisor to review. Signals about the viewer are withheld.

    A supervisor never reviews their own pattern; ``withheld_about_viewer``
    says how many exist so another supervisor can be asked to look.
    """
    if not 1 <= window_days <= 365:
        raise ValueError("window_days must be between 1 and 365")
    window_end = _utc_seconds(now)
    window_start = window_end - datetime.timedelta(days=window_days)
    in_window = sorted(
        (e for e in entries if window_start <= _utc_seconds(e.occurred_at) <= window_end),
        key=lambda e: e.chain_seq,
    )
    overrides: dict[str, list[LedgerEntry]] = defaultdict(list)
    sod_attempts: dict[str, list[LedgerEntry]] = defaultdict(list)
    for entry in in_window:
        if entry.category == "override":
            overrides[entry.actor.lower()].append(entry)
        elif entry.category == "denied" and entry.action.removesuffix(".denied") in SOD_DENIALS:
            sod_attempts[entry.actor.lower()].append(entry)
    signals: list[dict[str, Any]] = []
    for actor, hits in sorted(overrides.items()):
        if len(hits) >= override_threshold:
            signals.append(
                _signal(
                    "repeated_overrides",
                    actor=actor,
                    hits=hits,
                    threshold=override_threshold,
                    window_start=window_start,
                    window_end=window_end,
                )
            )
    for actor, hits in sorted(sod_attempts.items()):
        if len(hits) >= sod_threshold:
            signals.append(
                _signal(
                    "repeated_separation_of_duties_attempts",
                    actor=actor,
                    hits=hits,
                    threshold=sod_threshold,
                    window_start=window_start,
                    window_end=window_end,
                )
            )
    integrity = verify_chain(entries)
    if not integrity["intact"]:
        signals.append(
            {
                **_signal(
                    "ledger_integrity",
                    actor=None,
                    hits=[],
                    threshold=1,
                    window_start=window_start,
                    window_end=window_end,
                ),
                "count": len(integrity["problems"]),
                "problems": integrity["problems"],
            }
        )
    visible = [s for s in signals if s["subject_actor"] != viewer.lower()]
    return {
        "status": "review_only",
        "note": REVIEW_ONLY_NOTE,
        "window_start": iso(window_start),
        "window_end": iso(window_end),
        "signals": visible,
        "withheld_about_viewer": len(signals) - len(visible),
    }


def summary(
    entries: Sequence[LedgerEntry],
    *,
    since: datetime.datetime,
    until: datetime.datetime,
) -> dict[str, Any]:
    """Counts for the management view (TAX-060). Measured from the ledger only."""
    if since > until:
        raise ValueError("since must not be after until")
    lo, hi = _utc_seconds(since), _utc_seconds(until)
    window = [e for e in entries if lo <= _utc_seconds(e.occurred_at) <= hi]
    by_category = {c: 0 for c in CATEGORIES}
    by_action: dict[str, int] = defaultdict(int)
    for entry in window:
        by_category[entry.category] += 1
        by_action[entry.action] += 1
    integrity = verify_chain(entries)
    return {
        "since": iso(lo),
        "until": iso(hi),
        "entries": len(window),
        "by_category": by_category,
        "by_action": dict(sorted(by_action.items())),
        "distinct_actors": len({e.actor.lower() for e in window}),
        "ledger_intact": integrity["intact"],
        "head_seq": integrity["head_seq"],
    }


def entry_view(entry: LedgerEntry) -> dict[str, Any]:
    return {
        "event_id": entry.event_id,
        "chain_seq": entry.chain_seq,
        "category": entry.category,
        "action": entry.action,
        "outcome": entry.outcome,
        "actor": entry.actor,
        "actor_role": entry.actor_role,
        "target_actor": entry.target_actor,
        "occurred_at": iso(entry.occurred_at),
        "correlation_id": entry.correlation_id,
        "subject_type": entry.subject_type,
        "subject_id": entry.subject_id,
        "case_id": entry.case_id,
        "case_version": entry.sequence,
        "reason": entry.reason,
        "payload_hash": entry.payload_hash,
        "prev_hash": entry.prev_hash,
        "entry_hash": entry.entry_hash,
    }


# --- storage port -------------------------------------------------------------------


class Duplicate(Exception):
    """A unique key (chain position, case version, event ID) is already taken."""


class LedgerStore(Protocol):
    def atomic(self) -> AbstractContextManager[None]: ...

    def head(self, tenant_id: str) -> LedgerEntry | None:
        """The tenant's last entry, read with a lock where the store supports one."""
        ...

    def insert(self, entry: LedgerEntry) -> None: ...

    def entries(self, tenant_id: str) -> list[LedgerEntry]: ...


def append(store: LedgerStore, record: Unsealed, *, attempts: int = 3) -> LedgerEntry:
    """Seal ``record`` after the tenant head and store it.

    If the insert clashes and the head moved (another writer took the
    position), retry at the new head. A clash with an unchanged head is a
    different unique key (for example the case version) and is raised.
    """
    for _ in range(attempts):
        head = store.head(record.tenant_id)
        entry = seal(record, head)
        try:
            store.insert(entry)
        except Duplicate:
            latest = store.head(record.tenant_id)
            moved = (latest.entry_hash if latest else GENESIS) != (
                head.entry_hash if head else GENESIS
            )
            if moved:
                continue
            raise
        return entry
    raise Duplicate("ledger head kept moving; retry the request")


@dataclass
class MemoryLedger:
    """``LedgerStore`` in memory with the same unique keys as the DocType."""

    rows: list[LedgerEntry] = field(default_factory=list)

    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        snapshot = list(self.rows)
        try:
            yield
        except BaseException:
            self.rows = snapshot
            raise

    def head(self, tenant_id: str) -> LedgerEntry | None:
        mine = [e for e in self.rows if e.tenant_id == tenant_id]
        return max(mine, key=lambda e: e.chain_seq) if mine else None

    def insert(self, entry: LedgerEntry) -> None:
        for other in self.rows:
            if other.event_id == entry.event_id:
                raise Duplicate("event_id")
            if other.tenant_id != entry.tenant_id:
                continue
            if other.chain_seq == entry.chain_seq:
                raise Duplicate("chain_key")
            if (
                entry.sequence
                and other.case_id == entry.case_id
                and other.sequence == entry.sequence
            ):
                raise Duplicate("sequence_key")
        self.rows.append(entry)

    def entries(self, tenant_id: str) -> list[LedgerEntry]:
        return sorted((e for e in self.rows if e.tenant_id == tenant_id), key=lambda e: e.chain_seq)


def new_event_id() -> str:
    return str(uuid.uuid4())
