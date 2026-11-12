"""Evidence request responses: Frappe persistence and scope checks (TAX-045).

The taxpayer portal reads its own taxpayer's TAX-046 evidence requests through
this module, never through the ``Vati Evidence Request`` DocType (taxpayers have
no DocType permission on it). Every query is filtered by the caller's own
(tenant, taxpayer) memberships and selects only the columns the taxpayer view
needs, so internal fields are never loaded. A request outside the caller's
scope is reported exactly like a missing one.

Responding stores one immutable ``Vati Evidence Response``, moves the request
``recorded`` -> ``sent`` -> ``responded`` (the transitions ADR-013 assigns to
TAX-045) and appends a ``write`` entry to the TAX-059 audit ledger, all in one
transaction. Idempotent per taxpayer: the same key and answers replay the stored
result; a different request under the same key is ``idempotency_conflict``.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from collections.abc import Iterable
from typing import Any

import frappe

from vati_tax.audit import ledger
from vati_tax.audit import store as audit_store
from vati_tax.identity import permissions, policy
from vati_tax.ids.canonical import (
    CaseNotFound,
    IdempotencyConflict,
    InvalidMapping,
    MappingConflict,
    canonical_uuid,
)
from vati_tax.responses import rules
from vati_tax.vati_tax.doctype.vati_refund_claim.vati_refund_claim import foreign_evidence_ids

REQUEST = "Vati Evidence Request"
RESPONSE = "Vati Evidence Response"
EVIDENCE = "Vati Evidence"
# Only what the taxpayer view needs; internal columns are never selected.
_REQUEST_FIELDS = ["name", "tenant_id", "taxpayer_id", "status", "due_date", "items",
                   "message_to_taxpayer", "created_at", "line_items", "already_held"]  # fmt: skip
_RESPONSE_FIELDS = ["name", "request_id", "items", "message", "responded_at", "request_digest"]
_UNIQUE = (frappe.UniqueValidationError, frappe.DuplicateEntryError)

Scope = list[tuple[str, str]]


class RequestNotFound(CaseNotFound):
    """Missing, or outside the caller's scope (reported the same way)."""


class RequestClosed(MappingConflict):
    """The request no longer accepts a response (withdrawn or already answered)."""


def _scoped_key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _rows(scope: Scope, extra: dict[str, Any] | None = None) -> list[Any]:
    rows: list[Any] = []
    for tenant_id, taxpayer_id in scope:
        rows.extend(
            frappe.get_all(
                REQUEST,
                filters={"tenant_id": tenant_id, "taxpayer_id": taxpayer_id, **(extra or {})},
                fields=_REQUEST_FIELDS,
            )
        )
    return rows


def _owned_request(scope: Scope, request_id: str) -> Any:
    try:
        request_id = canonical_uuid(request_id, "request_id")
    except InvalidMapping:
        raise RequestNotFound("Evidence request not found.", "request_id", "not_found") from None
    rows = _rows(scope, {"name": request_id})
    if not rows:
        raise RequestNotFound("Evidence request not found.", "request_id", "not_found")
    return rows[0]


def _responses(request_ids: Iterable[str]) -> dict[str, Any]:
    ids = list(request_ids)
    if not ids:
        return {}
    rows = frappe.get_all(RESPONSE, filters={"request_id": ["in", ids]}, fields=_RESPONSE_FIELDS)
    return {row.request_id: row for row in rows}


def _document_names(
    tenant_id: str, taxpayer_id: str, evidence_ids: Iterable[str]
) -> dict[str, str]:
    ids = list(evidence_ids)
    if not ids:
        return {}
    rows = frappe.get_all(
        EVIDENCE,
        filters={"name": ["in", ids], "tenant_id": tenant_id, "taxpayer_id": taxpayer_id},
        fields=["name", "original_filename"],
    )
    return {row.name: row.original_filename or "Document" for row in rows}


def _view(row: Any, response: Any | None) -> dict[str, Any] | None:
    names: dict[str, str] = {}
    if response is not None:
        ids = [e for a in rules.stored_answers(response["items"]) for e in a["evidence_ids"]]
        names = _document_names(row.tenant_id, row.taxpayer_id, ids)
    return rules.taxpayer_view(row, response, names)


def list_requests(scope: Scope) -> list[dict[str, Any]]:
    """The caller's own evidence requests, open ones first, then newest first."""
    rows = _rows(scope)
    responses = _responses(row.name for row in rows)
    views = [v for row in rows if (v := _view(row, responses.get(row.name))) is not None]
    order = {"awaiting_response": 0, "response_sent": 1, "no_longer_needed": 2}
    views.sort(key=lambda v: (order.get(v["status"], 9), v["due_date"] or ""))
    return views


def get_request(scope: Scope, request_id: str) -> dict[str, Any]:
    row = _owned_request(scope, request_id)
    view = _view(row, _responses([row.name]).get(row.name))
    if view is None:
        raise RequestNotFound("Evidence request not found.", "request_id", "not_found")
    return view


def _reused_elsewhere(tenant_id: str, taxpayer_id: str, request_id: str, ids: set[str]) -> bool:
    """Whether any evidence is already attached to a response to another request."""
    for row in frappe.get_all(
        RESPONSE,
        filters={
            "tenant_id": tenant_id,
            "taxpayer_id": taxpayer_id,
            "request_id": ["!=", request_id],
        },
        fields=["items"],
    ):
        if ids & {e for a in rules.stored_answers(row["items"]) for e in a["evidence_ids"]}:
            return True
    return False


def _replay(registration_key: str, digest: str) -> Any:
    stored = frappe.db.get_value(
        RESPONSE,
        {"registration_key": registration_key},
        ["request_id", "request_digest"],
        as_dict=True,
    )
    if not stored:
        return None
    if stored.request_digest != digest:
        raise IdempotencyConflict(
            "This idempotency key was used for a different response.",
            "idempotency_key",
            "idempotency_conflict",
        )
    return stored


def respond(
    scope: Scope, request_id: str, body: dict[str, Any], correlation_id: str | None = None
) -> dict[str, Any]:
    """Store the taxpayer's response to one of its own open evidence requests."""
    row = _owned_request(scope, request_id)
    # The taxpayer may respond only for its own taxpayer (TAX-029 action matrix).
    permissions.require_action(policy.EVIDENCE_RESPOND, row.tenant_id, taxpayer_id=row.taxpayer_id)
    answer = rules.build_response(body, rules.requested_items(row["items"], row["line_items"]))
    digest = answer.digest(row.name)
    registration_key = _scoped_key(row.tenant_id, row.taxpayer_id, answer.idempotency_key)

    stored = _replay(registration_key, digest)
    if stored is not None:
        if stored.request_id != row.name:
            raise IdempotencyConflict(
                "This idempotency key was used for a different response.",
                "idempotency_key",
                "idempotency_conflict",
            )
        return {**get_request(scope, row.name), "replayed": True}
    if row.status not in rules.OPEN_STATUSES:
        reason = "already_responded" if row.status == "responded" else "request_withdrawn"
        raise RequestClosed("This request no longer accepts a response.", "request_id", reason)

    ids = set(answer.evidence_ids)
    if foreign_evidence_ids(row.tenant_id, row.taxpayer_id, ids):
        # 422 like any invalid answer; missing and foreign evidence look the same.
        raise MappingConflict("A document could not be found.", "evidence_ids", "not_found")
    if _reused_elsewhere(row.tenant_id, row.taxpayer_id, row.name, ids):
        raise MappingConflict(
            "A document is already attached to another request.", "evidence_ids", "evidence_reused"
        )

    now = datetime.datetime.now(datetime.UTC)
    try:
        frappe.get_doc(
            {
                "doctype": RESPONSE,
                "response_id": str(uuid.uuid4()),
                "request_id": row.name,
                "tenant_id": row.tenant_id,
                "taxpayer_id": row.taxpayer_id,
                "items": json.dumps([a.as_dict() for a in answer.items]),
                "message": answer.message,
                "responded_at": now.replace(tzinfo=None),
                "responded_by": frappe.session.user,
                "registration_key": registration_key,
                "request_digest": digest,
            }
        ).insert(ignore_permissions=True)
    except _UNIQUE:
        # A concurrent send won: answer as a retry, or as a closed request.
        frappe.db.rollback()
        stored = _replay(registration_key, digest)
        if stored is not None and stored.request_id == row.name:
            return {**get_request(scope, row.name), "replayed": True}
        raise RequestClosed(
            "This request no longer accepts a response.", "request_id", "already_responded"
        ) from None

    request = frappe.get_doc(REQUEST, row.name)
    if request.status == "recorded":
        request.status = "sent"
        request.save(ignore_permissions=True)
    request.status = "responded"
    request.save(ignore_permissions=True)

    ctx = audit_store.context(row.tenant_id, correlation_id)
    case_id = frappe.db.get_value(REQUEST, row.name, "case_id")
    ledger.append(
        audit_store.FrappeLedger(),
        ledger.Unsealed(
            event_id=ledger.new_event_id(),
            tenant_id=row.tenant_id,
            action="evidence_request.responded",
            outcome="responded",
            actor=ctx.scope.user,
            actor_role=policy.TAXPAYER,
            occurred_at=ctx.now,
            correlation_id=ctx.correlation_id,
            subject_type="evidence_request",
            subject_id=row.name,
            payload_hash=digest,
            case_id=case_id,
            taxpayer_id=row.taxpayer_id,
            category="write",
        ),
    )
    return {**get_request(scope, row.name), "replayed": False}
