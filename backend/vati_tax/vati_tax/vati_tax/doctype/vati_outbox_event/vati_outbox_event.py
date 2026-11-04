"""Durable outbox event (TAX-033).

Created only through ``vati_tax.outbox.store.enqueue`` inside the caller's
transaction. The envelope is immutable; only delivery fields change. The unique
``dedupe_key`` and ``sequence_key`` columns enforce idempotent enqueue and
per-case ordering in the database, even for a direct insert.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import frappe
from frappe.model.document import Document

from vati_tax.ids.canonical import MappingError
from vati_tax.outbox import policy

ENVELOPE = (
    "tenant_id",
    "case_id",
    "event_type",
    "event_id",
    "correlation_id",
    "source_service",
    "idempotency_key",
    "payload_json",
    "dedupe_key",
    "ordering_key",
    "sequence",
    "sequence_key",
    "request_digest",
)


class VatiOutboxEvent(Document):
    if TYPE_CHECKING:
        tenant_id: str
        case_id: str
        event_type: str
        event_id: str
        correlation_id: str
        source_service: str
        occurred_at: Any
        idempotency_key: str
        payload_json: str
        status: str
        attempts: int
        max_attempts: int
        next_attempt_at: Any
        last_error_code: str | None
        delivered_at: Any
        dead_lettered_at: Any
        manual_retries: int
        last_manual_retry_by: str | None
        lease_token: str | None
        lease_until: Any
        locked_by: str | None
        ordering_key: str
        sequence: int
        sequence_key: str
        dedupe_key: str
        request_digest: str

    def envelope(self) -> policy.Envelope:
        return policy.Envelope.build(
            tenant_id=self.tenant_id,
            case_id=self.case_id,
            type=self.event_type,
            payload=json.loads(self.payload_json or "null"),
            idempotency_key=self.idempotency_key,
            correlation_id=self.correlation_id,
            source_service=self.source_service,
            event_id=self.event_id,
            occurred_at=frappe.utils.get_datetime(self.occurred_at),
        )

    def validate(self) -> None:
        try:
            env = self.envelope()
        except (MappingError, ValueError) as exc:
            frappe.throw(str(exc), title=getattr(exc, "code", "invalid_request"))
        case_tenant = frappe.db.get_value("Vati Case Identity", env.case_id, "tenant_id")
        if case_tenant != env.tenant_id:
            # Same message for a missing and a foreign case: no cross-tenant probing.
            frappe.throw("Case not found.", frappe.DoesNotExistError)
        if self.status not in policy.STATUSES:
            frappe.throw("Unknown outbox status.")
        if not isinstance(self.sequence, int) or self.sequence < 1:
            frappe.throw("sequence must be a positive integer.")
        self.tenant_id, self.case_id, self.event_id = env.tenant_id, env.case_id, env.event_id
        self.correlation_id = env.correlation_id
        self.payload_json = policy.canonical_json(env.payload)
        self.dedupe_key = env.dedupe_key
        self.ordering_key = env.ordering_key
        self.sequence_key = policy.sequence_key(env.ordering_key, self.sequence)
        self.request_digest = env.request_digest
        if not self.is_new():
            before = self.get_doc_before_save()
            if before is not None:
                for field in ENVELOPE:
                    if (before.get(field) or None) != (self.get(field) or None):
                        frappe.throw(f"{field} cannot change; enqueue a new event instead.")

    def sync_state(self) -> dict[str, object]:
        """Contract ``SyncState`` for this event's delivery."""
        attempted = frappe.db.get_value(
            "Vati Outbox Attempt",
            {"event": self.name},
            "started_at",
            order_by="attempt_no desc",
        )
        return {
            "state": policy.SYNC_STATE[self.status],
            "last_attempt_at": policy.iso(frappe.utils.get_datetime(attempted))
            if attempted
            else None,
            "error_code": self.last_error_code or None,
        }
