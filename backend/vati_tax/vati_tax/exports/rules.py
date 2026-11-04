"""Evidence export, manifest and retention rules (TAX-062), independent of Frappe.

An export is a request to hand a scoped set of one case's evidence to a named
recipient for a stated purpose. It moves through ``requested`` -> ``approved``
or ``rejected``; an unanswered request lapses. The manifest is built and frozen
when a supervisor approves, so what was authorised is exactly what is read.

The manifest lists, per evidence version, the SHA-256 of the original, its
lineage and the extraction revision it was read with, plus retention metadata.
It never contains document content, file paths or extracted text.

Retention here is metadata, not deletion. The pilot runs on synthetic data and
no RSL record-retention period has been verified, so the default schedule sets
no disposal date and every record is marked ``disposal: manual_review_only``.
A site may configure periods per class for rehearsal; they are still labelled
unverified. The audit ledger is never given a disposal date: its hash chain must
stay verifiable for the life of the site.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from vati_tax.ids.canonical import (
    InvalidMapping,
    MappingError,
    canonical_uuid,
    validate_idempotency_key,
)

MANIFEST_SCHEMA: Final = "vati.evidence-export-manifest/1"

Status = Literal["requested", "approved", "rejected"]
STATUSES: Final[tuple[Status, ...]] = ("requested", "approved", "rejected")

# Why evidence is leaving the case file. Deliberately generic: a purpose names
# an internal reason, not a legal power. Real disclosure routes need RSL review.
PURPOSES: Final[tuple[str, ...]] = (
    "supervisory_review",  # review by RSL staff outside the case team
    "specialist_review",  # a named specialist reviewing the evidence offline
    "taxpayer_copy",  # a copy of the taxpayer's own submitted evidence
    "authorised_disclosure",  # a disclosure an authorised official has approved
)

REASON_MIN: Final = 20
REASON_MAX: Final = 2000
RECIPIENT_MAX: Final = 200
MAX_EVIDENCE: Final = 200
# A request waits at most this long for a supervisor; an approval lets the
# requester and approver read the manifest for this long.
REQUEST_TTL: Final = datetime.timedelta(hours=72)
ACCESS_TTL: Final = datetime.timedelta(hours=24)

# --- retention ----------------------------------------------------------------------

RETENTION_POLICY_ID: Final = "pilot-synthetic-unverified-1"
RETENTION_NOTE: Final = (
    "Pilot placeholder. No RSL record-retention period has been verified; nothing is "
    "disposed of automatically. Replace through a reviewed change before real data."
)
RETENTION_CLASSES: Final[tuple[str, ...]] = ("evidence", "export_manifest", "audit_ledger")
# Classes that never get a disposal date, whatever the site configures.
PERMANENT_CLASSES: Final[frozenset[str]] = frozenset({"audit_ledger"})
RETAIN_DAYS_MAX: Final = 36_500


class ExportNotFound(MappingError):
    """Unknown export, or one outside the caller's tenant (reported identically)."""

    code = "not_found"
    http_status = 404


class ExportConflict(MappingError):
    """The export is not in a state that allows this step, or is stale."""

    code = "invalid_domain_input"
    http_status = 422


def utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(datetime.UTC).replace(microsecond=0)


def iso(value: datetime.datetime | None) -> str | None:
    return utc(value).isoformat().replace("+00:00", "Z") if value else None


def digest(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def retention_schedule(config: Mapping[str, Any] | None = None) -> dict[str, int | None]:
    """Days to retain per class: ``None`` means no disposal date is set.

    ``config`` is the site's ``vati_retention_days`` map. Unknown classes, a
    non-integer or an out-of-range value are refused rather than ignored, so a
    typo cannot silently shorten retention. Permanent classes stay ``None``.
    """
    schedule: dict[str, int | None] = {name: None for name in RETENTION_CLASSES}
    for name, days in (config or {}).items():
        if name not in RETENTION_CLASSES:
            raise InvalidMapping(f"Unknown retention class {name!r}.", "retention", "unknown")
        if name in PERMANENT_CLASSES:
            raise InvalidMapping(f"{name} cannot have a disposal date.", "retention", "permanent")
        if days is None:
            continue
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= RETAIN_DAYS_MAX:
            raise InvalidMapping(
                f"Retention for {name} must be 1 to {RETAIN_DAYS_MAX} days.", "retention", "invalid"
            )
        schedule[name] = days
    return schedule


def retention_metadata(
    retention_class: str,
    retain_from: datetime.datetime,
    schedule: Mapping[str, int | None],
) -> dict[str, Any]:
    """Retention metadata for one record. Never a deletion instruction."""
    if retention_class not in RETENTION_CLASSES:
        raise ValueError(f"unknown retention class {retention_class!r}")
    days = None if retention_class in PERMANENT_CLASSES else schedule.get(retention_class)
    until = utc(retain_from) + datetime.timedelta(days=days) if days else None
    return {
        "policy_id": RETENTION_POLICY_ID,
        "policy_verified": False,
        "retention_class": retention_class,
        "retain_from": iso(retain_from),
        "retain_days": days,
        "review_after": iso(until),
        "disposal": "manual_review_only",
    }


def retention_view(schedule: Mapping[str, int | None]) -> dict[str, Any]:
    return {
        "policy_id": RETENTION_POLICY_ID,
        "policy_verified": False,
        "note": RETENTION_NOTE,
        "classes": [
            {
                "retention_class": name,
                "retain_days": None if name in PERMANENT_CLASSES else schedule.get(name),
                "permanent": name in PERMANENT_CLASSES,
                "disposal": "manual_review_only",
            }
            for name in RETENTION_CLASSES
        ],
    }


# --- requests -----------------------------------------------------------------------


def required_text(body: Mapping[str, Any], field: str, low: int, high: int) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InvalidMapping(f"{field} is required.", field, "required")
    value = value.strip()
    if not low <= len(value) <= high:
        raise InvalidMapping(
            f"{field} must be {low} to {high} characters.", field, "invalid_length"
        )
    return value


@dataclass(frozen=True)
class ExportRequest:
    case_id: str
    evidence_ids: tuple[str, ...]
    purpose: str
    recipient: str
    reason: str
    idempotency_key: str

    @classmethod
    def build(cls, body: Mapping[str, Any], idempotency_key: str) -> ExportRequest:
        purpose = body.get("purpose")
        if purpose not in PURPOSES:
            raise InvalidMapping("Unknown export purpose.", "purpose", "unknown_purpose")
        raw = body.get("evidence_ids")
        if not isinstance(raw, (list, tuple)) or not raw:
            raise InvalidMapping(
                "Name the evidence to export; a whole-case export is not allowed.",
                "evidence_ids",
                "required",
            )
        if len(raw) > MAX_EVIDENCE:
            raise InvalidMapping(
                f"At most {MAX_EVIDENCE} evidence items per export.", "evidence_ids", "too_many"
            )
        ids = [canonical_uuid(item, "evidence_ids") for item in raw]
        if len(set(ids)) != len(ids):
            raise InvalidMapping("Evidence is listed twice.", "evidence_ids", "duplicate")
        return cls(
            case_id=canonical_uuid(body.get("case_id"), "case_id"),
            evidence_ids=tuple(sorted(ids)),
            purpose=str(purpose),
            recipient=required_text(body, "recipient", 3, RECIPIENT_MAX),
            reason=required_text(body, "reason", REASON_MIN, REASON_MAX),
            idempotency_key=validate_idempotency_key(idempotency_key),
        )

    def payload(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "evidence_ids": list(self.evidence_ids),
            "purpose": self.purpose,
            "recipient": self.recipient,
            "reason": self.reason,
        }

    def digest(self) -> str:
        return digest(self.payload())


_EXPORT_NS: Final = uuid.UUID("3c7b2f0e-9a51-4d8e-8f2a-62e0d4b1c062")


def export_id(tenant_id: str, idempotency_key: str) -> str:
    """Deterministic per (tenant, key): a replayed request finds its record."""
    return str(uuid.uuid5(_EXPORT_NS, f"{tenant_id}|{idempotency_key}"))


@dataclass(frozen=True)
class ExportRecord:
    export_id: str
    tenant_id: str
    case_id: str
    evidence_ids: tuple[str, ...]
    purpose: str
    recipient: str
    reason: str
    request_digest: str
    requested_by: str
    requested_at: datetime.datetime
    status: Status = "requested"
    version: int = 1
    decided_by: str | None = None
    decided_at: datetime.datetime | None = None
    decision_reason: str | None = None
    manifest: dict[str, Any] | None = None
    manifest_sha256: str | None = None


def effective_status(record: ExportRecord, now: datetime.datetime) -> str:
    """``requested`` past ``REQUEST_TTL`` reads as ``lapsed``; nothing is stored."""
    if record.status == "requested" and utc(now) - utc(record.requested_at) > REQUEST_TTL:
        return "lapsed"
    return record.status


def check_decidable(record: ExportRecord, expected_version: object, now: datetime.datetime) -> None:
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise InvalidMapping("expected_version must be an integer.", "expected_version", "required")
    status = effective_status(record, now)
    if status == "lapsed":
        raise ExportConflict(
            "The request is older than 72 hours; make a new one.", "export_id", "lapsed"
        )
    if status != "requested":
        raise ExportConflict("The export has already been decided.", "export_id", "not_pending")
    if expected_version != record.version:
        raise ExportConflict(
            "The export changed since you loaded it.", "expected_version", "stale_version"
        )


def check_readable(record: ExportRecord, user: str, now: datetime.datetime) -> None:
    """Only an approved, unexpired export, and only to its requester or approver.

    Raises ``ExportNotFound`` for anyone else so the record cannot be probed.
    """
    people = {record.requested_by.lower(), (record.decided_by or "").lower()}
    if user.lower() not in people:
        raise ExportNotFound("Export not found.", "export_id", "not_found")
    if record.status != "approved" or record.manifest is None:
        raise ExportConflict("The export has not been approved.", "export_id", "not_approved")
    if record.decided_at is None or utc(now) - utc(record.decided_at) > ACCESS_TTL:
        raise ExportConflict(
            "The approval is older than 24 hours; request the export again.",
            "export_id",
            "access_expired",
        )


# --- manifest -----------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceItem:
    """One evidence version as the manifest needs it (no content, no path)."""

    evidence_id: str
    tenant_id: str
    taxpayer_id: str | None
    case_id: str | None
    version: int
    lineage_id: str
    supersedes_evidence_id: str | None
    document_sha256: str
    media_type: str
    size_bytes: int
    document_type: str
    source: str
    received_at: datetime.datetime
    synthetic: bool
    extraction: Mapping[str, Any] | None = None


def check_scope(
    evidence_ids: Sequence[str], tenant_id: str, case_evidence: Iterable[EvidenceItem]
) -> list[EvidenceItem]:
    """Every requested evidence must belong to the case inside the tenant.

    ``case_evidence`` is the case's evidence as the evidence store sees it
    (registered for the case or linked to it later). Anything else is reported
    as ``not_found`` without saying which reason, so an export request cannot be
    used to probe other cases, taxpayers or tenants. The pilot also refuses to
    export anything not marked synthetic.
    """
    available = {item.evidence_id: item for item in case_evidence if item.tenant_id == tenant_id}
    missing = [eid for eid in evidence_ids if eid not in available]
    if missing:
        raise ExportNotFound("Evidence not found for this case.", "evidence_ids", "not_found")
    chosen = [available[eid] for eid in evidence_ids]
    if any(not item.synthetic for item in chosen):
        raise ExportConflict(
            "The pilot exports synthetic evidence only.", "evidence_ids", "not_synthetic"
        )
    return chosen


def _entry(item: EvidenceItem, schedule: Mapping[str, int | None]) -> dict[str, Any]:
    extraction = None
    if item.extraction:
        ex = item.extraction
        extraction = {
            "extraction_id": ex.get("extraction_id"),
            "revision": ex.get("revision"),
            "status": ex.get("status"),
            "extractor": ex.get("extractor"),
            "extractor_version": ex.get("extractor_version"),
            "document_sha256": ex.get("document_sha256"),
            # The extraction read exactly these bytes, or the manifest says so.
            "matches_document": ex.get("document_sha256") == item.document_sha256,
        }
    return {
        "evidence_id": item.evidence_id,
        "taxpayer_id": item.taxpayer_id,
        "version": item.version,
        "lineage_id": item.lineage_id,
        "supersedes_evidence_id": item.supersedes_evidence_id,
        "document_sha256": item.document_sha256,
        "media_type": item.media_type,
        "size_bytes": item.size_bytes,
        "document_type": item.document_type,
        "source": item.source,
        "received_at": iso(item.received_at),
        "synthetic": item.synthetic,
        "extraction": extraction,
        "retention": retention_metadata("evidence", item.received_at, schedule),
    }


def build_manifest(
    record: ExportRecord,
    items: Sequence[EvidenceItem],
    *,
    approved_by: str,
    approved_at: datetime.datetime,
    schedule: Mapping[str, int | None],
) -> dict[str, Any]:
    """The frozen hash/provenance manifest with its own ``manifest_sha256``."""
    entries = [_entry(item, schedule) for item in sorted(items, key=lambda i: i.evidence_id)]
    body: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "export_id": record.export_id,
        "tenant_id": record.tenant_id,
        "case_id": record.case_id,
        "purpose": record.purpose,
        "recipient": record.recipient,
        "requested_by": record.requested_by,
        "requested_at": iso(record.requested_at),
        "approved_by": approved_by,
        "approved_at": iso(approved_at),
        "entry_count": len(entries),
        "entries": entries,
        "retention": retention_metadata("export_manifest", approved_at, schedule),
        "synthetic": all(item.synthetic for item in items),
        "content_included": False,
    }
    return {**body, "manifest_sha256": digest(body)}


def verify_manifest(manifest: Mapping[str, Any]) -> bool:
    """Recompute ``manifest_sha256``; False if any field was changed."""
    body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    return manifest.get("manifest_sha256") == digest(body)


def export_view(record: ExportRecord, now: datetime.datetime) -> dict[str, Any]:
    """Status view. The manifest itself is only returned by the manifest read."""
    return {
        "export_id": record.export_id,
        "tenant_id": record.tenant_id,
        "case_id": record.case_id,
        "evidence_ids": list(record.evidence_ids),
        "purpose": record.purpose,
        "recipient": record.recipient,
        "reason": record.reason,
        "status": effective_status(record, now),
        "version": record.version,
        "requested_by": record.requested_by,
        "requested_at": iso(record.requested_at),
        "decided_by": record.decided_by,
        "decided_at": iso(record.decided_at),
        "decision_reason": record.decision_reason,
        "manifest_sha256": record.manifest_sha256,
    }
