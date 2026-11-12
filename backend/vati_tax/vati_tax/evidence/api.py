"""Whitelisted evidence methods for the taxpayer upload form (TAX-043).

``X-Vati-Tenant-ID`` selects the tenant (TAX-029). Uploads are multipart POSTs
with the document in the ``file`` part; the body is read with a hard bound so an
oversized upload is refused before it is stored. Errors keep the ADR-003 code in
the exception title and never echo document content.
"""

from __future__ import annotations

from typing import Any, NoReturn

import frappe

from vati_tax.audit import store as audit_store
from vati_tax.evidence import rules, store
from vati_tax.ids.canonical import MappingError
from vati_tax.refund_access import store as refund_access


class EvidenceRequestError(frappe.ValidationError):
    http_status_code = 422


class EvidenceBadRequest(frappe.ValidationError):
    http_status_code = 400


def _raise(exc: MappingError) -> NoReturn:
    if exc.http_status == 404:
        frappe.throw(str(exc), frappe.DoesNotExistError, title=exc.code)
    cls = EvidenceBadRequest if exc.http_status == 400 else EvidenceRequestError
    reason = exc.field_errors[0]["code"] if exc.field_errors else exc.code
    frappe.throw(str(exc), cls, title=f"{exc.code}:{reason}")
    raise AssertionError("unreachable")


@frappe.whitelist(methods=["POST"])
def upload_evidence(
    document_type: str,
    idempotency_key: str,
    taxpayer_id: str | None = None,
    media_type: str | None = None,
    case_id: str | None = None,
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    supersedes_evidence_id: str | None = None,
) -> dict[str, Any]:
    refund_access.require_case(case_id, "evidence.register")  # TAX-142
    request = getattr(frappe.local, "request", None)
    upload = request.files.get("file") if request is not None else None
    if upload is None:
        _raise(rules.UploadRejected("A file part is required.", "file", "missing_file"))
    # Read one byte past the limit: enough to know it is too large, never more.
    content = upload.stream.read(rules.MAX_DOCUMENT_BYTES + 1)
    try:
        return store.register_upload(
            tenant_id=store.permissions.request_tenant_header() or "",
            content=content,
            filename=upload.filename,
            document_type=document_type,
            idempotency_key=idempotency_key,
            taxpayer_id=taxpayer_id or None,
            media_type=media_type or None,
            case_id=case_id or None,
            reference_doctype=reference_doctype or None,
            reference_name=reference_name or None,
            supersedes_evidence_id=supersedes_evidence_id or None,
        )
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["GET"])
def get_evidence(evidence_id: str) -> dict[str, Any]:
    refund_access.require_record("Vati Evidence", evidence_id, "evidence.read")  # TAX-142
    tenant_id = store.permissions.request_tenant_header() or ""
    try:
        view = store.get_evidence(tenant_id=tenant_id, evidence_id=evidence_id)
    except MappingError as exc:
        _raise(exc)
    # TAX-059: who read which evidence is kept in the tenant audit ledger.
    audit_store.record_read(
        audit_store.context(tenant_id),
        action="evidence.read",
        subject_type="evidence",
        subject_id=view["evidence_id"],
        case_id=view.get("case_id"),
        taxpayer_id=view.get("taxpayer_id"),
    )
    return view
