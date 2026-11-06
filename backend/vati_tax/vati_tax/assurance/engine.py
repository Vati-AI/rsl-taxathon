"""Lane proposals and assurance runs, recorded idempotently (TAX-121), independent of Frappe.

``store`` binds ``Repository`` to the ``Vati Lane Proposal`` and ``Vati Assurance Run``
DocTypes; tests bind it to ``MemoryRepository``, which enforces the same unique keys.

- A lane proposal is append-only. The same claim, check version and inputs are a replay
  (nothing written); changed inputs append a new proposal that supersedes the previous one
  for that claim. Recording needs ``draft.record`` (service or staff); reading
  ``case.read``. A proposal decides nothing: an officer reviews every lane.
- One assurance run per outbox event: what ran, which services answered, what was
  recorded. A replayed event returns the stored run.
"""

from __future__ import annotations

import contextlib
import datetime
import uuid
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol

from vati_tax.assurance import rules
from vati_tax.identity import policy
from vati_tax.ids.canonical import CaseNotFound, MappingError, canonical_uuid


class Duplicate(Exception):
    """The repository refused a write because a unique key is already taken."""


class ProposalConflict(MappingError):
    code = "version_conflict"
    http_status = 409


@dataclass(frozen=True)
class LaneRecord:
    proposal_id: str
    tenant_id: str
    case_id: str
    claim_ref: str
    revision: int
    supersedes_proposal_id: str | None
    body: dict[str, Any]
    producer: str
    producer_version: str
    input_digest: str
    run_key: str
    created_at: datetime.datetime
    correlation_id: str
    proposal_key: str
    request_digest: str


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    tenant_id: str
    case_id: str
    submission_id: str
    claim_ref: str | None
    status: str
    reason: str | None
    services: dict[str, Any]
    checks: dict[str, str]
    summary: dict[str, Any]
    finding_ids: tuple[str, ...]
    lane_proposal_id: str | None
    producer_version: str | None
    input_digest: str | None
    event_id: str
    correlation_id: str
    started_at: datetime.datetime
    seconds: float
    run_key: str


class Repository(Protocol):
    def atomic(self) -> AbstractContextManager[None]: ...

    def lane_by_key(self, proposal_key: str) -> LaneRecord | None: ...

    def latest_lane(self, tenant_id: str, case_id: str, claim_ref: str) -> LaneRecord | None: ...

    def insert_lane(self, record: LaneRecord) -> None: ...

    def case_lanes(self, tenant_id: str, case_id: str) -> list[LaneRecord]: ...

    def run_by_key(self, run_key: str) -> RunRecord | None: ...

    def insert_run(self, record: RunRecord) -> None: ...

    def case_runs(self, tenant_id: str, case_id: str) -> list[RunRecord]: ...


def lane_key(tenant_id: str, case_id: str, claim_ref: str, version: str, inputs: str) -> str:
    return rules.sha256_text("lane.proposal", tenant_id, case_id, claim_ref, version, inputs)


def run_key(tenant_id: str, event_id: str) -> str:
    return rules.sha256_text("assurance.run", tenant_id, event_id)


def record_lane_proposal(
    scope: policy.TenantScope,
    repo: Repository,
    *,
    case_id: str,
    body: Mapping[str, Any],
    producer_version: str,
    input_digest: str,
    run_key: str,
    now: datetime.datetime,
    correlation_id: str,
) -> dict[str, Any]:
    """Record one claim's lane proposal (or its next revision); a replay writes nothing."""
    policy.authorize_action(scope, policy.DRAFT_RECORD)
    lane = rules.validate_lane(body)
    case_id = canonical_uuid(case_id, "case_id")
    tenant = scope.tenant_id
    claim_ref = str(lane["claim_ref"])
    key = lane_key(tenant, case_id, claim_ref, producer_version, input_digest)
    request = rules.digest({"lane": lane, "version": producer_version, "inputs": input_digest})
    existing = repo.lane_by_key(key)
    if existing is not None:
        return lane_view(existing, replayed=True)
    previous = repo.latest_lane(tenant, case_id, claim_ref)
    if (
        previous is not None
        and previous.body == lane
        and (previous.producer_version == producer_version)
    ):
        return lane_view(previous, replayed=True)
    record = LaneRecord(
        proposal_id=str(uuid.uuid4()),
        tenant_id=tenant,
        case_id=case_id,
        claim_ref=claim_ref,
        revision=previous.revision + 1 if previous else 1,
        supersedes_proposal_id=previous.proposal_id if previous else None,
        body=lane,
        producer=rules.PRODUCER,
        producer_version=producer_version,
        input_digest=input_digest,
        run_key=run_key,
        created_at=now,
        correlation_id=correlation_id,
        proposal_key=key,
        request_digest=request,
    )
    try:
        with repo.atomic():
            repo.insert_lane(record)
    except Duplicate:
        stored = repo.lane_by_key(key)
        if stored is not None:
            return lane_view(stored, replayed=True)
        raise ProposalConflict(
            "Another lane proposal for this claim was recorded first; retry.",
            "claim_ref",
            "lane_revision_conflict",
        ) from None
    return lane_view(record, replayed=False)


def record_run(repo: Repository, record: RunRecord) -> dict[str, Any]:
    """Keep what one assurance step did; the same event returns the stored run."""
    existing = repo.run_by_key(record.run_key)
    if existing is not None:
        return run_view(existing, replayed=True)
    try:
        with repo.atomic():
            repo.insert_run(record)
    except Duplicate:
        stored = repo.run_by_key(record.run_key)
        if stored is None:
            raise
        return run_view(stored, replayed=True)
    return run_view(record, replayed=False)


def _case_scope(scope: policy.TenantScope, case: Mapping[str, Any] | None) -> None:
    policy.authorize_action(scope, policy.CASE_READ)
    if case is None or case.get("tenant_id") != scope.tenant_id:
        raise CaseNotFound("Case not found.", "case_id", "not_found")


def case_assurance(
    scope: policy.TenantScope,
    repo: Repository,
    *,
    case: Mapping[str, Any] | None,
    include_superseded: bool = False,
) -> dict[str, Any]:
    """Runs and lane proposals of one case (staff and service; never taxpayers)."""
    _case_scope(scope, case)
    assert case is not None
    case_id = str(case["case_id"])
    lanes = repo.case_lanes(scope.tenant_id, case_id)
    replaced = {lane.supersedes_proposal_id for lane in lanes if lane.supersedes_proposal_id}
    return {
        "tenant_id": scope.tenant_id,
        "case_id": case_id,
        "runs": [run_view(r) for r in repo.case_runs(scope.tenant_id, case_id)],
        "lane_proposals": [
            lane_view(lane, current=lane.proposal_id not in replaced)
            for lane in lanes
            if include_superseded or lane.proposal_id not in replaced
        ],
        "decision": None,
    }


def lane_view(
    record: LaneRecord, *, replayed: bool | None = None, current: bool | None = None
) -> dict[str, Any]:
    view: dict[str, Any] = {
        "proposal_id": record.proposal_id,
        "tenant_id": record.tenant_id,
        "case_id": record.case_id,
        "revision": record.revision,
        "supersedes_proposal_id": record.supersedes_proposal_id,
        **record.body,
        "producer": record.producer,
        "producer_version": record.producer_version,
        "input_digest": record.input_digest,
        "created_at": _iso(record.created_at),
        "correlation_id": record.correlation_id,
        "advisory": True,
        "decision": None,
    }
    if replayed is not None:
        view["replayed"] = replayed
    if current is not None:
        view["current"] = current
    return view


def run_view(record: RunRecord, *, replayed: bool | None = None) -> dict[str, Any]:
    view: dict[str, Any] = {
        "run_id": record.run_id,
        "tenant_id": record.tenant_id,
        "case_id": record.case_id,
        "submission_id": record.submission_id,
        "claim_ref": record.claim_ref,
        "status": record.status,
        "reason": record.reason,
        "services": record.services,
        "checks": record.checks,
        "summary": record.summary,
        "finding_ids": list(record.finding_ids),
        "lane_proposal_id": record.lane_proposal_id,
        "producer": rules.PRODUCER,
        "producer_version": record.producer_version,
        "input_digest": record.input_digest,
        "event_id": record.event_id,
        "correlation_id": record.correlation_id,
        "started_at": _iso(record.started_at),
        "seconds": record.seconds,
    }
    if replayed is not None:
        view["replayed"] = replayed
    return view


def _iso(value: datetime.datetime) -> str:
    return value.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class MemoryRepository:
    """Same unique keys as the DocTypes (proposal key; run key; lane revision per claim)."""

    lanes: list[LaneRecord] = field(default_factory=list)
    runs: list[RunRecord] = field(default_factory=list)

    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        lanes, runs = list(self.lanes), list(self.runs)
        try:
            yield
        except BaseException:
            self.lanes, self.runs = lanes, runs
            raise

    def lane_by_key(self, proposal_key: str) -> LaneRecord | None:
        return next((r for r in self.lanes if r.proposal_key == proposal_key), None)

    def latest_lane(self, tenant_id: str, case_id: str, claim_ref: str) -> LaneRecord | None:
        rows = [r for r in self.lanes
                if (r.tenant_id, r.case_id, r.claim_ref) == (tenant_id, case_id, claim_ref)]  # fmt: skip
        return max(rows, key=lambda r: r.revision) if rows else None

    def insert_lane(self, record: LaneRecord) -> None:
        for r in self.lanes:
            if r.proposal_key == record.proposal_key or (
                (r.tenant_id, r.case_id, r.claim_ref, r.revision)
                == (record.tenant_id, record.case_id, record.claim_ref, record.revision)
            ):
                raise Duplicate(record.proposal_key)
        self.lanes.append(record)

    def case_lanes(self, tenant_id: str, case_id: str) -> list[LaneRecord]:
        return [r for r in self.lanes if (r.tenant_id, r.case_id) == (tenant_id, case_id)]

    def run_by_key(self, run_key: str) -> RunRecord | None:
        return next((r for r in self.runs if r.run_key == run_key), None)

    def insert_run(self, record: RunRecord) -> None:
        if any(r.run_key == record.run_key for r in self.runs):
            raise Duplicate(record.run_key)
        self.runs.append(record)

    def case_runs(self, tenant_id: str, case_id: str) -> list[RunRecord]:
        return [r for r in self.runs if (r.tenant_id, r.case_id) == (tenant_id, case_id)]
