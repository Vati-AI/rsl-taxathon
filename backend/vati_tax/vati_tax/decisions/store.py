"""Frappe binding for decisions, approvals and evidence requests (TAX-046).

``FrappeRepository`` implements ``engine.Repository`` on the ``Vati Decision``,
``Vati Evidence Request`` and ``Vati Audit Event`` DocTypes. The public
functions resolve the session user's tenant scope (TAX-029) and run the
engine, which authorises every action with the policy matrix.

Inserts use ``ignore_permissions`` because the DocTypes are read-only in desk;
the engine's action, tenant and separation-of-duties checks replace the
DocType create permission. Unique keys (idempotency, one open decision per
case, one action per case version) are database indexes, so a concurrent
writer loses with ``Duplicate`` rather than silently doubling an action.

The Jube case lifecycle adapter (TAX-034, ``integration/jube/case_lifecycle.py``)
lives outside this app. A site names a factory in ``site_config.json``::

    "vati_case_lifecycle": "dotted.path.to.factory"

``factory(tenant_id)`` returns an object with the adapter's ``get_case``,
``transition`` and ``escalate``. Without one, decisions that need Jube are
refused as ``upstream_unavailable`` and nothing is recorded. The factory is
``vati_tax.integrations.factories.case_lifecycle`` (TAX-085), which
``scripts/frappe_dev.py init`` names and configures from ignored local config.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import frappe

from vati_tax.audit import ledger
from vati_tax.audit import service as audit_service
from vati_tax.audit import store as audit_store
from vati_tax.decisions import engine, rules
from vati_tax.decisions.engine import (
    AuditEvent,
    CaseRef,
    DecisionRecord,
    Duplicate,
    EvidenceRequestRecord,
)
from vati_tax.identity import permissions, policy

DECISION = "Vati Decision"
REQUEST = "Vati Evidence Request"
AUDIT = "Vati Audit Event"
CASE = "Vati Case Identity"
EVIDENCE = "Vati Evidence"
_UNIQUE = (frappe.UniqueValidationError, frappe.DuplicateEntryError)

# Tests and local smoke runs can inject a lifecycle without site config.
_lifecycle_override: Callable[[str], engine.CaseLifecycle] | None = None


def set_lifecycle_factory(factory: Callable[[str], engine.CaseLifecycle] | None) -> None:
    global _lifecycle_override
    _lifecycle_override = factory


def lifecycle_for(tenant_id: str) -> engine.CaseLifecycle:
    if _lifecycle_override is not None:
        return _lifecycle_override(tenant_id)
    path = frappe.conf.get("vati_case_lifecycle")
    if not path:
        raise rules.UpstreamUnavailable(
            "The Investigation case connection is not configured for this site.",
            "case_id",
            "jube_not_configured",
        )
    factory: Callable[[str], engine.CaseLifecycle] = frappe.get_attr(path)
    return factory(tenant_id)


def _naive(value: datetime.datetime | None) -> datetime.datetime | None:
    if value is None:
        return None
    return value.astimezone(datetime.UTC).replace(tzinfo=None)


def _aware(value: Any) -> datetime.datetime | None:
    if not value:
        return None
    return frappe.utils.get_datetime(value).replace(tzinfo=datetime.UTC)


def _ids(value: Any) -> tuple[str, ...]:
    return tuple(json.loads(value)) if value else ()


def _bool_text(value: bool | None) -> str | None:
    return None if value is None else ("true" if value else "false")


# --- repository ---------------------------------------------------------------------


class FrappeRepository:
    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        savepoint = "vati_decision_" + uuid.uuid4().hex[:12]
        frappe.db.savepoint(savepoint)
        try:
            yield
        except BaseException:
            frappe.db.rollback(save_point=savepoint)
            raise

    def get_case(self, tenant_id: str, case_id: str) -> CaseRef | None:
        row = frappe.db.get_value(CASE, case_id, ["tenant_id", "taxpayer_id"], as_dict=True)
        if not row:
            return None
        return CaseRef(row.tenant_id, case_id, row.taxpayer_id or None)

    def case_version(self, tenant_id: str, case_id: str) -> int:
        count = frappe.db.count(
            AUDIT, {"tenant_id": tenant_id, "case_id": case_id, "sequence": [">", 0]}
        )
        return 1 + int(count)

    def known_evidence(
        self, tenant_id: str, taxpayer_id: str | None, evidence_ids: tuple[str, ...]
    ) -> set[str]:
        filters: dict[str, Any] = {"name": ["in", list(evidence_ids)], "tenant_id": tenant_id}
        if taxpayer_id:
            filters["taxpayer_id"] = taxpayer_id
        return set(frappe.get_all(EVIDENCE, filters=filters, pluck="name"))

    # decisions

    def _decision(self, filters: Any) -> DecisionRecord | None:
        name = frappe.db.get_value(DECISION, filters, "name")
        if not name:
            return None
        doc = frappe.get_doc(DECISION, name)
        changed = doc.jube_changed
        return DecisionRecord(
            decision_id=doc.name,
            tenant_id=doc.tenant_id,
            case_id=doc.case_id,
            taxpayer_id=doc.taxpayer_id or None,
            status=doc.status,
            origin=doc.origin,
            outcome=doc.outcome,
            rationale=doc.rationale,
            evidence_ids=_ids(doc.evidence_ids),
            finding_ids=_ids(doc.finding_ids),
            rating=int(doc.rating) if doc.rating else None,
            draft_id=doc.draft_id or None,
            proposed_by=doc.proposed_by,
            proposer_role=doc.proposer_role,
            proposed_at=_aware(doc.proposed_at) or datetime.datetime.now(datetime.UTC),
            case_status_at_proposal=doc.case_status_at_proposal or None,
            case_version=int(doc.case_version) if doc.case_version else None,
            registration_key=doc.registration_key,
            request_digest=doc.request_digest,
            open_key=doc.open_key or None,
            reviewed_by=doc.reviewed_by or None,
            reviewer_role=doc.reviewer_role or None,
            reviewed_at=_aware(doc.reviewed_at),
            review_reason=doc.review_reason or None,
            review_key=doc.review_key or None,
            review_digest=doc.review_digest or None,
            sync_state=doc.sync_state,
            sync_attempts=int(doc.sync_attempts or 0),
            sync_error_code=doc.sync_error_code or None,
            sync_retryable=bool(doc.sync_retryable),
            last_attempt_at=_aware(doc.last_attempt_at),
            synced_at=_aware(doc.synced_at),
            jube_changed=None if not changed else changed == "true",
            jube_status_after=doc.jube_status_after or None,
        )

    def get_decision(self, decision_id: str) -> DecisionRecord | None:
        return self._decision({"name": decision_id})

    def decision_by_key(self, registration_key: str) -> DecisionRecord | None:
        return self._decision({"registration_key": registration_key})

    def decision_by_review_key(self, review_key: str) -> DecisionRecord | None:
        return self._decision({"review_key": review_key})

    def open_decision(self, tenant_id: str, case_id: str) -> DecisionRecord | None:
        return self._decision({"open_key": rules.open_decision_key(tenant_id, case_id)})

    _SETTLE = (
        "status",
        "open_key",
        "sync_state",
        "sync_attempts",
        "sync_error_code",
        "sync_retryable",
        "last_attempt_at",
        "synced_at",
        "jube_changed",
        "jube_status_after",
    )
    _REVIEW = (
        "reviewed_by",
        "reviewer_role",
        "reviewed_at",
        "review_reason",
        "review_key",
        "review_digest",
    )

    @staticmethod
    def _fields(record: DecisionRecord) -> dict[str, Any]:
        return {
            "decision_id": record.decision_id,
            "tenant_id": record.tenant_id,
            "taxpayer_id": record.taxpayer_id,
            "case_id": record.case_id,
            "status": record.status,
            "origin": record.origin,
            "outcome": record.outcome,
            "rationale": record.rationale,
            "evidence_ids": json.dumps(list(record.evidence_ids)),
            "finding_ids": json.dumps(list(record.finding_ids)),
            "rating": record.rating,
            "draft_id": record.draft_id,
            "proposed_by": record.proposed_by,
            "proposer_role": record.proposer_role,
            "proposed_at": _naive(record.proposed_at),
            "case_status_at_proposal": record.case_status_at_proposal,
            "case_version": record.case_version,
            "registration_key": record.registration_key,
            "request_digest": record.request_digest,
            "open_key": record.open_key,
            "reviewed_by": record.reviewed_by,
            "reviewer_role": record.reviewer_role,
            "reviewed_at": _naive(record.reviewed_at),
            "review_reason": record.review_reason,
            "review_key": record.review_key,
            "review_digest": record.review_digest,
            "sync_state": record.sync_state,
            "sync_attempts": record.sync_attempts,
            "sync_error_code": record.sync_error_code,
            "sync_retryable": 1 if record.sync_retryable else 0,
            "last_attempt_at": _naive(record.last_attempt_at),
            "synced_at": _naive(record.synced_at),
            "jube_changed": _bool_text(record.jube_changed),
            "jube_status_after": record.jube_status_after,
        }

    def insert_decision(self, record: DecisionRecord) -> None:
        try:
            frappe.get_doc({"doctype": DECISION, **self._fields(record)}).insert(
                ignore_permissions=True
            )
        except _UNIQUE as exc:
            raise Duplicate(str(exc)) from exc

    def save_decision(self, record: DecisionRecord) -> None:
        """Write only what a review or a Jube attempt changes.

        The proposal fields are frozen, and the review is written once, so values
        read back from the database are never round-tripped into a save.
        """
        doc = frappe.get_doc(DECISION, record.decision_id)
        fields = self._fields(record)
        changes = {name: fields[name] for name in self._SETTLE}
        if not doc.review_key and record.review_key:
            changes.update({name: fields[name] for name in self._REVIEW})
        doc.update(changes)
        try:
            doc.save(ignore_permissions=True)
        except _UNIQUE as exc:
            raise Duplicate(str(exc)) from exc

    # evidence requests

    def request_by_key(self, registration_key: str) -> EvidenceRequestRecord | None:
        name = frappe.db.get_value(REQUEST, {"registration_key": registration_key}, "name")
        if not name:
            return None
        doc = frappe.get_doc(REQUEST, name)
        return EvidenceRequestRecord(
            request_id=doc.name,
            tenant_id=doc.tenant_id,
            case_id=doc.case_id,
            taxpayer_id=doc.taxpayer_id,
            status=doc.status,
            authorised_by=doc.authorised_by,
            authoriser_role=doc.authoriser_role,
            finding_ids=_ids(doc.finding_ids),
            items=tuple(rules.RequestedItem(**item) for item in json.loads(doc.items)),
            due_date=frappe.utils.getdate(doc.due_date),
            message_to_taxpayer=doc.message_to_taxpayer,
            case_version=int(doc.case_version),
            created_at=_aware(doc.created_at) or datetime.datetime.now(datetime.UTC),
            registration_key=doc.registration_key,
            request_digest=doc.request_digest,
        )

    def insert_request(self, record: EvidenceRequestRecord) -> None:
        doc = frappe.get_doc(
            {
                "doctype": REQUEST,
                "request_id": record.request_id,
                "tenant_id": record.tenant_id,
                "taxpayer_id": record.taxpayer_id,
                "case_id": record.case_id,
                "status": record.status,
                "authorised_by": record.authorised_by,
                "authoriser_role": record.authoriser_role,
                "case_version": record.case_version,
                "due_date": record.due_date,
                "created_at": _naive(record.created_at),
                "items": json.dumps(
                    [
                        {"document_type": i.document_type, "description": i.description}
                        for i in record.items
                    ]
                ),
                "finding_ids": json.dumps(list(record.finding_ids)),
                "message_to_taxpayer": record.message_to_taxpayer,
                "registration_key": record.registration_key,
                "request_digest": record.request_digest,
            }
        )
        try:
            doc.insert(ignore_permissions=True)
        except _UNIQUE as exc:
            raise Duplicate(str(exc)) from exc

    # audit events

    def append_event(self, event: AuditEvent) -> None:
        # TAX-059: every event takes the next position in the tenant hash chain.
        try:
            ledger.append(audit_store.FrappeLedger(), engine.to_ledger(event))
        except ledger.Duplicate as exc:
            raise Duplicate(str(exc)) from exc

    def case_events(self, tenant_id: str, case_id: str) -> list[AuditEvent]:
        rows = frappe.get_all(
            AUDIT,
            # The decision trail only: reads, overrides and refusals are
            # supervisor-only ledger entries (TAX-059).
            filters={
                "tenant_id": tenant_id,
                "case_id": case_id,
                "category": ["in", ["write", "approval"]],
            },
            fields=[
                "name",
                "taxpayer_id",
                "sequence",
                "actor",
                "actor_role",
                "action",
                "subject_type",
                "subject_id",
                "occurred_at",
                "correlation_id",
                "payload_hash",
                "outcome",
            ],
            order_by="chain_seq asc",
        )
        return [
            AuditEvent(
                event_id=row.name,
                tenant_id=tenant_id,
                case_id=case_id,
                taxpayer_id=row.taxpayer_id or None,
                sequence=int(row.sequence) or None,
                actor=row.actor,
                actor_role=row.actor_role,
                action=row.action,
                subject_type=row.subject_type,
                subject_id=row.subject_id,
                occurred_at=_aware(row.occurred_at) or datetime.datetime.now(datetime.UTC),
                correlation_id=row.correlation_id,
                payload_hash=row.payload_hash,
                outcome=row.outcome,
            )
            for row in rows
        ]


# --- service functions --------------------------------------------------------------


def _context(tenant_id: str, correlation_id: str | None) -> engine.Context:
    scope = permissions.resolve_scope(tenant_id)
    return engine.Context.build(scope, correlation_id, datetime.datetime.now(datetime.UTC))


def _log(action: str, view: dict[str, Any]) -> None:
    # IDs and states only: never rationale, messages or taxpayer details.
    frappe.logger("vati_tax.decisions").info(
        {
            "action": action,
            "tenant_id": view.get("tenant_id"),
            "case_id": view.get("case_id"),
            "subject_id": view.get("decision_id") or view.get("request_id"),
            "status": view.get("status"),
            "jube_sync": (view.get("jube_sync") or {}).get("state"),
            "replayed": view.get("replayed"),
        }
    )


def _safe_uuid(value: object) -> str | None:
    return policy.canonical_uuid(value)


def _guard(
    ctx: engine.Context,
    attempted_action: str,
    subject_type: str,
    subject_id: object,
    fn: Callable[[], dict[str, Any]],
    *,
    case_id: object = None,
) -> dict[str, Any]:
    """Run ``fn``; a policy refusal is kept in the ledger (TAX-059) and re-raised."""
    audit_ctx = audit_service.Context(
        scope=ctx.scope, now=ctx.now, correlation_id=ctx.correlation_id
    )
    result: dict[str, Any] = audit_store.guarded(
        audit_ctx,
        attempted_action=attempted_action,
        subject_type=subject_type,
        subject_id=_safe_uuid(subject_id) or "invalid",
        case_id=_safe_uuid(case_id),
        fn=fn,
    )
    return result


def record_draft(
    *,
    tenant_id: str,
    case_id: str,
    body: dict[str, Any],
    idempotency_key: str,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    ctx = _context(tenant_id, correlation_id)
    view = _guard(
        ctx,
        policy.DRAFT_RECORD,
        "case",
        case_id,
        case_id=case_id,
        fn=lambda: engine.record_draft(
            ctx, FrappeRepository(), case_id=case_id, body=body, idempotency_key=idempotency_key
        ),
    )
    _log("record_draft", view)
    return view


def propose_decision(
    *,
    tenant_id: str,
    case_id: str,
    body: dict[str, Any],
    idempotency_key: str,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    ctx = _context(tenant_id, correlation_id)
    view = _guard(
        ctx,
        policy.DECISION_PROPOSE,
        "case",
        case_id,
        case_id=case_id,
        fn=lambda: engine.propose(
            ctx,
            FrappeRepository(),
            lifecycle_for(ctx.scope.tenant_id),
            case_id=case_id,
            body=body,
            idempotency_key=idempotency_key,
        ),
    )
    _log("propose_decision", view)
    return view


def review_decision(
    *,
    tenant_id: str,
    decision_id: str,
    action: str,
    body: dict[str, Any],
    idempotency_key: str,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    if action not in ("approve", "reject", "withdraw"):
        raise rules.InvalidMapping("Unknown review action.", "action", "unknown_action")
    ctx = _context(tenant_id, correlation_id)
    attempted = policy.DECISION_PROPOSE if action == "withdraw" else policy.DECISION_APPROVE
    view = _guard(
        ctx,
        attempted,
        "decision",
        decision_id,
        fn=lambda: engine.review(
            ctx,
            FrappeRepository(),
            lifecycle_for(ctx.scope.tenant_id),
            action=action,  # type: ignore[arg-type]
            decision_id=decision_id,
            body=body,
            idempotency_key=idempotency_key,
        ),
    )
    _log(f"{action}_decision", view)
    return view


def retry_decision(
    *, tenant_id: str, decision_id: str, correlation_id: str | None = None
) -> dict[str, Any]:
    ctx = _context(tenant_id, correlation_id)
    view = _guard(
        ctx,
        policy.DECISION_EXECUTE,
        "decision",
        decision_id,
        fn=lambda: engine.retry_execution(
            ctx, FrappeRepository(), lifecycle_for(ctx.scope.tenant_id), decision_id=decision_id
        ),
    )
    _log("retry_decision", view)
    return view


def authorise_evidence_request(
    *,
    tenant_id: str,
    case_id: str,
    body: dict[str, Any],
    idempotency_key: str,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    ctx = _context(tenant_id, correlation_id)
    view = _guard(
        ctx,
        policy.EVIDENCE_REQUEST_AUTHORISE,
        "case",
        case_id,
        case_id=case_id,
        fn=lambda: engine.authorise_evidence_request(
            ctx, FrappeRepository(), case_id=case_id, body=body, idempotency_key=idempotency_key
        ),
    )
    _log("authorise_evidence_request", view)
    return view


def case_review_state(*, tenant_id: str, case_id: str) -> dict[str, Any]:
    ctx = _context(tenant_id, None)
    state = _guard(
        ctx,
        policy.CASE_READ,
        "case",
        case_id,
        case_id=case_id,
        fn=lambda: engine.case_review_state(ctx, FrappeRepository(), case_id=case_id),
    )
    # TAX-059: who read the case is recorded (and committed: this is a GET).
    audit_store.record_read(
        audit_service.Context(scope=ctx.scope, now=ctx.now, correlation_id=ctx.correlation_id),
        action="case.read",
        subject_type="case",
        subject_id=state["case_id"],
        case_id=state["case_id"],
    )
    return state
