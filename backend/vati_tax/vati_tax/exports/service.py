"""Evidence export workflow with policy checks (TAX-062), independent of Frappe.

``store.py`` binds the ports below to Frappe; tests bind them to the in-memory
classes at the end of this module. Every step is recorded in the TAX-059 audit
ledger, including refusals, so a supervisor can see who asked to export what,
who authorised it and who read the manifest.

- ``request_export``: an auditor, specialist or supervisor names the evidence of
  one case, a purpose, a recipient and a reason. Evidence outside that case,
  taxpayer or tenant is refused as ``not_found`` and the attempt is recorded.
- ``approve_export`` / ``reject_export``: a supervisor who did not make the
  request decides. Approval re-checks the scope and freezes the manifest.
- ``get_manifest``: the requester or the approver reads the frozen manifest for
  24 hours after approval. Each read is recorded.

No step exports document bytes. The manifest is the authorised, verifiable
description of what may be handed over; copying the files out is a separate,
human-controlled act outside this prototype.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, TypeVar

from vati_tax.audit import ledger
from vati_tax.audit import service as audit
from vati_tax.audit.ledger import LedgerStore
from vati_tax.exports import rules
from vati_tax.exports.rules import EvidenceItem, ExportRecord
from vati_tax.identity import policy
from vati_tax.ids.canonical import IdempotencyConflict, MappingError, canonical_uuid

T = TypeVar("T")
SUBJECT = "export"


class ExportStore(Protocol):
    def atomic(self) -> contextlib.AbstractContextManager[None]: ...

    def get(self, tenant_id: str, export_id: str) -> ExportRecord | None: ...

    def insert(self, record: ExportRecord) -> None:
        """Store a new record; raise ``ledger.Duplicate`` if the ID exists."""
        ...

    def update(self, record: ExportRecord, expected_version: int) -> bool:
        """Replace the stored record if it is still at ``expected_version``."""
        ...


class EvidenceSource(Protocol):
    def case_exists(self, tenant_id: str, case_id: str) -> bool: ...

    def case_evidence(self, tenant_id: str, case_id: str) -> list[EvidenceItem]: ...


@dataclass(frozen=True)
class Ports:
    exports: ExportStore
    evidence: EvidenceSource
    ledger: LedgerStore
    schedule: Mapping[str, int | None]


# --- ledger helpers -----------------------------------------------------------------


def _append(
    ctx: audit.Context,
    ports: Ports,
    *,
    action: str,
    outcome: str,
    record: ExportRecord,
    payload: Any,
    privilege: str,
    reason: str | None = None,
) -> None:
    ledger.append(
        ports.ledger,
        ledger.Unsealed(
            event_id=ledger.new_event_id(),
            tenant_id=ctx.scope.tenant_id,
            action=action,
            outcome=outcome,
            actor=ctx.scope.user,
            actor_role=audit.acting_role(ctx.scope, privilege),
            occurred_at=ctx.now,
            correlation_id=ctx.correlation_id,
            subject_type=SUBJECT,
            subject_id=record.export_id,
            payload_hash=ledger.digest(payload),
            case_id=record.case_id,
            reason=reason,
        ),
    )


def _refuse(
    ctx: audit.Context,
    ports: Ports,
    *,
    attempted: str,
    export_id: str,
    case_id: str | None,
    exc: MappingError,
) -> None:
    """Record a refusal that is not a policy denial (scope, state) and re-raise."""
    audit.record_denied(
        ctx,
        ports.ledger,
        attempted_action=attempted,
        subject_type=SUBJECT,
        subject_id=export_id,
        code=exc.field_errors[0]["code"] if exc.field_errors else exc.code,
        case_id=case_id,
    )
    raise exc


def _authorise(
    ctx: audit.Context,
    ports: Ports,
    *,
    action: str,
    export_id: str,
    case_id: str | None,
    fn: Callable[[], T],
) -> T:
    return audit.guarded(
        ctx,
        ports.ledger,
        attempted_action=action,
        subject_type=SUBJECT,
        subject_id=export_id,
        case_id=case_id,
        fn=fn,
    )


def _load(ctx: audit.Context, ports: Ports, export_id: object) -> ExportRecord:
    eid = canonical_uuid(export_id, "export_id")
    record = ports.exports.get(ctx.scope.tenant_id, eid)
    if record is None or record.tenant_id != ctx.scope.tenant_id:
        raise rules.ExportNotFound("Export not found.", "export_id", "not_found")
    return record


# --- request ------------------------------------------------------------------------


def request_export(
    ctx: audit.Context, ports: Ports, *, body: Mapping[str, Any], idempotency_key: str
) -> dict[str, Any]:
    """Record an export request. Replaying the same key returns the stored request."""
    request = rules.ExportRequest.build(body, idempotency_key)
    eid = rules.export_id(ctx.scope.tenant_id, request.idempotency_key)
    _authorise(
        ctx,
        ports,
        action=policy.EXPORT_REQUEST,
        export_id=eid,
        case_id=request.case_id,
        fn=lambda: policy.authorize_action(ctx.scope, policy.EXPORT_REQUEST),
    )
    existing = ports.exports.get(ctx.scope.tenant_id, eid)
    if existing is not None:
        if (
            existing.request_digest != request.digest()
            or existing.requested_by.lower() != ctx.scope.user.lower()
        ):
            raise IdempotencyConflict(
                "This idempotency key was used for a different request.",
                "idempotency_key",
                "idempotency_conflict",
            )
        return {**rules.export_view(existing, ctx.now), "replayed": True}
    try:
        if not ports.evidence.case_exists(ctx.scope.tenant_id, request.case_id):
            raise rules.ExportNotFound("Case not found.", "case_id", "not_found")
        rules.check_scope(
            request.evidence_ids,
            ctx.scope.tenant_id,
            ports.evidence.case_evidence(ctx.scope.tenant_id, request.case_id),
        )
    except MappingError as exc:
        _refuse(
            ctx,
            ports,
            attempted=policy.EXPORT_REQUEST,
            export_id=eid,
            case_id=request.case_id,
            exc=exc,
        )
    record = ExportRecord(
        export_id=eid,
        tenant_id=ctx.scope.tenant_id,
        case_id=request.case_id,
        evidence_ids=request.evidence_ids,
        purpose=request.purpose,
        recipient=request.recipient,
        reason=request.reason,
        request_digest=request.digest(),
        requested_by=ctx.scope.user,
        requested_at=rules.utc(ctx.now),
    )
    with ports.exports.atomic():
        ports.exports.insert(record)
        _append(
            ctx,
            ports,
            action="export.requested",
            outcome="requested",
            record=record,
            payload=request.payload(),
            privilege=policy.EXPORT_REQUEST,
            reason=request.reason,
        )
    return {**rules.export_view(record, ctx.now), "replayed": False}


# --- decide -------------------------------------------------------------------------


def _decide(
    ctx: audit.Context,
    ports: Ports,
    *,
    export_id: object,
    expected_version: object,
    approve: bool,
    reason: str | None,
) -> dict[str, Any]:
    record = _load(ctx, ports, export_id)
    _authorise(
        ctx,
        ports,
        action=policy.EXPORT_APPROVE,
        export_id=record.export_id,
        case_id=record.case_id,
        fn=lambda: policy.authorize_action(
            ctx.scope, policy.EXPORT_APPROVE, proposed_by=record.requested_by
        ),
    )
    text: str | None = None
    if reason is not None or not approve:
        text = rules.required_text({"reason": reason}, "reason", rules.REASON_MIN, rules.REASON_MAX)
    manifest = None
    try:
        rules.check_decidable(record, expected_version, ctx.now)
        if approve:
            # The case's evidence may have changed since the request: re-check.
            items = rules.check_scope(
                record.evidence_ids,
                ctx.scope.tenant_id,
                ports.evidence.case_evidence(ctx.scope.tenant_id, record.case_id),
            )
            manifest = rules.build_manifest(
                record,
                items,
                approved_by=ctx.scope.user,
                approved_at=rules.utc(ctx.now),
                schedule=ports.schedule,
            )
    except MappingError as exc:
        _refuse(
            ctx,
            ports,
            attempted=policy.EXPORT_APPROVE,
            export_id=record.export_id,
            case_id=record.case_id,
            exc=exc,
        )
    decided = replace(
        record,
        status="approved" if approve else "rejected",
        version=record.version + 1,
        decided_by=ctx.scope.user,
        decided_at=rules.utc(ctx.now),
        decision_reason=text,
        manifest=manifest,
        manifest_sha256=manifest["manifest_sha256"] if manifest else None,
    )
    with ports.exports.atomic():
        if not ports.exports.update(decided, record.version):
            raise rules.ExportConflict(
                "The export changed since you loaded it.", "expected_version", "stale_version"
            )
        _append(
            ctx,
            ports,
            action="export.approved" if approve else "export.rejected",
            outcome=decided.status,
            record=decided,
            payload={"version": decided.version, "manifest_sha256": decided.manifest_sha256},
            privilege=policy.EXPORT_APPROVE,
            reason=text,
        )
    return rules.export_view(decided, ctx.now)


def approve_export(
    ctx: audit.Context,
    ports: Ports,
    *,
    export_id: object,
    expected_version: object,
    reason: str | None = None,
) -> dict[str, Any]:
    return _decide(
        ctx,
        ports,
        export_id=export_id,
        expected_version=expected_version,
        approve=True,
        reason=reason,
    )


def reject_export(
    ctx: audit.Context,
    ports: Ports,
    *,
    export_id: object,
    expected_version: object,
    reason: str | None,
) -> dict[str, Any]:
    return _decide(
        ctx,
        ports,
        export_id=export_id,
        expected_version=expected_version,
        approve=False,
        reason=reason,
    )


# --- read ---------------------------------------------------------------------------


def get_export(ctx: audit.Context, ports: Ports, *, export_id: object) -> dict[str, Any]:
    """Status of an export for staff of the tenant (no manifest)."""
    record = _load(ctx, ports, export_id)
    _authorise(
        ctx,
        ports,
        action=policy.EXPORT_READ,
        export_id=record.export_id,
        case_id=record.case_id,
        fn=lambda: policy.authorize_action(ctx.scope, policy.EXPORT_READ),
    )
    return rules.export_view(record, ctx.now)


def get_manifest(ctx: audit.Context, ports: Ports, *, export_id: object) -> dict[str, Any]:
    """The frozen manifest, for the requester or approver, recorded as a read."""
    record = _load(ctx, ports, export_id)
    _authorise(
        ctx,
        ports,
        action=policy.EXPORT_READ,
        export_id=record.export_id,
        case_id=record.case_id,
        fn=lambda: policy.authorize_action(ctx.scope, policy.EXPORT_READ),
    )
    try:
        rules.check_readable(record, ctx.scope.user, ctx.now)
        manifest = record.manifest or {}
        if not rules.verify_manifest(manifest) or (
            manifest.get("manifest_sha256") != record.manifest_sha256
        ):
            raise rules.ExportConflict(
                "The stored manifest no longer matches its hash.", "export_id", "manifest_altered"
            )
    except MappingError as exc:
        _refuse(
            ctx,
            ports,
            attempted=policy.EXPORT_READ,
            export_id=record.export_id,
            case_id=record.case_id,
            exc=exc,
        )
    audit.record_read(
        ctx,
        ports.ledger,
        action="export.read",
        subject_type=SUBJECT,
        subject_id=record.export_id,
        case_id=record.case_id,
        detail={"manifest_sha256": record.manifest_sha256},
    )
    return dict(manifest)


def retention_policy(ctx: audit.Context, ports: Ports) -> dict[str, Any]:
    """The retention schedule in force, for staff (no record data)."""
    if not ctx.scope.roles & policy.STAFF_ROLES:
        raise policy.ActionDenied("Only staff read the retention policy")
    return rules.retention_view(ports.schedule)


# --- in-memory ports (tests, and a reference for the Frappe binding) ----------------


@dataclass
class MemoryExports:
    rows: dict[tuple[str, str], ExportRecord] = field(default_factory=dict)

    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        snapshot = dict(self.rows)
        try:
            yield
        except BaseException:
            self.rows = snapshot
            raise

    def get(self, tenant_id: str, export_id: str) -> ExportRecord | None:
        return self.rows.get((tenant_id, export_id))

    def insert(self, record: ExportRecord) -> None:
        if any(eid == record.export_id for _, eid in self.rows):
            raise ledger.Duplicate("export_id")
        self.rows[(record.tenant_id, record.export_id)] = record

    def update(self, record: ExportRecord, expected_version: int) -> bool:
        current = self.rows.get((record.tenant_id, record.export_id))
        if current is None or current.version != expected_version:
            return False
        self.rows[(record.tenant_id, record.export_id)] = record
        return True


@dataclass
class MemoryEvidence:
    """Evidence by (tenant, case). A case exists once it has an entry."""

    cases: dict[tuple[str, str], list[EvidenceItem]] = field(default_factory=dict)

    def add(self, tenant_id: str, case_id: str, *items: EvidenceItem) -> None:
        self.cases.setdefault((tenant_id, case_id), []).extend(items)

    def case_exists(self, tenant_id: str, case_id: str) -> bool:
        return (tenant_id, case_id) in self.cases

    def case_evidence(self, tenant_id: str, case_id: str) -> list[EvidenceItem]:
        return list(self.cases.get((tenant_id, case_id), []))
