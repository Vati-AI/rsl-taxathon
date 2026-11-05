"""Frappe binding for correction reminders (TAX-078).

``FrappeReminders`` stores ``Vati Correction Reminder`` rows. Decisions (TAX-046)
and challenges (TAX-069) are read through their own stores and never written
here. Refusals are written to the audit ledger by ``service`` before it raises;
``_call`` commits them (and GET reads) before Frappe would roll the request
back, so on a refusal the only rows written are those ledger entries.

The taxpayer portal reads through ``taxpayer_reminders``, which takes the
caller's own (tenant, taxpayer) memberships and filters every query by both.
Taxpayers have no DocType permission on reminders.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import uuid
from collections.abc import Callable, Iterable, Iterator
from typing import Any, TypeVar

import frappe

from vati_tax.audit import ledger
from vati_tax.audit import store as audit_store
from vati_tax.audit.store import FrappeLedger
from vati_tax.challenges.store import FrappeChallenges
from vati_tax.decisions.store import FrappeRepository
from vati_tax.identity import policy
from vati_tax.ids.canonical import MappingError
from vati_tax.reminders import service
from vati_tax.reminders.rules import ReminderRecord

REMINDER = "Vati Correction Reminder"
_UNIQUE = (frappe.UniqueValidationError, frappe.DuplicateEntryError)
_FIELDS = [
    "reminder_id",
    "tenant_id",
    "taxpayer_id",
    "case_id",
    "decision_id",
    "topic",
    "status",
    "version",
    "explanation",
    "request_digest",
    "recorded_by",
    "recorded_at",
    "updated_by",
    "updated_at",
    "change_reason",
    "history",
]
T = TypeVar("T")


def _naive(value: datetime.datetime | None) -> datetime.datetime | None:
    return value.astimezone(datetime.UTC).replace(tzinfo=None) if value else None


def _aware(value: Any) -> datetime.datetime | None:
    if not value:
        return None
    return frappe.utils.get_datetime(value).replace(tzinfo=datetime.UTC)


def _record(row: Any) -> ReminderRecord:
    recorded_at = _aware(row.recorded_at)
    if recorded_at is None:
        raise ValueError("recorded_at is required")
    return ReminderRecord(
        reminder_id=row.reminder_id,
        tenant_id=row.tenant_id,
        taxpayer_id=row.taxpayer_id,
        case_id=row.case_id,
        decision_id=row.decision_id,
        topic=row.topic,
        explanation=row.explanation,
        request_digest=row.request_digest,
        recorded_by=row.recorded_by,
        recorded_at=recorded_at,
        status=row.status,
        version=int(row.version),
        updated_by=row.updated_by or None,
        updated_at=_aware(row.updated_at),
        change_reason=row.change_reason or None,
        history=tuple(json.loads(row.history or "[]")),
    )


class FrappeReminders:
    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        savepoint = "vati_reminder_" + uuid.uuid4().hex[:12]
        frappe.db.savepoint(savepoint)
        try:
            yield
        except BaseException:
            frappe.db.rollback(save_point=savepoint)
            raise

    def get(self, tenant_id: str, reminder_id: str) -> ReminderRecord | None:
        row = frappe.db.get_value(REMINDER, reminder_id, _FIELDS, as_dict=True)
        if not row or row.tenant_id != tenant_id:
            return None
        return _record(row)

    def for_taxpayer(self, tenant_id: str, taxpayer_id: str) -> list[ReminderRecord]:
        rows = frappe.get_all(
            REMINDER,
            filters={"tenant_id": tenant_id, "taxpayer_id": taxpayer_id},
            fields=_FIELDS,
            order_by="recorded_at desc, creation desc",
        )
        return [_record(row) for row in rows]

    def insert(self, record: ReminderRecord) -> None:
        doc = frappe.get_doc(
            {
                "doctype": REMINDER,
                "reminder_id": record.reminder_id,
                "tenant_id": record.tenant_id,
                "taxpayer_id": record.taxpayer_id,
                "case_id": record.case_id,
                "decision_id": record.decision_id,
                "topic": record.topic,
                "status": record.status,
                "version": record.version,
                "explanation": record.explanation,
                "request_digest": record.request_digest,
                "recorded_by": record.recorded_by,
                "recorded_at": _naive(record.recorded_at),
                "history": "[]",
            }
        )
        try:
            doc.insert(ignore_permissions=True)
        except _UNIQUE as exc:
            raise ledger.Duplicate(str(exc)) from exc

    def update(self, record: ReminderRecord, expected_version: int) -> bool:
        """Compare-and-set on ``version``: the row is locked, then checked."""
        rows = frappe.db.sql(
            f"select `version` from `tab{REMINDER}` where `name` = %s for update",  # noqa: S608
            (record.reminder_id,),
        )
        if not rows or int(rows[0][0]) != expected_version:
            return False
        doc = frappe.get_doc(REMINDER, record.reminder_id)
        doc.update(
            {
                "topic": record.topic,
                "status": record.status,
                "version": record.version,
                "explanation": record.explanation,
                "updated_by": record.updated_by,
                "updated_at": _naive(record.updated_at),
                "change_reason": record.change_reason,
                "history": json.dumps(list(record.history)),
            }
        )
        try:
            doc.save(ignore_permissions=True)
        except _UNIQUE as exc:
            raise ledger.Duplicate(str(exc)) from exc
        return True


def ports() -> service.Ports:
    return service.Ports(
        reminders=FrappeReminders(),
        decisions=FrappeRepository(),
        challenges=FrappeChallenges(),
        ledger=FrappeLedger(),
    )


def _call(fn: Callable[[], T]) -> T:
    """Run a service step; keep recorded refusals and reads, map policy refusals."""
    try:
        result = fn()
    except policy.ActionDenied as exc:
        frappe.db.commit()  # nosemgrep: keep the recorded refusal
        raise frappe.PermissionError(str(exc)) from exc
    except MappingError:
        frappe.db.commit()  # nosemgrep: keep the recorded refusal (only ledger rows)
        raise
    frappe.db.commit()  # nosemgrep: GET reads are not committed by Frappe
    return result


def record(
    *, tenant_id: str, body: dict[str, Any], idempotency_key: str, correlation_id: str | None
) -> dict[str, Any]:
    ctx = audit_store.context(tenant_id, correlation_id)
    return _call(lambda: service.record(ctx, ports(), body=body, idempotency_key=idempotency_key))


def correct(
    *,
    tenant_id: str,
    reminder_id: str,
    expected_version: object,
    action: str,
    reason: str,
    topic: str | None,
    explanation: str | None,
    correlation_id: str | None,
) -> dict[str, Any]:
    ctx = audit_store.context(tenant_id, correlation_id)
    return _call(
        lambda: service.correct(
            ctx,
            ports(),
            reminder_id=reminder_id,
            expected_version=expected_version,
            action=action,
            reason=reason,
            topic=topic,
            explanation=explanation,
        )
    )


def staff_list(*, tenant_id: str, taxpayer_id: str) -> dict[str, Any]:
    ctx = audit_store.context(tenant_id)
    return _call(lambda: service.staff_list(ctx, ports(), taxpayer_id=taxpayer_id))


def taxpayer_reminders(scope: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
    """The portal view for the caller's own memberships (no tenant header needed)."""
    return service.taxpayer_list(ports(), scope)
