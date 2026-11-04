"""Evidence request responses: taxpayer-facing rules (TAX-045).

Pure functions with no Frappe import, so they are testable without a bench.
``vati_tax.responses.store`` applies them to the TAX-046 ``Vati Evidence
Request`` records and the ``Vati Evidence Response`` DocType.

What a taxpayer sees is an explicit allowlist (``taxpayer_view``): the request
ID, a taxpayer-facing status, the due date, the officer's message to the
taxpayer and the requested items. Case IDs, finding IDs, who authorised the
request, case versions and keys are internal and never leave the server.

A response answers every requested item exactly once, with at least one of the
taxpayer's own documents or a short reason the document cannot be provided.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from vati_tax.ids.canonical import InvalidMapping, canonical_uuid, validate_idempotency_key

# Internal request status -> the only statuses a taxpayer is shown. A status not
# listed here (for example a future internal one) is never shown at all.
TAXPAYER_STATUS: Final[dict[str, str]] = {
    "recorded": "awaiting_response",
    "sent": "awaiting_response",
    "responded": "response_sent",
    "withdrawn": "no_longer_needed",
}
OPEN_STATUSES: Final = frozenset({"recorded", "sent"})
MAX_REASON: Final = 1000
MAX_MESSAGE: Final = 2000
MAX_DOCUMENTS: Final = 50


@dataclass(frozen=True)
class FieldError:
    field: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code, "message": self.message}


class ResponseInputError(ValueError):
    """One or more answers are invalid (API ``invalid_domain_input``, HTTP 422)."""

    def __init__(self, errors: list[FieldError]):
        super().__init__("Check the highlighted answers.")
        self.errors = errors


@dataclass(frozen=True)
class RequestedItem:
    index: int
    document_type: str
    description: str


@dataclass(frozen=True)
class ItemAnswer:
    item_index: int
    evidence_ids: tuple[str, ...]
    unavailable_reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_index": self.item_index,
            "evidence_ids": list(self.evidence_ids),
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True)
class ResponseInput:
    idempotency_key: str
    items: tuple[ItemAnswer, ...]
    message: str | None

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(e for item in self.items for e in item.evidence_ids)

    def digest(self, request_id: str) -> str:
        payload = {
            "request_id": request_id,
            "items": [item.as_dict() for item in self.items],
            "message": self.message,
        }
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def requested_items(raw: object) -> list[RequestedItem]:
    """The items stored on a request (JSON text); malformed entries are skipped."""
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    items = []
    for index, entry in enumerate(value):
        if isinstance(entry, Mapping):
            doc_type, description = entry.get("document_type"), entry.get("description")
            if isinstance(doc_type, str) and isinstance(description, str):
                items.append(RequestedItem(index, doc_type, description))
    return items


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def build_response(raw: Mapping[str, Any], requested: Sequence[RequestedItem]) -> ResponseInput:
    """Validate a response against the exact items requested; report every problem."""
    errors: list[FieldError] = []
    try:
        key = validate_idempotency_key(raw.get("idempotency_key"))
    except InvalidMapping:  # the message below is taxpayer-facing
        key = ""
        errors.append(
            FieldError(
                "idempotency_key",
                "invalid_idempotency_key",
                "This response could not be sent safely. Reload the page and try again.",
            )
        )

    message = _text(raw.get("message"))
    if message is not None and len(message) > MAX_MESSAGE:
        errors.append(
            FieldError("message", "too_long", f"Keep the message under {MAX_MESSAGE} characters.")
        )

    answers_raw = raw.get("items")
    if isinstance(answers_raw, str):
        try:
            answers_raw = json.loads(answers_raw)
        except ValueError:
            answers_raw = None
    if not isinstance(answers_raw, list):
        errors.append(FieldError("items", "invalid_type", "Answer each requested item."))
        raise ResponseInputError(errors)

    wanted = {item.index for item in requested}
    answers: dict[int, ItemAnswer] = {}
    seen_evidence: set[str] = set()
    for entry in answers_raw:
        if not isinstance(entry, Mapping):
            errors.append(FieldError("items", "invalid_item", "Answer each requested item."))
            continue
        index = entry.get("item_index")
        if isinstance(index, bool) or not isinstance(index, int) or index not in wanted:
            # An answer to an item this request did not ask for.
            errors.append(
                FieldError("items", "unknown_item", "An answer does not match this request.")
            )
            continue
        field = f"items.{index}"
        if index in answers:
            errors.append(FieldError(field, "duplicate_item", "Answer each item only once."))
            continue
        ids_raw = entry.get("evidence_ids") or []
        ids: list[str] = []
        if not isinstance(ids_raw, list):
            errors.append(FieldError(field, "invalid_uuid", "A document reference is not valid."))
            continue
        bad = False
        for value in ids_raw:
            try:
                evidence_id = canonical_uuid(value, "evidence_ids")
            except InvalidMapping:
                bad = True
                continue
            if evidence_id in seen_evidence:
                errors.append(
                    FieldError(
                        field,
                        "evidence_reused",
                        "Attach each document to one requested item only.",
                    )
                )
                bad = True
                continue
            seen_evidence.add(evidence_id)
            ids.append(evidence_id)
        if bad and not any(e.field == field for e in errors):
            errors.append(
                FieldError(
                    field,
                    "invalid_uuid",
                    "A document reference is not valid. Remove it and upload the file again.",
                )
            )
        reason = _text(entry.get("unavailable_reason"))
        if reason is not None and len(reason) > MAX_REASON:
            errors.append(
                FieldError(field, "too_long", f"Keep the reason under {MAX_REASON} characters.")
            )
        if not ids and reason is None:
            errors.append(
                FieldError(
                    field,
                    "answer_required",
                    "Upload a document for this item, or explain why you cannot provide it.",
                )
            )
        answers[index] = ItemAnswer(index, tuple(ids), reason)

    for index in sorted(wanted - set(answers)):
        if not any(e.field == f"items.{index}" for e in errors):
            errors.append(
                FieldError(
                    f"items.{index}",
                    "answer_required",
                    "Upload a document for this item, or explain why you cannot provide it.",
                )
            )
    if len(seen_evidence) > MAX_DOCUMENTS:
        errors.append(FieldError("items", "too_many", f"Attach at most {MAX_DOCUMENTS} documents."))
    if errors:
        raise ResponseInputError(errors)
    return ResponseInput(
        idempotency_key=key,
        items=tuple(answers[i] for i in sorted(answers)),
        message=message,
    )


def stored_answers(raw: object) -> list[dict[str, Any]]:
    """Answers stored on a response (JSON text), normalised for display."""
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    out = []
    for entry in value:
        if isinstance(entry, Mapping) and isinstance(entry.get("item_index"), int):
            ids = entry.get("evidence_ids")
            out.append(
                {
                    "item_index": entry["item_index"],
                    "evidence_ids": [e for e in ids if isinstance(e, str)]
                    if isinstance(ids, list)
                    else [],
                    "unavailable_reason": entry.get("unavailable_reason")
                    if isinstance(entry.get("unavailable_reason"), str)
                    else None,
                }
            )
    return out


def _iso(value: object) -> str | None:
    """ISO text for a date or datetime (Frappe returns either, or a string)."""
    if value is None or value == "":
        return None
    iso = getattr(value, "isoformat", None)
    return str(iso()) if callable(iso) else str(value)


def taxpayer_view(
    request: Mapping[str, Any],
    response: Mapping[str, Any] | None = None,
    document_names: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """The taxpayer-facing projection of a request, or None if it must not be shown.

    Built field by field from an allowlist; nothing else from the request row
    can reach the portal.
    """
    status = TAXPAYER_STATUS.get(str(request.get("status")))
    if status is None:
        return None
    due = request.get("due_date")
    view: dict[str, Any] = {
        "request_id": request.get("name") or request.get("request_id"),
        "status": status,
        "due_date": _iso(due),
        "message_to_taxpayer": request.get("message_to_taxpayer") or "",
        "items": [
            {"index": i.index, "document_type": i.document_type, "description": i.description}
            for i in requested_items(request.get("items"))
        ],
        "response": None,
    }
    if response is not None and status == "response_sent":
        names = document_names or {}
        answered = response.get("responded_at")
        view["response"] = {
            "responded_at": _iso(answered),
            "message": response.get("message") or None,
            "items": [
                {
                    "index": a["item_index"],
                    "documents": [
                        {"evidence_id": e, "name": names.get(e, "Document")}
                        for e in a["evidence_ids"]
                    ],
                    "unavailable_reason": a["unavailable_reason"],
                }
                for a in stored_answers(response.get("items"))
            ],
        }
    return view
