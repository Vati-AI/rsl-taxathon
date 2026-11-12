"""Whitelisted staff methods for correction reminders (TAX-078).

``X-Vati-Tenant-ID`` selects the tenant (TAX-029). ``record_reminder`` takes an
``Idempotency-Key`` (argument or header); ``X-Vati-Correlation-ID`` is optional.
Every step, read and refusal is kept in the tenant audit ledger (TAX-059).

- ``record_reminder`` (POST): an auditor or supervisor records a reminder from
  an approved decision whose outcome recorded an omission
  (``close_after_correction`` or ``request_further_evidence``).
- ``correct_reminder`` (POST): an auditor or supervisor corrects the topic or
  explanation (``action: correct``) or withdraws it (``action: withdraw``),
  with a reason and the current ``expected_version``.
- ``get_reminders`` (GET): every reminder of one taxpayer, with its history and
  whether the taxpayer can currently see it.

Taxpayers read their own reminders through ``vati_tax.portal.api``.
"""

from __future__ import annotations

from typing import Any, NoReturn

import frappe

from vati_tax.identity import permissions
from vati_tax.ids.canonical import MappingError
from vati_tax.refund_access import store as refund_access
from vati_tax.reminders import store


class ReminderBadRequest(frappe.ValidationError):
    http_status_code = 400


class ReminderConflict(frappe.ValidationError):
    http_status_code = 422


def _raise(exc: MappingError) -> NoReturn:
    if exc.http_status == 404:
        frappe.throw(str(exc), frappe.DoesNotExistError, title=exc.code)
    cls = ReminderBadRequest if exc.http_status == 400 else ReminderConflict
    reason = exc.field_errors[0]["code"] if exc.field_errors else exc.code
    frappe.throw(str(exc), cls, title=f"{exc.code}:{reason}")
    raise AssertionError("unreachable")


def _tenant() -> str:
    return permissions.request_tenant_header() or ""


def _header(name: str) -> str | None:
    request = getattr(frappe.local, "request", None)
    return request.headers.get(name) if request is not None else None


@frappe.whitelist(methods=["POST"])
def record_reminder(
    decision_id: str, topic: str, explanation: str, idempotency_key: str | None = None
) -> dict[str, Any]:
    refund_access.require_record("Vati Decision", decision_id, "reminder.record")  # TAX-142
    try:
        return store.record(
            tenant_id=_tenant(),
            body={"decision_id": decision_id, "topic": topic, "explanation": explanation},
            idempotency_key=idempotency_key or _header("Idempotency-Key") or "",
            correlation_id=_header("X-Vati-Correlation-ID"),
        )
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["POST"])
def correct_reminder(
    reminder_id: str,
    expected_version: int | str,
    action: str,
    reason: str,
    topic: str | None = None,
    explanation: str | None = None,
) -> dict[str, Any]:
    refund_access.require_record(
        "Vati Correction Reminder", reminder_id, "reminder.correct"
    )  # TAX-142
    try:
        return store.correct(
            tenant_id=_tenant(),
            reminder_id=reminder_id,
            expected_version=frappe.utils.cint(expected_version)
            if expected_version not in (None, "")
            else None,
            action=action,
            reason=reason,
            topic=topic or None,
            explanation=explanation or None,
            correlation_id=_header("X-Vati-Correlation-ID"),
        )
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["GET"])
def get_reminders(taxpayer_id: str) -> dict[str, Any]:
    try:
        return store.staff_list(tenant_id=_tenant(), taxpayer_id=taxpayer_id)
    except MappingError as exc:
        _raise(exc)
