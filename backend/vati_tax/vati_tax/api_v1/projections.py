"""Frappe-free projections from Vati records to /api/v1 contract objects (TAX-090).

Each function takes plain values read from Frappe (or from the existing domain
views) and returns one contract object with exactly the contract's fields
(``additionalProperties: false``). Nothing here invents a value: where Frappe holds
no source for a field, the contract's documented empty value is used (null or an
empty list) and ADR-046 says why.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from vati_tax.api_v1.core import officer_id

CASE_STATUSES = (
    "RECEIVED",
    "VALIDATING",
    "INVESTIGATING",
    "AWAITING_EVIDENCE",
    "AWAITING_HUMAN",
    "RESOLVED",
    "FAILED_PROCESSING",
)
OPEN_STATUSES = frozenset(CASE_STATUSES) - {"RESOLVED"}
# Vati Lane Proposal lanes (ADR-042) -> contract RefundLane (TAX-048 three lanes).
LANES = {"fast": "fast", "standard": "standard", "investigation": "targeted"}
OFFICER_ROLES = ("auditor", "specialist", "supervisor")
CONTRACT_SERVICES = frozenset({"jube", "frappe", "openaleph", "yente", "docling", "langgraph"})
ENTITY_SCHEMAS = frozenset(
    {
        "Person",
        "Company",
        "Organization",
        "LegalEntity",
        "Ownership",
        "Directorship",
        "Employment",
        "Payment",
    }
)
EVIDENCE_SOURCES = frozenset({"taxpayer_portal", "officer_upload", "synthetic_fixture"})
# Entity IDs: FtM IDs are SHA-256 hex (integration/followthemoney); the contract wants
# a UUID. One UUID per tenant and FtM ID; the FtM ID stays in the OpenAleph link.
ENTITY_NAMESPACE = uuid.UUID("5a7e0090-0000-4000-8000-0000000000e1")
_MAX_PROPERTY_VALUES = 50
_MAX_VALUE_LENGTH = 1024


def iso(value: Any) -> str | None:
    """A UTC contract timestamp from a Frappe datetime (naive values are UTC)."""
    if value in (None, ""):
        return None
    if isinstance(value, str):
        value = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        value = datetime.datetime.combine(value, datetime.time(), datetime.UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.UTC)
    return str(value.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))


def money(amount: Any, currency: Any) -> dict[str, str] | None:
    """Contract Money (decimal string, ISO currency) or None when not recorded."""
    if amount in (None, "") or not currency:
        return None
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    # Frappe Currency fields hold two decimals; "-0.00" is not a contract decimal.
    text = format(value.quantize(Decimal("0.01")) + Decimal("0.00"), "f")
    return {"amount": text, "currency": str(currency).upper()}


def sync_state(state: Any, last_attempt_at: Any = None, error_code: Any = None) -> dict[str, Any]:
    value = state if state in ("pending", "synced", "failed") else "pending"
    return {
        "state": value,
        "last_attempt_at": iso(last_attempt_at),
        "error_code": (str(error_code)[:64] or None) if error_code else None,
    }


def service_link(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """A ``Vati Service Link`` row (or ``ids.mapping`` view) as a contract ServiceLink."""
    service = row.get("service")
    if service not in CONTRACT_SERVICES:
        return None
    sync = row.get("sync")
    if isinstance(sync, Mapping):
        state = sync_state(sync.get("state"), sync.get("last_attempt_at"), sync.get("error_code"))
    else:
        state = sync_state(row.get("sync_state"), row.get("last_attempt_at"), row.get("error_code"))
    return {
        "service": service,
        "external_type": str(row.get("external_type") or "record")[:64],
        "external_id": str(row["external_id"])[:255] if row.get("external_id") else None,
        "sync": state,
    }


def service_links(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = [link for link in (service_link(r) for r in rows) if link is not None]
    return sorted(out, key=lambda x: (x["service"], x["external_type"], x["external_id"] or ""))


def refund_lane(lane: Any) -> str | None:
    return LANES.get(str(lane)) if lane else None


def taxpayer_summary(taxpayer: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "taxpayer_id": taxpayer["taxpayer_id"],
        "display_name": str(taxpayer.get("display_name") or "Taxpayer with no name on record"),
        "taxpayer_type": taxpayer.get("taxpayer_type") or "other",
    }


def case(
    identity: Mapping[str, Any],
    *,
    taxpayer: Mapping[str, Any],
    status: str | None,
    version: int,
    refund_claim: dict[str, str] | None,
    lane: str | None,
    links: Sequence[dict[str, Any]],
    updated_at: Any,
) -> dict[str, Any]:
    """Contract ``Case``. ``status`` is the Jube projection (``Vati Case Sync``).

    A case Jube has not reported on yet is RECEIVED: registration is all that is
    known, and the Jube service link shows whether Jube has acknowledged it.
    ``assigned_officer_id`` is null and ``selection_reasons`` empty because Frappe
    holds no copy of Jube's assignment or of RSL's selection reasons (ADR-046).
    """
    created = iso(identity.get("creation"))
    updated = iso(updated_at) or created
    return {
        "case_id": identity["case_id"],
        "tenant_id": identity["tenant_id"],
        "display_id": identity["display_id"],
        "taxpayer_id": identity["taxpayer_id"],
        "taxpayer": taxpayer_summary(taxpayer),
        "selection_reasons": [],
        "submission_id": identity["submission_id"],
        "status": status if status in CASE_STATUSES else "RECEIVED",
        "status_authority": "jube",
        "version": int(version),
        "refund_claim": refund_claim,
        "refund_lane": refund_lane(lane),
        "assigned_officer_id": None,
        "service_links": list(links),
        "created_at": created,
        "updated_at": max(updated, created) if updated and created else updated,
        "synthetic": bool(identity.get("synthetic")),
    }


def open_case_ref(item: Mapping[str, Any]) -> dict[str, Any]:
    return {"case_id": item["case_id"], "display_id": item["display_id"], "status": item["status"]}


def taxpayer_profile(
    taxpayer: Mapping[str, Any], *, open_cases: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """Contract ``TaxpayerProfile``. The full TIN never leaves Frappe."""
    masked = taxpayer.get("tin_masked") or None
    registered = taxpayer.get("registered_on")
    if isinstance(registered, datetime.date):
        registered = registered.isoformat()
    return {
        **taxpayer_summary(taxpayer),
        "tenant_id": taxpayer["tenant_id"],
        "tin_masked": masked,
        "registered_on": registered or None,
        "sector": taxpayer.get("sector") or None,
        "open_cases": [open_case_ref(c) for c in sorted(open_cases, key=lambda c: c["display_id"])],
        "service_links": [],
        "synthetic": bool(taxpayer.get("synthetic")),
    }


def evidence(view: Mapping[str, Any], *, case_id: str) -> dict[str, Any]:
    """Contract ``Evidence`` from ``evidence.store.evidence_view`` (Vati fields dropped).

    Evidence linked to a case through a service link carries no ``case_id`` of its
    own, so the case being listed is used.
    """
    source = view.get("source")
    return {
        "evidence_id": view["evidence_id"],
        "case_id": case_id,
        "tenant_id": view["tenant_id"],
        "version": int(view["version"]),
        "supersedes_evidence_id": view.get("supersedes_evidence_id") or None,
        "document_sha256": view["document_sha256"],
        "media_type": view["media_type"],
        "size_bytes": int(view["size_bytes"]),
        "document_type": view["document_type"],
        "source": source if source in EVIDENCE_SOURCES else "officer_upload",
        "received_at": view["received_at"],
        "extraction": dict(view["extraction"]),
        "service_links": list(view.get("service_links") or []),
        "synthetic": bool(view.get("synthetic")),
    }


def entity_uuid(tenant_id: str, ftm_id: str) -> str:
    return str(uuid.uuid5(ENTITY_NAMESPACE, f"{tenant_id}\x1f{ftm_id}"))


def entity_properties(properties: Mapping[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key in sorted(properties):
        raw = properties[key]
        values = raw if isinstance(raw, list) else [raw]
        texts = [str(v)[:_MAX_VALUE_LENGTH] for v in values if v is not None]
        if texts:
            out[key] = texts[:_MAX_PROPERTY_VALUES]
    return out


def entity(
    *,
    tenant_id: str,
    case_id: str,
    ftm: Mapping[str, Any],
    extension: Mapping[str, Any] | None,
    index_row: Mapping[str, Any] | None,
    candidates: Sequence[Mapping[str, Any]],
    synthetic: bool,
) -> dict[str, Any] | None:
    """Contract ``Entity`` for one mapped FtM entity, or None for schemata the
    contract does not list (Documents are evidence, not entities)."""
    schema = str(ftm.get("schema"))
    if schema not in ENTITY_SCHEMAS:
        return None
    ftm_id = str(ftm["id"])
    cited = (extension or {}).get("evidence") or []
    evidence_ids = sorted({str(c["evidence_id"]) for c in cited if c.get("evidence_id")})
    link: dict[str, Any] = {"service": "openaleph", "external_type": "entity"}
    if index_row:
        state = "pending" if index_row.get("reindex_required") else "synced"
        link.update(external_id=ftm_id, sync=sync_state(state, index_row.get("indexed_at")))
    else:
        link.update(external_id=None, sync=sync_state("pending"))
    return {
        "entity_id": entity_uuid(tenant_id, ftm_id),
        "case_id": case_id,
        "tenant_id": tenant_id,
        "ftm_schema": schema,
        "properties": entity_properties(ftm.get("properties") or {}),
        "evidence_ids": evidence_ids,
        "match_candidates": [dict(c) for c in candidates],
        "service_links": [link],
        "synthetic": synthetic,
    }


def _officer(user: Any) -> str | None:
    return officer_id(str(user)) if user else None


def decision(view: Mapping[str, Any]) -> dict[str, Any]:
    """Contract ``Decision`` from ``decisions.engine.decision_view``."""
    keys = (
        "decision_id", "case_id", "tenant_id", "status", "origin", "outcome", "operation",
        "target_status", "rationale", "evidence_ids", "finding_ids", "rating", "draft_id",
        "proposed_at", "case_status_at_proposal", "case_version", "reviewed_at", "review_reason",
        "jube_sync",
    )  # fmt: skip
    out = {k: view[k] for k in keys}
    out["proposed_by"] = _officer(view["proposed_by"])
    out["reviewed_by"] = _officer(view.get("reviewed_by"))
    return out


def evidence_request(view: Mapping[str, Any]) -> dict[str, Any]:
    """Contract ``EvidenceRequest`` from ``decisions.engine.evidence_request_view``."""
    keys = (
        "request_id", "case_id", "tenant_id", "status", "finding_ids", "items", "due_date",
        "case_version", "service_links", "created_at",
    )  # fmt: skip
    out = {k: view[k] for k in keys}
    out["authorised_by"] = officer_id(str(view["authorised_by"]))
    return out


def review_event(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "sequence": event.get("sequence"),
        "actor_id": officer_id(str(event["actor"])),
        "actor_role": event["actor_role"],
        "action": event["action"],
        "subject_type": event["subject_type"],
        "subject_id": event["subject_id"],
        "occurred_at": event["occurred_at"],
        "outcome": str(event["outcome"])[:64],
    }


def case_review(state: Mapping[str, Any]) -> dict[str, Any]:
    """Contract ``CaseReview`` from ``decisions.store.case_review_state``."""
    viewer = state["viewer"]
    open_decision = state.get("open_decision")
    return {
        "case_id": state["case_id"],
        "tenant_id": state["tenant_id"],
        "case_version": state["case_version"],
        "viewer": {
            "officer_id": officer_id(str(viewer["actor"])),
            "roles": [r for r in OFFICER_ROLES if r in viewer["roles"]],
            "allowed_actions": list(viewer["allowed_actions"]),
        },
        "open_decision": decision(open_decision) if open_decision else None,
        "audit_events": [review_event(e) for e in state["audit_events"]],
    }


def officer_roles(roles: Iterable[str]) -> list[str]:
    held = set(roles)
    return [r for r in OFFICER_ROLES if r in held]


# TAX-146 workspace apps: the native investigation and explore screens are permitted for
# officers but not connected until TAX-106, so they are listed as unavailable.
NOT_CONNECTED_APPS = ("investigation", "explore")


def workspace_session(
    *, tenant_id: str, tenant_name: str, user: str, display_name: str, roles: Iterable[str],
    synthetic: bool,
) -> dict[str, Any] | None:  # fmt: skip
    """Contract ``WorkspaceSession`` (getWorkspaceSession), or None for a non-officer.

    The same navigation rules as the fixture server: Cases for every officer, Reports with
    the management and integrity views for supervisors only, no Admin app."""
    held = officer_roles(roles)
    if not held:
        return None
    apps: list[dict[str, Any]] = [{"key": "cases", "availability": "available", "reason": None}]
    apps += [
        {"key": key, "availability": "unavailable", "reason": "not_connected"}
        for key in NOT_CONNECTED_APPS
    ]
    views = ["overview", "case_queue"]
    if "supervisor" in held:
        apps.append({"key": "reports", "availability": "available", "reason": None})
        views += ["management", "integrity"]
    return {
        "tenant_id": tenant_id,
        "tenant_name": (tenant_name or "Tenant with no name on record")[:120],
        "officer_id": officer_id(user),
        "display_name": (display_name or user)[:120],
        "roles": held,
        "apps": apps,
        "views": views,
        "synthetic": synthetic,
    }
