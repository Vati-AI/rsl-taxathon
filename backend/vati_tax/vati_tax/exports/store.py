"""Frappe binding for evidence export (TAX-062).

``FrappeExports`` stores ``Vati Evidence Export`` rows; ``FrappeEvidence`` reads
a case's evidence through the TAX-032 evidence store (which applies the session
user's tenant scope) plus each item's latest extraction revision. The retention
schedule comes from the site's ``vati_retention_days`` (``site_config.json``).

Refusals are written to the audit ledger by ``service`` before it raises. A
whitelisted call that raises is rolled back by Frappe, so ``_call`` commits
first: on every refusal path the only rows written are those ledger entries.
GET reads are committed too, because Frappe does not commit GET requests.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import uuid
from collections.abc import Callable, Iterator
from typing import Any, TypeVar

import frappe

from vati_tax.audit import ledger
from vati_tax.audit import store as audit_store
from vati_tax.audit.store import FrappeLedger
from vati_tax.evidence import store as evidence_store
from vati_tax.exports import rules, service
from vati_tax.exports.rules import EvidenceItem, ExportRecord
from vati_tax.identity import policy
from vati_tax.ids.canonical import MappingError

EXPORT = "Vati Evidence Export"
CASE = "Vati Case Identity"
EXTRACTION = "Vati Evidence Extraction"
_UNIQUE = (frappe.UniqueValidationError, frappe.DuplicateEntryError)
_FIELDS = [
    "export_id",
    "tenant_id",
    "case_id",
    "status",
    "version",
    "purpose",
    "recipient",
    "reason",
    "evidence_ids",
    "request_digest",
    "requested_by",
    "requested_at",
    "decided_by",
    "decided_at",
    "decision_reason",
    "manifest",
    "manifest_sha256",
]
T = TypeVar("T")


def _naive(value: datetime.datetime | None) -> datetime.datetime | None:
    return value.astimezone(datetime.UTC).replace(tzinfo=None) if value else None


def _aware(value: Any) -> datetime.datetime | None:
    if not value:
        return None
    return frappe.utils.get_datetime(value).replace(tzinfo=datetime.UTC)


def _record(row: Any) -> ExportRecord:
    requested_at = _aware(row.requested_at)
    assert requested_at is not None  # reqd field
    return ExportRecord(
        export_id=row.export_id,
        tenant_id=row.tenant_id,
        case_id=row.case_id,
        evidence_ids=tuple(json.loads(row.evidence_ids or "[]")),
        purpose=row.purpose,
        recipient=row.recipient,
        reason=row.reason,
        request_digest=row.request_digest,
        requested_by=row.requested_by,
        requested_at=requested_at,
        status=row.status,
        version=int(row.version),
        decided_by=row.decided_by or None,
        decided_at=_aware(row.decided_at),
        decision_reason=row.decision_reason or None,
        manifest=json.loads(row.manifest) if row.manifest else None,
        manifest_sha256=row.manifest_sha256 or None,
    )


class FrappeExports:
    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        savepoint = "vati_export_" + uuid.uuid4().hex[:12]
        frappe.db.savepoint(savepoint)
        try:
            yield
        except BaseException:
            frappe.db.rollback(save_point=savepoint)
            raise

    def get(self, tenant_id: str, export_id: str) -> ExportRecord | None:
        row = frappe.db.get_value(EXPORT, export_id, _FIELDS, as_dict=True)
        if not row or row.tenant_id != tenant_id:
            return None
        return _record(row)

    def insert(self, record: ExportRecord) -> None:
        doc = frappe.get_doc(
            {
                "doctype": EXPORT,
                "export_id": record.export_id,
                "tenant_id": record.tenant_id,
                "case_id": record.case_id,
                "status": record.status,
                "version": record.version,
                "purpose": record.purpose,
                "recipient": record.recipient,
                "reason": record.reason,
                "evidence_ids": json.dumps(list(record.evidence_ids)),
                "request_digest": record.request_digest,
                "requested_by": record.requested_by,
                "requested_at": _naive(record.requested_at),
            }
        )
        try:
            doc.insert(ignore_permissions=True)
        except _UNIQUE as exc:
            raise ledger.Duplicate(str(exc)) from exc

    def update(self, record: ExportRecord, expected_version: int) -> bool:
        """Compare-and-set on ``version``: the row is locked, then checked."""
        rows = frappe.db.sql(
            f"select `version` from `tab{EXPORT}` where `name` = %s for update",  # noqa: S608
            (record.export_id,),
        )
        if not rows or int(rows[0][0]) != expected_version:
            return False
        doc = frappe.get_doc(EXPORT, record.export_id)
        doc.update(
            {
                "status": record.status,
                "version": record.version,
                "decided_by": record.decided_by,
                "decided_at": _naive(record.decided_at),
                "decision_reason": record.decision_reason,
                "manifest": json.dumps(record.manifest, sort_keys=True)
                if record.manifest
                else None,
                "manifest_sha256": record.manifest_sha256,
            }
        )
        doc.save(ignore_permissions=True)
        return True


class FrappeEvidence:
    def case_exists(self, tenant_id: str, case_id: str) -> bool:
        return frappe.db.get_value(CASE, case_id, "tenant_id") == tenant_id

    def case_evidence(self, tenant_id: str, case_id: str) -> list[EvidenceItem]:
        views = evidence_store.case_evidence(tenant_id=tenant_id, case_id=case_id)
        return [self._item(view) for view in views]

    @staticmethod
    def _item(view: dict[str, Any]) -> EvidenceItem:
        latest = frappe.get_all(
            EXTRACTION,
            filters={"evidence_id": view["evidence_id"]},
            fields=[
                "name",
                "revision",
                "status",
                "extractor",
                "extractor_version",
                "document_sha256",
            ],
            order_by="revision desc",
            limit=1,
        )
        extraction = None
        if latest:
            row = latest[0]
            extraction = {
                "extraction_id": row.name,
                "revision": int(row.revision),
                "status": row.status,
                "extractor": row.extractor,
                "extractor_version": row.extractor_version,
                "document_sha256": row.document_sha256,
            }
        received = _aware(view["received_at"])
        assert received is not None  # every evidence has a receipt time
        return EvidenceItem(
            evidence_id=view["evidence_id"],
            tenant_id=view["tenant_id"],
            taxpayer_id=view.get("taxpayer_id"),
            case_id=view.get("case_id"),
            version=int(view["version"]),
            lineage_id=view["lineage_id"],
            supersedes_evidence_id=view.get("supersedes_evidence_id"),
            document_sha256=view["document_sha256"],
            media_type=view["media_type"],
            size_bytes=int(view["size_bytes"]),
            document_type=view["document_type"],
            source=view["source"],
            received_at=received,
            synthetic=bool(view.get("synthetic")),
            extraction=extraction,
        )


def schedule() -> dict[str, int | None]:
    return rules.retention_schedule(frappe.conf.get("vati_retention_days") or None)


def ports() -> service.Ports:
    return service.Ports(
        exports=FrappeExports(),
        evidence=FrappeEvidence(),
        ledger=FrappeLedger(),
        schedule=schedule(),
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


# --- service functions --------------------------------------------------------------


def request_export(
    *, tenant_id: str, body: dict[str, Any], idempotency_key: str, correlation_id: str | None
) -> dict[str, Any]:
    ctx = audit_store.context(tenant_id, correlation_id)
    return _call(
        lambda: service.request_export(ctx, ports(), body=body, idempotency_key=idempotency_key)
    )


def decide_export(
    *,
    tenant_id: str,
    export_id: str,
    expected_version: object,
    approve: bool,
    reason: str | None,
    correlation_id: str | None,
) -> dict[str, Any]:
    ctx = audit_store.context(tenant_id, correlation_id)
    step = service.approve_export if approve else service.reject_export
    return _call(
        lambda: step(
            ctx, ports(), export_id=export_id, expected_version=expected_version, reason=reason
        )
    )


def get_export(*, tenant_id: str, export_id: str) -> dict[str, Any]:
    ctx = audit_store.context(tenant_id)
    return _call(lambda: service.get_export(ctx, ports(), export_id=export_id))


def get_manifest(*, tenant_id: str, export_id: str) -> dict[str, Any]:
    ctx = audit_store.context(tenant_id)
    return _call(lambda: service.get_manifest(ctx, ports(), export_id=export_id))


def retention_policy(*, tenant_id: str) -> dict[str, Any]:
    ctx = audit_store.context(tenant_id)
    try:
        return service.retention_policy(ctx, ports())
    except policy.ActionDenied as exc:
        raise frappe.PermissionError(str(exc)) from exc
