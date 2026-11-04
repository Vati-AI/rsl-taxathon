"""Frappe binding for the audit ledger (TAX-059).

``FrappeLedger`` implements ``ledger.LedgerStore`` on ``Vati Audit Event``.
``head`` reads the tenant's last entry ``for update``, so concurrent writers in
one tenant take positions one after another; the unique ``chain_key`` makes a
clash fail loudly instead of forking the chain.

Two kinds of entry must survive a request that otherwise writes nothing:

- reads, recorded from GET methods (Frappe commits only unsafe methods);
- refused attempts, recorded while the request is failing.

Both are committed explicitly. ``guarded`` first rolls back, to a savepoint,
anything the refused call wrote, so a refusal never commits partial work.
"""

from __future__ import annotations

import contextlib
import datetime
import uuid
from collections.abc import Iterator
from typing import Any

import frappe

from vati_tax.audit import ledger, service
from vati_tax.audit.ledger import LedgerEntry
from vati_tax.identity import permissions, policy

AUDIT = "Vati Audit Event"
_UNIQUE = (frappe.UniqueValidationError, frappe.DuplicateEntryError)
_FIELDS = [
    "name",
    "event_id",
    "tenant_id",
    "chain_seq",
    "prev_hash",
    "entry_hash",
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
]


def _entry(row: Any) -> LedgerEntry:
    from vati_tax.vati_tax.doctype.vati_audit_event.vati_audit_event import entry_from_doc

    return entry_from_doc(row)


class FrappeLedger:
    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        savepoint = "vati_ledger_" + uuid.uuid4().hex[:12]
        frappe.db.savepoint(savepoint)
        try:
            yield
        except BaseException:
            frappe.db.rollback(save_point=savepoint)
            raise

    def head(self, tenant_id: str) -> LedgerEntry | None:
        columns = ", ".join(f"`{name}`" for name in _FIELDS)
        rows = frappe.db.sql(
            f"select {columns} from `tab{AUDIT}` where `tenant_id` = %s "  # noqa: S608
            "order by `chain_seq` desc limit 1 for update",
            (tenant_id,),
            as_dict=True,
        )
        return _entry(rows[0]) if rows else None

    def insert(self, entry: LedgerEntry) -> None:
        doc = frappe.get_doc(
            {
                "doctype": AUDIT,
                "event_id": entry.event_id,
                "tenant_id": entry.tenant_id,
                "taxpayer_id": entry.taxpayer_id,
                "case_id": entry.case_id,
                "sequence": entry.sequence or 0,
                "action": entry.action,
                "outcome": entry.outcome,
                "category": entry.category,
                "actor": entry.actor,
                "actor_role": entry.actor_role,
                "target_actor": entry.target_actor,
                "occurred_at": entry.occurred_at.astimezone(datetime.UTC).replace(tzinfo=None),
                "correlation_id": entry.correlation_id,
                "subject_type": entry.subject_type,
                "subject_id": entry.subject_id,
                "payload_hash": entry.payload_hash,
                "reason": entry.reason,
                "chain_seq": entry.chain_seq,
                "prev_hash": entry.prev_hash,
                "entry_hash": entry.entry_hash,
            }
        )
        with self.atomic():
            try:
                doc.insert(ignore_permissions=True)
            except _UNIQUE as exc:
                raise ledger.Duplicate(str(exc)) from exc

    def entries(self, tenant_id: str) -> list[LedgerEntry]:
        rows = frappe.get_all(
            AUDIT, filters={"tenant_id": tenant_id}, fields=_FIELDS, order_by="chain_seq asc"
        )
        return [_entry(row) for row in rows]


# --- session helpers ----------------------------------------------------------------


def context(tenant_id: str, correlation_id: str | None = None) -> service.Context:
    scope = permissions.resolve_scope(tenant_id)
    return service.Context.build(scope, correlation_id, datetime.datetime.now(datetime.UTC))


def record_read(
    ctx: service.Context,
    *,
    action: str,
    subject_type: str,
    subject_id: str,
    case_id: str | None = None,
    taxpayer_id: str | None = None,
) -> None:
    """Record a successful read and commit it (reads arrive on GET)."""
    service.record_read(
        ctx,
        FrappeLedger(),
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        case_id=case_id,
        taxpayer_id=taxpayer_id,
    )
    frappe.db.commit()  # nosemgrep: GET requests are not committed by Frappe


def guarded(
    ctx: service.Context,
    *,
    attempted_action: str,
    subject_type: str,
    subject_id: str,
    fn: Any,
    case_id: str | None = None,
) -> Any:
    """Run ``fn``; keep a refused attempt in the ledger and raise ``PermissionError``.

    Anything ``fn`` wrote before it was refused is rolled back to a savepoint
    taken just before it ran; the refusal entry is then written and committed,
    because the failing request is otherwise rolled back or never committed.
    """
    savepoint = "vati_guard_" + uuid.uuid4().hex[:12]
    frappe.db.savepoint(savepoint)
    try:
        return fn()
    except policy.ActionDenied as exc:
        frappe.db.rollback(save_point=savepoint)
        service.record_denied(
            ctx,
            FrappeLedger(),
            attempted_action=attempted_action,
            subject_type=subject_type,
            subject_id=subject_id,
            code=exc.code,
            case_id=case_id,
        )
        frappe.db.commit()  # nosemgrep: the refusal must outlive the failing request
        raise frappe.PermissionError(str(exc)) from exc


def _supervisor_call(ctx: service.Context, fn: Any) -> dict[str, Any]:
    """Run a service read. A refusal is already recorded by ``service``; keep it."""
    try:
        result: dict[str, Any] = fn()
    except policy.ActionDenied as exc:
        frappe.db.commit()  # nosemgrep: keep the recorded refusal
        raise frappe.PermissionError(str(exc)) from exc
    frappe.db.commit()  # nosemgrep: keep the recorded read (GET)
    return result


# --- service functions --------------------------------------------------------------


def ledger_entries(
    *,
    tenant_id: str,
    case_id: str | None = None,
    actor: str | None = None,
    category: str | None = None,
    since_seq: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    ctx = context(tenant_id)
    return _supervisor_call(
        ctx,
        lambda: service.ledger_entries(
            ctx,
            FrappeLedger(),
            case_id=case_id,
            actor=actor,
            category=category,
            since_seq=since_seq,
            limit=limit,
        ),
    )


def verify_ledger(*, tenant_id: str) -> dict[str, Any]:
    ctx = context(tenant_id)
    return _supervisor_call(ctx, lambda: service.verify_ledger(ctx, FrappeLedger()))


def review_signals(*, tenant_id: str, window_days: int) -> dict[str, Any]:
    ctx = context(tenant_id)
    return _supervisor_call(
        ctx, lambda: service.review_signals(ctx, FrappeLedger(), window_days=window_days)
    )


def ledger_summary(
    *, tenant_id: str, since: datetime.datetime, until: datetime.datetime
) -> dict[str, Any]:
    ctx = context(tenant_id)
    return _supervisor_call(
        ctx, lambda: service.ledger_summary(ctx, FrappeLedger(), since=since, until=until)
    )


def record_override(
    *,
    tenant_id: str,
    body: dict[str, Any],
    idempotency_key: str,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    ctx = context(tenant_id, correlation_id)
    try:
        return service.record_override(
            ctx, FrappeLedger(), body=body, idempotency_key=idempotency_key
        )
    except policy.ActionDenied as exc:
        frappe.db.commit()  # nosemgrep: keep the recorded refusal
        raise frappe.PermissionError(str(exc)) from exc
