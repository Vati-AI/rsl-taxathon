"""Persist canonical case IDs and service links in Frappe (TAX-030).

Internal server-side API for adapters, the outbox (TAX-033) and later Vati routes.
Nothing here is whitelisted: callers must already have resolved the acting
principal's permitted tenant (TAX-029) and pass that tenant explicitly. Every
read and write is scoped to that tenant, so a case UUID alone never reaches
another tenant's data. Inserts use ``ignore_permissions`` because the DocTypes are
read-only in desk; that bypass is limited to this module and covered by negative
tests in ``test_mapping_bench.py``.

Rules come from ``vati_tax.ids.canonical``; the DocTypes' unique columns enforce
the same keys, and a lost insert race is re-decided once against the winner.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.model.naming import make_autoname

from vati_tax.ids import canonical
from vati_tax.ids.canonical import (
    CaseNotFound,
    CaseRegistration,
    InvalidMapping,
    Link,
    StoredCase,
    TenantBoundaryError,
)

CASE = "Vati Case Identity"
LINK = "Vati Service Link"
DISPLAY_SERIES = "CASE-.######"
_CASE_FIELDS = [
    "tenant_id",
    "case_id",
    "display_id",
    "taxpayer_id",
    "submission_id",
    "registration_key",
]
_LINK_FIELDS = [
    "name",
    "tenant_id",
    "case_id",
    "service",
    "external_type",
    "external_id",
    "source_event_id",
]
_RACE_ERRORS = (frappe.UniqueValidationError, frappe.DuplicateEntryError)


def _log(action: str, outcome: str, **ids: str | None) -> None:
    frappe.logger("vati_tax.ids").info({"action": action, "outcome": outcome, **ids})


def _stored_case(field: str, value: str | None) -> StoredCase | None:
    if not value:
        return None
    row = frappe.db.get_value(CASE, {field: value}, _CASE_FIELDS, as_dict=True)
    if not row:
        return None
    return StoredCase(
        row.tenant_id,
        row.case_id,
        row.display_id,
        row.taxpayer_id or None,
        row.submission_id or None,
        row.registration_key or None,
    )


def _case_result(case: StoredCase, replayed: bool) -> dict[str, Any]:
    return {
        "tenant_id": case.tenant_id,
        "case_id": case.case_id,
        "display_id": case.display_id,
        "taxpayer_id": case.taxpayer_id,
        "submission_id": case.submission_id,
        "replayed": replayed,
    }


def register_case(
    *,
    tenant_id: str,
    idempotency_key: str,
    case_id: str | None = None,
    display_id: str | None = None,
    taxpayer_id: str | None = None,
    submission_id: str | None = None,
) -> dict[str, Any]:
    """Create the canonical case identity, or replay an earlier identical registration.

    ``case_id`` and ``display_id`` are generated when omitted. The same
    ``idempotency_key`` with the same request returns the original case
    (``replayed: True``); with a different request it raises ``IdempotencyConflict``.
    """
    request = CaseRegistration.build(
        tenant_id, idempotency_key, case_id, display_id, taxpayer_id, submission_id
    )
    reg_key = canonical.registration_key(request.tenant_id, request.idempotency_key)
    for attempt in (1, 2):
        try:
            action, existing = canonical.decide_case(
                request,
                _stored_case("registration_key", reg_key),
                _stored_case("case_id", request.case_id),
                _stored_case(
                    "display_key",
                    canonical.display_key(request.tenant_id, request.display_id)
                    if request.display_id
                    else None,
                ),
            )
        except canonical.MappingError as exc:
            _log("register_case", exc.code, tenant_id=request.tenant_id)
            raise
        if action == "replay" and existing is not None:
            _log("register_case", "replayed", tenant_id=request.tenant_id, case_id=existing.case_id)
            return _case_result(existing, replayed=True)
        doc = frappe.get_doc(
            {
                "doctype": CASE,
                "case_id": request.case_id or canonical.new_case_id(),
                "tenant_id": request.tenant_id,
                "display_id": request.display_id or make_autoname(DISPLAY_SERIES),
                "taxpayer_id": request.taxpayer_id,
                "submission_id": request.submission_id,
                "registration_key": reg_key,
                "synthetic": 1,
            }
        )
        frappe.db.savepoint("vati_register_case")
        try:
            doc.insert(ignore_permissions=True)
        except _RACE_ERRORS:
            # A concurrent registration won the unique key: decide again against it.
            frappe.db.rollback(save_point="vati_register_case")
            if attempt == 2:
                raise
            continue
        created = StoredCase(
            doc.tenant_id,
            doc.case_id,
            doc.display_id,
            doc.taxpayer_id or None,
            doc.submission_id or None,
            reg_key,
        )
        _log("register_case", "created", tenant_id=doc.tenant_id, case_id=doc.case_id)
        return _case_result(created, replayed=False)
    raise AssertionError("unreachable")


def _stored_link(field: str, value: str | None) -> Link | None:
    if not value:
        return None
    row = frappe.db.get_value(LINK, {field: value}, _LINK_FIELDS, as_dict=True)
    if not row:
        return None
    return Link(
        row.tenant_id,
        row.case_id,
        row.service,
        row.external_type,
        row.external_id,
        row.source_event_id or None,
    )


def _case_tenant(case_id: str) -> str | None:
    return frappe.db.get_value(CASE, case_id, "tenant_id")


def record_link(
    *,
    tenant_id: str,
    case_id: str,
    service: str,
    external_type: str,
    external_id: str,
    source_event_id: str | None = None,
) -> dict[str, Any]:
    """Map ``case_id`` to an upstream record, or replay an identical earlier mapping.

    Raises ``CaseNotFound``/``TenantBoundaryError`` (both ``not_found``) when the
    case is missing or belongs to another tenant, ``MappingConflict`` when the
    external record is mapped elsewhere or a one-per-case slot is taken, and
    ``IdempotencyConflict`` when ``source_event_id`` was used for another mapping.
    """
    request = Link.build(tenant_id, case_id, service, external_type, external_id, source_event_id)
    for attempt in (1, 2):
        try:
            action = canonical.decide_link(
                request,
                _case_tenant(request.case_id),
                _stored_link("link_key", request.link_key),
                _stored_link("slot_key", request.slot_key),
                _stored_link("event_key", request.event_key),
            )
        except canonical.MappingError as exc:
            _log(
                "record_link",
                exc.code,
                tenant_id=request.tenant_id,
                service=request.service,
                reason=exc.field_errors[0]["code"] if exc.field_errors else None,
            )
            raise
        if action == "replay":
            name = frappe.db.get_value(LINK, {"link_key": request.link_key}, "name")
            _log("record_link", "replayed", tenant_id=request.tenant_id, case_id=request.case_id)
            return _link_result(frappe.get_doc(LINK, name), replayed=True)
        doc = frappe.get_doc(
            {
                "doctype": LINK,
                "tenant_id": request.tenant_id,
                "case_id": request.case_id,
                "service": request.service,
                "external_type": request.external_type,
                "external_id": request.external_id,
                "source_event_id": request.source_event_id,
                "sync_state": "synced",
            }
        )
        frappe.db.savepoint("vati_record_link")
        try:
            doc.insert(ignore_permissions=True)
        except _RACE_ERRORS:
            frappe.db.rollback(save_point="vati_record_link")
            if attempt == 2:
                raise
            continue
        _log("record_link", "created", tenant_id=request.tenant_id, case_id=request.case_id)
        return _link_result(doc, replayed=False)
    raise AssertionError("unreachable")


def _link_result(doc: Any, replayed: bool) -> dict[str, Any]:
    return {
        "tenant_id": doc.tenant_id,
        "case_id": doc.case_id,
        "service_link": doc.as_service_link(),
        "replayed": replayed,
    }


def apply_link_event(event: dict[str, Any]) -> dict[str, Any]:
    """Record the link reported by an ``EventEnvelope`` (openapi.json); replays are no-ops.

    The payload carries ``service``, ``external_type`` and ``external_id``. An
    upstream may only report its own IDs (``source_service`` equals the payload
    service), unless Vati itself emitted the event.
    """
    if not isinstance(event, dict) or event.get("schema_version") != "1":
        raise InvalidMapping("Unsupported event schema_version.", "schema_version")
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise InvalidMapping("Event payload must be an object.", "payload")
    source = event.get("source_service")
    if source != "vati" and source != payload.get("service"):
        raise InvalidMapping(
            "A service may only report its own external IDs.", "source_service", "wrong_source"
        )
    return record_link(
        tenant_id=_text(event, "tenant_id"),
        case_id=_text(event, "case_id"),
        service=_text(payload, "service"),
        external_type=_text(payload, "external_type"),
        external_id=_text(payload, "external_id"),
        source_event_id=_text(event, "event_id"),
    )


def _text(source: dict[str, Any], field: str) -> str:
    value = source.get(field)
    if not isinstance(value, str) or not value:
        raise InvalidMapping(f"{field} is required.", field)
    return value


def resolve_case(
    *, tenant_id: str, service: str, external_type: str, external_id: str
) -> str | None:
    """Return the case mapped to an upstream record in this tenant, or ``None``."""
    tenant = canonical.canonical_uuid(tenant_id, "tenant_id")
    canonical.cardinality(service, external_type)
    key = canonical.link_key(
        tenant, service, external_type, canonical.validate_external_id(external_id)
    )
    row = frappe.db.get_value(LINK, {"link_key": key}, ["case_id", "external_type"], as_dict=True)
    if not row or row.external_type != external_type:
        return None
    return row.case_id


def case_service_links(*, tenant_id: str, case_id: str) -> list[dict[str, Any]]:
    """Contract-shaped ``service_links`` for a case the tenant owns."""
    tenant = canonical.canonical_uuid(tenant_id, "tenant_id")
    case = canonical.canonical_uuid(case_id, "case_id")
    owner = _case_tenant(case)
    if owner is None:
        raise CaseNotFound("Case not found.", "case_id", "not_found")
    if owner != tenant:
        raise TenantBoundaryError("Case not found.", "case_id", "not_found")
    names = frappe.get_all(
        LINK,
        filters={"tenant_id": tenant, "case_id": case},
        order_by="creation asc",
        pluck="name",
    )
    return [frappe.get_doc(LINK, name).as_service_link() for name in names]
