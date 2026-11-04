"""Persist evidence, extraction revisions and provenance in Frappe (TAX-032).

Internal server-side API for the taxpayer upload form (TAX-043), the Docling
adapter (TAX-038), the OpenAleph mapping (TAX-036) and later Vati routes. Every
function authorises the *current session user* with the TAX-029 action matrix
(``require_action``) inside the tenant passed in, so a background job must run
as the service identity (``frappe.set_user``), not as Administrator.

Inserts use ``ignore_permissions`` because the DocTypes are read-only in desk;
the explicit action, tenant and taxpayer checks here replace the DocType create
permission, and the bench tests prove the negative cases. Reads of the
reference/case/predecessor records use ``frappe.db`` and are always compared with
the authorised tenant (and taxpayer) before anything is returned.

Storage: the original bytes go into a private Frappe ``File`` (``is_private=1``,
``/private/files/...``) named after the evidence ID. The SHA-256 is computed
here from the bytes, then re-read from disk after the write and compared, so the
recorded hash is the hash of what is actually stored. ``guard_evidence_file``
and ``file_has_permission`` stop that file from being made public, re-pointed,
edited or deleted through Frappe.
"""

from __future__ import annotations

import datetime
import os
import uuid
from typing import Any

import frappe

from vati_tax.evidence import rules
from vati_tax.evidence.rules import EvidenceConflict, UploadRejected
from vati_tax.identity import permissions, policy
from vati_tax.ids import mapping
from vati_tax.ids.canonical import (
    CaseNotFound,
    IdempotencyConflict,
    InvalidMapping,
    MappingError,
    TenantBoundaryError,
    canonical_uuid,
    validate_idempotency_key,
)

EVIDENCE = "Vati Evidence"
EXTRACTION = "Vati Evidence Extraction"
CASE = "Vati Case Identity"
FILE = "File"
_RACE_ERRORS = (frappe.UniqueValidationError, frappe.DuplicateEntryError)
_EVIDENCE_FIELDS = [
    "name",
    "tenant_id",
    "taxpayer_id",
    "case_id",
    "reference_doctype",
    "reference_name",
    "version",
    "supersedes_evidence_id",
    "lineage_id",
    "document_type",
    "source",
    "received_at",
    "synthetic",
    "document_sha256",
    "media_type",
    "size_bytes",
    "original_filename",
    "file_ref",
    "file_url",
    "request_digest",
]


class EvidenceNotFound(CaseNotFound):
    """Missing evidence, or evidence outside the caller's tenant/taxpayer.

    Both are reported the same way so a caller cannot probe other tenants.
    """


def _log(action: str, outcome: str, **ids: Any) -> None:
    # IDs and outcomes only: never file names, content or taxpayer names.
    frappe.logger("vati_tax.evidence").info({"action": action, "outcome": outcome, **ids})


def _utc() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _iso(value: Any) -> str:
    return frappe.utils.get_datetime(value).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- authorisation -----------------------------------------------------------------


def _register_scope(tenant_id: str, taxpayer_id: str | None) -> tuple[policy.TenantScope, str]:
    """Authorise ``evidence.register`` and resolve the owning taxpayer."""
    scope = permissions.resolve_scope(tenant_id)
    if taxpayer_id is None and scope.roles == {policy.TAXPAYER} and len(scope.taxpayer_ids) == 1:
        taxpayer_id = next(iter(scope.taxpayer_ids))
    if taxpayer_id is None:
        raise InvalidMapping("taxpayer_id is required.", "taxpayer_id", "invalid_uuid")
    taxpayer_id = canonical_uuid(taxpayer_id, "taxpayer_id")
    permissions.require_action(policy.EVIDENCE_REGISTER, scope.tenant_id, taxpayer_id=taxpayer_id)
    return scope, taxpayer_id


def _read_scope(tenant_id: str) -> policy.TenantScope:
    return permissions.require_action(policy.EVIDENCE_READ, tenant_id)


# --- lookups -----------------------------------------------------------------------


def _evidence_row(evidence_id: str) -> Any:
    return frappe.db.get_value(EVIDENCE, evidence_id, _EVIDENCE_FIELDS, as_dict=True)


def _owned_evidence(tenant_id: str, evidence_id: str, taxpayer_id: str | None = None) -> Any:
    """The evidence row if it is in this tenant (and taxpayer), else ``EvidenceNotFound``."""
    evidence_id = canonical_uuid(evidence_id, "evidence_id")
    row = _evidence_row(evidence_id)
    if not row or row.tenant_id != tenant_id:
        raise EvidenceNotFound("Evidence not found.", "evidence_id", "not_found")
    if taxpayer_id is not None and row.taxpayer_id != taxpayer_id:
        raise EvidenceNotFound("Evidence not found.", "evidence_id", "not_found")
    return row


def _check_case(reg: rules.Registration) -> None:
    if reg.case_id is None:
        return
    case = frappe.db.get_value(CASE, reg.case_id, ["tenant_id", "taxpayer_id"], as_dict=True)
    if not case:
        raise CaseNotFound("Case not found.", "case_id", "not_found")
    if case.tenant_id != reg.tenant_id:
        raise TenantBoundaryError("Case not found.", "case_id", "not_found")
    if case.taxpayer_id and case.taxpayer_id != reg.taxpayer_id:
        raise EvidenceConflict(
            "The case belongs to another taxpayer.", "case_id", "taxpayer_mismatch"
        )


def _check_reference(reg: rules.Registration) -> None:
    """The submission/request must be a tenant-scoped Vati record the caller can read."""
    if reg.reference_doctype is None:
        return
    missing = CaseNotFound("Reference not found.", "reference_name", "not_found")
    if not frappe.db.exists("DocType", reg.reference_doctype):
        raise missing
    scoped, taxpayer_owned = permissions.scope_of(reg.reference_doctype)
    if not scoped:
        raise InvalidMapping(
            "Evidence can only reference a tenant-scoped Vati record.",
            "reference_doctype",
            "unsupported_reference",
        )
    fields = ["tenant_id", "taxpayer_id"] if taxpayer_owned else ["tenant_id"]
    row = frappe.db.get_value(reg.reference_doctype, reg.reference_name, fields, as_dict=True)
    if not row or row.tenant_id != reg.tenant_id:
        raise missing
    if taxpayer_owned and row.taxpayer_id != reg.taxpayer_id:
        raise missing
    if not frappe.has_permission(reg.reference_doctype, "read", doc=reg.reference_name):
        raise missing


def _lineage(reg: rules.Registration, document_sha256: str) -> tuple[int, str | None, Any]:
    """(version, lineage_id, predecessor) for a new version; checks the supersede rules."""
    if reg.supersedes_evidence_id is None:
        return 1, None, None
    try:
        prev = _owned_evidence(reg.tenant_id, reg.supersedes_evidence_id, reg.taxpayer_id)
    except EvidenceNotFound:
        raise EvidenceNotFound(
            "Evidence not found.", "supersedes_evidence_id", "not_found"
        ) from None
    if not frappe.has_permission(EVIDENCE, "read", doc=prev.name):
        raise EvidenceNotFound("Evidence not found.", "supersedes_evidence_id", "not_found")
    if frappe.db.exists(EVIDENCE, {"supersede_key": rules.supersede_key(reg.tenant_id, prev.name)}):
        raise EvidenceConflict(
            "This version is already superseded; supersede the latest version.",
            "supersedes_evidence_id",
            "already_superseded",
        )
    if prev.document_sha256 == document_sha256:
        raise EvidenceConflict(
            "The new version has the same content as the version it supersedes.",
            "file",
            "unchanged_content",
        )
    for field in ("case_id", "reference_doctype", "reference_name"):
        mine = getattr(reg, field)
        if mine is not None and (prev.get(field) or None) not in (None, mine):
            raise EvidenceConflict(
                f"{field} must match the superseded version.", field, "lineage_mismatch"
            )
    return int(prev.version) + 1, prev.lineage_id, prev


def _replay(reg: rules.Registration, digest: str) -> dict[str, Any] | None:
    row = frappe.db.get_value(
        EVIDENCE,
        {"registration_key": reg.registration_key},
        ["name", "request_digest"],
        as_dict=True,
    )
    if not row:
        return None
    if row.request_digest != digest:
        raise IdempotencyConflict(
            "This idempotency key was used for a different upload.",
            "idempotency_key",
            "idempotency_conflict",
        )
    return evidence_view(row.name, replayed=True)


# --- registration ------------------------------------------------------------------


def register_upload(
    *,
    tenant_id: str,
    content: bytes,
    filename: str,
    document_type: str,
    idempotency_key: str,
    taxpayer_id: str | None = None,
    media_type: str | None = None,
    source: str | None = None,
    case_id: str | None = None,
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    supersedes_evidence_id: str | None = None,
) -> dict[str, Any]:
    """Store uploaded bytes as a private file and register them as evidence.

    Taxpayers may omit ``taxpayer_id`` (their own is used). Officers and services
    must name it. Returns ``evidence_view`` plus ``replayed``. A replay with the
    same key and the same request stores nothing new; a different request under
    the same key raises ``IdempotencyConflict``.
    """
    scope, taxpayer = _register_scope(tenant_id, taxpayer_id)
    reg = rules.Registration.build(
        tenant_id=scope.tenant_id,
        taxpayer_id=taxpayer,
        idempotency_key=idempotency_key,
        document_type=document_type,
        source=source or rules.default_source(scope.roles),
        case_id=case_id,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        supersedes_evidence_id=supersedes_evidence_id,
    )
    rules.check_source(reg.source, scope.roles)
    try:
        checked = rules.check_document(content, filename, media_type)
    except MappingError as exc:
        _log("register_upload", "rejected", tenant_id=reg.tenant_id, reason=_reason(exc))
        raise
    return _register(reg, checked, content=bytes(content))


def register_file(
    *,
    tenant_id: str,
    file_ref: str,
    document_sha256: str,
    media_type: str,
    size_bytes: int,
    document_type: str,
    idempotency_key: str,
    taxpayer_id: str | None = None,
    source: str | None = None,
    case_id: str | None = None,
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    supersedes_evidence_id: str | None = None,
) -> dict[str, Any]:
    """Register a private File already uploaded by the caller (``RegisterDocumentRequest``).

    The file must be private, unattached and readable by the caller. Its bytes are
    re-read here; the claimed ``document_sha256`` and ``size_bytes`` must match
    what is stored, otherwise the request is rejected.
    """
    scope, taxpayer = _register_scope(tenant_id, taxpayer_id)
    reg = rules.Registration.build(
        tenant_id=scope.tenant_id,
        taxpayer_id=taxpayer,
        idempotency_key=idempotency_key,
        document_type=document_type,
        source=source or rules.default_source(scope.roles),
        case_id=case_id,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        supersedes_evidence_id=supersedes_evidence_id,
    )
    rules.check_source(reg.source, scope.roles)
    claimed = rules.validate_sha256(document_sha256)
    not_found = CaseNotFound("File not found.", "frappe_file_ref", "not_found")
    if not isinstance(file_ref, str) or not file_ref or not frappe.db.exists(FILE, file_ref):
        raise not_found
    file_doc = frappe.get_doc(FILE, file_ref)
    if not file_doc.has_permission("read"):
        raise not_found
    if (
        file_doc.is_folder
        or not file_doc.is_private
        or not str(file_doc.file_url or "").startswith("/private/files/")
    ):
        raise UploadRejected("Evidence must be a private file.", "frappe_file_ref", "not_private")
    if file_doc.attached_to_doctype or file_doc.attached_to_name:
        raise UploadRejected(
            "The file is already attached to another record.", "frappe_file_ref", "already_attached"
        )
    # Re-read exactly what is stored. A file too large to be evidence is refused
    # without reading it all.
    path = file_doc.get_full_path()
    if os.path.getsize(path) > rules.MAX_DOCUMENT_BYTES:
        raise UploadRejected("The file is larger than 25 MiB.", "file", "too_large")
    with open(path, "rb") as handle:
        content = handle.read()
    checked = rules.check_document(content, file_doc.file_name, media_type)
    if checked.document_sha256 != claimed:
        raise UploadRejected(
            "document_sha256 does not match the stored file.", "document_sha256", "hash_mismatch"
        )
    if checked.size_bytes != size_bytes:
        raise UploadRejected(
            "size_bytes does not match the stored file.", "size_bytes", "size_mismatch"
        )
    return _register(reg, checked, file_doc=file_doc)


def _reason(exc: MappingError) -> str | None:
    return exc.field_errors[0]["code"] if exc.field_errors else exc.code


def _register(
    reg: rules.Registration,
    checked: rules.CheckedDocument,
    *,
    content: bytes | None = None,
    file_doc: Any = None,
) -> dict[str, Any]:
    digest = reg.digest(checked.document_sha256, checked.media_type)
    replay = _replay(reg, digest)
    if replay is not None:
        _log(
            "register_evidence",
            "replayed",
            tenant_id=reg.tenant_id,
            evidence_id=replay["evidence_id"],
        )
        return replay
    _check_case(reg)
    _check_reference(reg)
    version, lineage, prev = _lineage(reg, checked.document_sha256)
    evidence_id = str(uuid.uuid4())
    frappe.db.savepoint("vati_register_evidence")
    try:
        if file_doc is None:
            file_doc = _store_private_file(evidence_id, checked, content or b"")
        else:
            frappe.db.set_value(
                FILE,
                file_doc.name,
                {"attached_to_doctype": EVIDENCE, "attached_to_name": evidence_id},
                update_modified=False,
            )
        doc = frappe.get_doc(
            {
                "doctype": EVIDENCE,
                "evidence_id": evidence_id,
                "tenant_id": reg.tenant_id,
                "taxpayer_id": reg.taxpayer_id,
                "case_id": reg.case_id or (prev.case_id if prev else None),
                "reference_doctype": reg.reference_doctype
                or (prev.reference_doctype if prev else None),
                "reference_name": reg.reference_name or (prev.reference_name if prev else None),
                "version": version,
                "supersedes_evidence_id": reg.supersedes_evidence_id,
                "lineage_id": lineage or evidence_id,
                "document_type": reg.document_type,
                "source": reg.source,
                "received_at": _utc(),
                "synthetic": 1,
                "document_sha256": checked.document_sha256,
                "media_type": checked.media_type,
                "size_bytes": checked.size_bytes,
                "original_filename": checked.filename,
                "file_ref": file_doc.name,
                "file_url": file_doc.file_url,
                "registration_key": reg.registration_key,
                "request_digest": digest,
            }
        )
        doc.insert(ignore_permissions=True)
    except _RACE_ERRORS:
        # A concurrent registration won a unique key: answer as its replay, or
        # as a conflict (same key, different request; or a forked lineage).
        frappe.db.rollback(save_point="vati_register_evidence")
        replay = _replay(reg, digest)
        if replay is not None:
            return replay
        raise EvidenceConflict(
            "This version is already superseded; supersede the latest version.",
            "supersedes_evidence_id",
            "already_superseded",
        ) from None
    except MappingError as exc:
        # Nothing of a refused upload stays in the transaction.
        frappe.db.rollback(save_point="vati_register_evidence")
        _log("register_evidence", "rejected", tenant_id=reg.tenant_id, reason=_reason(exc))
        raise
    if doc.case_id:
        mapping.record_link(
            tenant_id=doc.tenant_id,
            case_id=doc.case_id,
            service="frappe",
            external_type=EVIDENCE,
            external_id=doc.name,
        )
    _log(
        "register_evidence",
        "created",
        tenant_id=doc.tenant_id,
        evidence_id=doc.name,
        version=version,
    )
    return evidence_view(doc.name, replayed=False)


def _store_private_file(evidence_id: str, checked: rules.CheckedDocument, content: bytes) -> Any:
    """Write the original as a private File and prove the stored bytes are unchanged."""
    file_doc = frappe.get_doc(
        {
            "doctype": FILE,
            "file_name": f"{evidence_id}.{checked.extension}",
            "is_private": 1,
            "content": content,
            "attached_to_doctype": EVIDENCE,
            "attached_to_name": evidence_id,
        }
    )
    try:
        file_doc.insert(ignore_permissions=True)
    except _RACE_ERRORS:
        raise
    except frappe.ValidationError as exc:
        # Frappe's own file checks, e.g. a PDF that contains JavaScript.
        raise UploadRejected(
            "The file store refused this document.", "file", "unsafe_content"
        ) from exc
    except Exception as exc:
        # Frappe parses PDFs (pypdf) to look for scripts; a broken PDF is refused
        # as malformed rather than surfacing as a server error.
        if type(exc).__module__.split(".")[0] == "pypdf":
            raise UploadRejected(
                "The document could not be read.", "file", "malformed_document"
            ) from exc
        raise
    if not file_doc.is_private or not str(file_doc.file_url).startswith("/private/files/"):
        raise UploadRejected("Evidence must be stored privately.", "file", "not_private")
    with open(file_doc.get_full_path(), "rb") as handle:
        stored = handle.read()
    if rules.sha256_hex(stored) != checked.document_sha256:
        # e.g. a site setting that strips image metadata on upload. Evidence must
        # be byte-exact, so refuse instead of recording a hash of altered bytes.
        raise UploadRejected(
            "The stored file differs from the upload.", "file", "stored_bytes_changed"
        )
    return file_doc


# --- extraction revisions ----------------------------------------------------------


def record_extraction(
    *,
    tenant_id: str,
    evidence_id: str,
    document_sha256: str,
    idempotency_key: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Append one extraction revision to an evidence document.

    Only a service identity may record (``integration.callback``). The caller
    passes the SHA-256 of the bytes it extracted; it must equal the evidence's
    original hash. ``result`` is validated by ``rules.parse_extraction``. The same
    ``idempotency_key`` with the same result returns the existing revision; with a
    different result it raises ``IdempotencyConflict``.
    """
    scope = permissions.require_action(policy.INTEGRATION_CALLBACK, tenant_id)
    key = validate_idempotency_key(idempotency_key)
    evidence = _owned_evidence(scope.tenant_id, evidence_id)
    if rules.validate_sha256(document_sha256) != evidence.document_sha256:
        raise EvidenceConflict(
            "The extraction was made from different bytes than this evidence.",
            "document_sha256",
            "hash_mismatch",
        )
    parsed = rules.parse_extraction(result)
    digest = parsed.digest(evidence.name, evidence.document_sha256)
    reg_key = rules.extraction_key(scope.tenant_id, key)
    for attempt in (1, 2):
        existing = frappe.db.get_value(
            EXTRACTION, {"registration_key": reg_key}, ["name", "request_digest"], as_dict=True
        )
        if existing:
            if existing.request_digest != digest:
                raise IdempotencyConflict(
                    "This idempotency key was used for a different extraction result.",
                    "idempotency_key",
                    "idempotency_conflict",
                )
            return extraction_view(existing.name, replayed=True)
        latest = _latest_extraction(evidence.name)
        revision = (latest.revision if latest else 0) + 1
        doc = frappe.get_doc(
            {
                "doctype": EXTRACTION,
                "extraction_id": str(uuid.uuid4()),
                "tenant_id": evidence.tenant_id,
                "taxpayer_id": evidence.taxpayer_id,
                "evidence_id": evidence.name,
                "document_sha256": evidence.document_sha256,
                "revision": revision,
                "supersedes_extraction_id": latest.name if latest else None,
                "status": parsed.status,
                "error_code": parsed.error_code,
                "error_category": parsed.error_category,
                "extractor": parsed.extractor,
                "extractor_version": parsed.extractor_version,
                "page_count": parsed.page_count,
                "item_count": len(parsed.items),
                "recorded_at": _utc(),
                "registration_key": reg_key,
                "request_digest": digest,
                "revision_key": rules.revision_key(evidence.name, revision),
                "items": [_item_row(i, item) for i, item in enumerate(parsed.items)],
            }
        )
        frappe.db.savepoint("vati_record_extraction")
        try:
            doc.insert(ignore_permissions=True)
        except _RACE_ERRORS:
            frappe.db.rollback(save_point="vati_record_extraction")
            if attempt == 2:
                raise
            continue
        _log(
            "record_extraction",
            parsed.status,
            tenant_id=doc.tenant_id,
            evidence_id=doc.evidence_id,
            revision=revision,
        )
        return extraction_view(doc.name, replayed=False)
    raise AssertionError("unreachable")


def _item_row(index: int, item: rules.ProvenanceItem) -> dict[str, Any]:
    box = item.bbox
    return {
        "item_index": index,
        "label": item.label,
        "element_ref": item.element_ref,
        "page": item.page,
        "record": item.record,
        "extracted_field": item.field,
        "bbox_left": box.left if box else None,
        "bbox_top": box.top if box else None,
        "bbox_right": box.right if box else None,
        "bbox_bottom": box.bottom if box else None,
        "coord_origin": box.coord_origin if box else None,
        "text": item.text,
        "text_sha256": item.text_sha256,
    }


def _latest_extraction(evidence_id: str) -> Any:
    rows = frappe.get_all(
        EXTRACTION,
        filters={"evidence_id": evidence_id},
        fields=["name", "revision", "status", "error_code", "extractor_version"],
        order_by="revision desc",
        limit=1,
    )
    return rows[0] if rows else None


# --- views -------------------------------------------------------------------------


def evidence_view(evidence_id: str, *, replayed: bool | None = None) -> dict[str, Any]:
    """Contract-shaped ``Evidence`` (integration/api/openapi.json) plus Vati fields.

    Never includes the file path or a URL. ``case_id`` is None until the evidence
    is registered for a case; ``reference`` names the submission/request.
    """
    row = _evidence_row(evidence_id)
    latest = _latest_extraction(row.name)
    view: dict[str, Any] = {
        "evidence_id": row.name,
        "case_id": row.case_id or None,
        "tenant_id": row.tenant_id,
        "taxpayer_id": row.taxpayer_id,
        "version": int(row.version),
        "supersedes_evidence_id": row.supersedes_evidence_id or None,
        "lineage_id": row.lineage_id,
        "document_sha256": row.document_sha256,
        "media_type": row.media_type,
        "size_bytes": int(row.size_bytes),
        "document_type": row.document_type,
        "source": row.source,
        "received_at": _iso(row.received_at),
        "extraction": rules.contract_extraction(dict(latest) if latest else None),
        "service_links": [],
        "synthetic": bool(row.synthetic),
        "reference": {"doctype": row.reference_doctype, "name": row.reference_name}
        if row.reference_doctype
        else None,
    }
    if replayed is not None:
        view["replayed"] = replayed
    return view


def extraction_view(extraction_id: str, *, replayed: bool | None = None) -> dict[str, Any]:
    doc = frappe.get_doc(EXTRACTION, extraction_id)
    items = []
    for row in sorted(doc.items, key=lambda r: r.item_index):
        bbox = None
        if row.coord_origin:
            bbox = {
                "l": row.bbox_left,
                "t": row.bbox_top,
                "r": row.bbox_right,
                "b": row.bbox_bottom,
                "coord_origin": row.coord_origin,
            }
        items.append(
            {
                "label": row.label,
                "element_ref": row.element_ref or None,
                "field": row.extracted_field or None,
                "text": row.text,
                "text_sha256": row.text_sha256 or None,
                "bbox": bbox,
                # Contract EvidenceRef: cite this item in findings.
                "evidence_ref": {
                    "evidence_id": doc.evidence_id,
                    "document_sha256": doc.document_sha256,
                    "locator": {"page": row.page or None, "record": row.record or None},
                },
            }
        )
    view: dict[str, Any] = {
        "extraction_id": doc.name,
        "evidence_id": doc.evidence_id,
        "document_sha256": doc.document_sha256,
        "revision": int(doc.revision),
        "supersedes_extraction_id": doc.supersedes_extraction_id or None,
        "status": doc.status,
        "error_code": doc.error_code or None,
        "error_category": doc.error_category or None,
        "extractor": doc.extractor,
        "extractor_version": doc.extractor_version,
        "page_count": doc.page_count if doc.page_count is not None else None,
        "recorded_at": _iso(doc.recorded_at),
        "items": items,
    }
    if replayed is not None:
        view["replayed"] = replayed
    return view


# --- reads -------------------------------------------------------------------------


def get_evidence(*, tenant_id: str, evidence_id: str) -> dict[str, Any]:
    """One evidence version, permission-checked for the session user.

    Officers and services read evidence in their tenant; a taxpayer reads only
    evidence it uploaded itself (DocType ``if_owner``) for its own taxpayer.
    """
    scope = permissions.resolve_scope(tenant_id)
    row = _owned_evidence(scope.tenant_id, evidence_id)
    if not frappe.has_permission(EVIDENCE, "read", doc=row.name):
        raise EvidenceNotFound("Evidence not found.", "evidence_id", "not_found")
    return evidence_view(row.name)


def evidence_history(*, tenant_id: str, evidence_id: str) -> list[dict[str, Any]]:
    """Every version in the evidence's lineage, oldest first (officers/services)."""
    scope = _read_scope(tenant_id)
    row = _owned_evidence(scope.tenant_id, evidence_id)
    names = frappe.get_all(
        EVIDENCE,
        filters={"tenant_id": scope.tenant_id, "lineage_id": row.lineage_id},
        order_by="version asc",
        pluck="name",
    )
    return [evidence_view(name) for name in names]


def list_extractions(*, tenant_id: str, evidence_id: str) -> list[dict[str, Any]]:
    """All extraction revisions of one evidence, oldest first (officers/services)."""
    scope = _read_scope(tenant_id)
    row = _owned_evidence(scope.tenant_id, evidence_id)
    names = frappe.get_all(
        EXTRACTION, filters={"evidence_id": row.name}, order_by="revision asc", pluck="name"
    )
    return [extraction_view(name) for name in names]


def case_evidence(*, tenant_id: str, case_id: str) -> list[dict[str, Any]]:
    """Evidence registered for a case or linked to it later through a service link."""
    scope = _read_scope(tenant_id)
    case = canonical_uuid(case_id, "case_id")
    if frappe.db.get_value(CASE, case, "tenant_id") != scope.tenant_id:
        raise CaseNotFound("Case not found.", "case_id", "not_found")
    direct = frappe.get_all(
        EVIDENCE, filters={"tenant_id": scope.tenant_id, "case_id": case}, pluck="name"
    )
    linked = frappe.get_all(
        "Vati Service Link",
        filters={
            "tenant_id": scope.tenant_id,
            "case_id": case,
            "service": "frappe",
            "external_type": EVIDENCE,
        },
        pluck="external_id",
    )
    names = sorted(
        {
            n
            for n in [*direct, *linked]
            if frappe.db.get_value(EVIDENCE, n, "tenant_id") == scope.tenant_id
        }
    )
    return [evidence_view(name) for name in names]


def verify_integrity(*, tenant_id: str, evidence_id: str) -> dict[str, Any]:
    """Re-hash the stored private file and compare it with the recorded SHA-256."""
    scope = _read_scope(tenant_id)
    row = _owned_evidence(scope.tenant_id, evidence_id)
    file_doc = frappe.get_doc(FILE, row.file_ref)
    try:
        with open(file_doc.get_full_path(), "rb") as handle:
            actual = rules.sha256_hex(handle.read())
    except FileNotFoundError:
        actual = None
    ok = (
        actual == row.document_sha256
        and bool(file_doc.is_private)
        and file_doc.file_url == row.file_url
    )
    _log(
        "verify_integrity",
        "ok" if ok else "mismatch",
        tenant_id=row.tenant_id,
        evidence_id=row.name,
    )
    return {"evidence_id": row.name, "document_sha256": row.document_sha256, "intact": ok}


# --- File guards (hooks.py) --------------------------------------------------------


def _is_evidence_file(file_name: str | None, file_url: str | None) -> bool:
    if file_name and frappe.db.exists(EVIDENCE, {"file_ref": file_name}):
        return True
    return bool(file_url) and bool(frappe.db.exists(EVIDENCE, {"file_url": file_url}))


def guard_evidence_file(doc: Any, method: str | None = None) -> None:
    """``File`` before_validate hook: an evidence file stays private and in place.

    Also covers other File rows that share the same stored file (Frappe
    de-duplicates identical content), because making one of them public would
    move the shared file on disk.
    """
    if doc.is_new() or doc.is_folder:
        return
    before = doc.get_doc_before_save()
    if before is None:
        return
    if not _is_evidence_file(doc.name, before.file_url):
        return
    if (
        int(before.is_private or 0) != int(doc.is_private or 0)
        or (before.file_url or "") != (doc.file_url or "")
        or (before.attached_to_doctype or "") != (doc.attached_to_doctype or "")
        or (before.attached_to_name or "") != (doc.attached_to_name or "")
    ):
        frappe.throw("Evidence files stay private and cannot be moved.", frappe.PermissionError)


def file_has_permission(
    doc: Any, ptype: str | None = None, user: str | None = None, debug: bool = False
) -> bool:
    """``File`` has_permission hook: nobody edits or deletes an evidence original."""
    if ptype in ("write", "delete", "cancel", "submit", "amend") and _is_evidence_file(
        doc.name, doc.file_url
    ):
        return False
    return True
