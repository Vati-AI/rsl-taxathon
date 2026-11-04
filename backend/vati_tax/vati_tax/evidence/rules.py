"""Evidence, extraction and provenance rules (TAX-032).

Pure functions with no Frappe import, so the rules are testable without a bench.
``vati_tax.evidence.store`` applies them to the ``Vati Evidence`` and
``Vati Evidence Extraction`` DocTypes. See
docs/decisions/ADR-010-evidence-storage-provenance.md.

Model:

- Evidence is one immutable original document. Its SHA-256 is computed here from
  the stored bytes, never taken from the caller. A correction is a new evidence
  version that points at the version it supersedes; the old one never changes.
- Uploads are limited to the media types and size of ``RegisterDocumentRequest``
  in integration/api/openapi.json. The declared media type, the file extension
  and the leading bytes must agree.
- Extraction results are append-only revisions of one evidence. Each revision
  records the SHA-256 of the evidence it read, a status of ``success``,
  ``partial`` or ``failure`` and page/bounding-box provenance per item. A failed
  extraction carries an error code and no items, so it is never mistaken for an
  empty document.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import zipfile
from dataclasses import dataclass
from typing import Any, Final, Literal, cast

from vati_tax.ids.canonical import (
    InvalidMapping,
    MappingError,
    canonical_uuid,
    optional_uuid,
    validate_idempotency_key,
)

# --- documents ---------------------------------------------------------------------

# Same bound as RegisterDocumentRequest.size_bytes (25 MiB) and Docling's limit.
MAX_DOCUMENT_BYTES: Final = 26_214_400

PDF: Final = "application/pdf"
PNG: Final = "image/png"
JPEG: Final = "image/jpeg"
CSV: Final = "text/csv"
XLSX: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Media type -> allowed file extensions (lower case, without dot).
MEDIA_TYPES: Final[dict[str, tuple[str, ...]]] = {
    PDF: ("pdf",),
    PNG: ("png",),
    JPEG: ("jpg", "jpeg"),
    CSV: ("csv",),
    XLSX: ("xlsx",),
}
DOCUMENT_TYPES: Final = (
    "invoice",
    "contract",
    "payslip",
    "import_declaration",
    "bank_statement",
    "financial_statement",
    "other",
)
SOURCES: Final = ("taxpayer_portal", "officer_upload", "synthetic_fixture")
# Which Vati roles may claim each source, so a provenance label cannot be spoofed:
# only a taxpayer uploads through the portal, only officers record an officer
# upload, and synthetic fixtures are loaded by officers or a service identity.
SOURCE_ROLES: Final[dict[str, frozenset[str]]] = {
    "taxpayer_portal": frozenset({"taxpayer"}),
    "officer_upload": frozenset({"auditor", "specialist", "supervisor"}),
    "synthetic_fixture": frozenset({"auditor", "specialist", "supervisor", "service"}),
}
DEFAULT_SOURCE: Final[dict[str, str]] = {
    "taxpayer": "taxpayer_portal",
    "auditor": "officer_upload",
    "specialist": "officer_upload",
    "supervisor": "officer_upload",
    "service": "synthetic_fixture",
}

SHA256_RE: Final = re.compile(r"^[a-f0-9]{64}$")
FILENAME_MAX: Final = 140
XLSX_MAX_ENTRIES: Final = 10_000
_SEP: Final = "\x1f"


class UploadRejected(MappingError):
    """A well-formed request whose document is not acceptable evidence."""

    code = "invalid_domain_input"
    http_status = 422


class EvidenceConflict(MappingError):
    """Valid request that would break an append-only or uniqueness rule."""

    code = "invalid_domain_input"
    http_status = 422


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def key(*parts: str) -> str:
    """Opaque unique-column key over the given parts (same scheme as TAX-030)."""
    return hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()


def validate_sha256(value: object, field: str = "document_sha256") -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise InvalidMapping(
            f"{field} must be 64 lower-case hex characters.", field, "invalid_sha256"
        )
    return value


def choice(value: object, allowed: tuple[str, ...], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise InvalidMapping(f"{field} is not an allowed value.", field, "invalid_choice")
    return value


def safe_filename(value: object) -> tuple[str, str]:
    """Return (display name, lower-case extension) for an uploaded file name.

    Only the final path component is kept; control characters and names without
    an extension are rejected. The stored file is renamed to the evidence ID, so
    this name is shown to humans only.
    """
    if not isinstance(value, str) or not value.strip():
        raise UploadRejected("A file name is required.", "file", "missing_filename")
    name = re.split(r"[\\/]", value.strip())[-1]
    if not name or name in {".", ".."} or any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise UploadRejected("The file name is not acceptable.", "file", "invalid_filename")
    if "." not in name.strip("."):
        raise UploadRejected("The file name needs an extension.", "file", "disallowed_type")
    extension = name.rsplit(".", 1)[1].lower()
    if len(name) > FILENAME_MAX:
        name = name[: FILENAME_MAX - len(extension) - 1] + "." + extension
    return name, extension


def detect_media_type(content: bytes) -> str | None:
    """Media type implied by the leading bytes, or None if not an allowed type."""
    if content.startswith(b"%PDF-"):
        return PDF
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if content.startswith(b"\xff\xd8\xff"):
        return JPEG
    if content.startswith(b"PK\x03\x04"):
        return XLSX if _is_xlsx(content) else None
    if _is_csv_text(content):
        return CSV
    return None


def _is_xlsx(content: bytes) -> bool:
    """An OOXML workbook: a ZIP with a content-types part and a workbook part.

    Only the central directory is read; nothing is decompressed.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, ValueError, OSError):
        return False
    if len(names) > XLSX_MAX_ENTRIES:
        return False
    return "[Content_Types].xml" in names and "xl/workbook.xml" in names


def _is_csv_text(content: bytes) -> bool:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    return not any(ord(ch) < 32 and ch not in "\t\r\n" for ch in text)


@dataclass(frozen=True)
class CheckedDocument:
    """An accepted original document and its server-computed identity."""

    filename: str
    extension: str
    media_type: str
    size_bytes: int
    document_sha256: str


def check_document(
    content: object, filename: object, declared_media_type: object
) -> CheckedDocument:
    """Accept or reject an original document before anything is stored.

    Raises ``UploadRejected`` with a field reason: ``empty_document``,
    ``too_large``, ``disallowed_type``, ``extension_mismatch`` or
    ``media_type_mismatch``.
    """
    if not isinstance(content, bytes | bytearray):
        raise InvalidMapping("File content must be bytes.", "file", "invalid_content")
    content = bytes(content)
    size = len(content)
    if size == 0:
        raise UploadRejected("The file is empty.", "file", "empty_document")
    if size > MAX_DOCUMENT_BYTES:
        raise UploadRejected("The file is larger than 25 MiB.", "file", "too_large")
    name, extension = safe_filename(filename)
    if declared_media_type is not None and declared_media_type not in MEDIA_TYPES:
        raise UploadRejected("This document type is not allowed.", "media_type", "disallowed_type")
    if not any(extension in exts for exts in MEDIA_TYPES.values()):
        raise UploadRejected("This document type is not allowed.", "file", "disallowed_type")
    detected = detect_media_type(content)
    if detected is None:
        raise UploadRejected("This document type is not allowed.", "file", "disallowed_type")
    if extension not in MEDIA_TYPES[detected]:
        raise UploadRejected(
            "The file extension does not match its content.", "file", "extension_mismatch"
        )
    if declared_media_type is not None and declared_media_type != detected:
        raise UploadRejected(
            "The declared media type does not match the file content.",
            "media_type",
            "media_type_mismatch",
        )
    return CheckedDocument(name, extension, detected, size, sha256_hex(content))


@dataclass(frozen=True)
class Registration:
    """Normalised evidence registration request (everything except the bytes)."""

    tenant_id: str
    taxpayer_id: str
    idempotency_key: str
    document_type: str
    source: str
    case_id: str | None
    reference_doctype: str | None
    reference_name: str | None
    supersedes_evidence_id: str | None

    @classmethod
    def build(
        cls,
        *,
        tenant_id: object,
        taxpayer_id: object,
        idempotency_key: object,
        document_type: object,
        source: object,
        case_id: object = None,
        reference_doctype: object = None,
        reference_name: object = None,
        supersedes_evidence_id: object = None,
    ) -> Registration:
        ref_doctype = reference_doctype or None
        ref_name = reference_name or None
        if (ref_doctype is None) != (ref_name is None):
            raise InvalidMapping(
                "reference_doctype and reference_name go together.",
                "reference_name",
                "incomplete_reference",
            )
        if ref_doctype is not None and (
            not isinstance(ref_doctype, str)
            or not re.fullmatch(r"[A-Z][A-Za-z0-9 ]{0,63}", ref_doctype)
        ):
            raise InvalidMapping("reference_doctype must be a DocType name.", "reference_doctype")
        if ref_name is not None and (
            not isinstance(ref_name, str)
            or not 0 < len(ref_name) <= 140
            or ref_name != ref_name.strip()
        ):
            raise InvalidMapping("reference_name is not a document name.", "reference_name")
        return cls(
            tenant_id=canonical_uuid(tenant_id, "tenant_id"),
            taxpayer_id=canonical_uuid(taxpayer_id, "taxpayer_id"),
            idempotency_key=validate_idempotency_key(idempotency_key),
            document_type=choice(document_type, DOCUMENT_TYPES, "document_type"),
            source=choice(source, SOURCES, "source"),
            case_id=optional_uuid(case_id, "case_id"),
            reference_doctype=ref_doctype,
            reference_name=ref_name,
            supersedes_evidence_id=optional_uuid(supersedes_evidence_id, "supersedes_evidence_id"),
        )

    @property
    def registration_key(self) -> str:
        return key(self.tenant_id, "evidence", self.idempotency_key)

    def digest(self, document_sha256: str, media_type: str) -> str:
        """Request digest; a replayed key with a different digest is a conflict."""
        return key(
            self.tenant_id,
            self.taxpayer_id,
            document_sha256,
            media_type,
            self.document_type,
            self.source,
            self.case_id or "",
            self.reference_doctype or "",
            self.reference_name or "",
            self.supersedes_evidence_id or "",
        )


def check_source(source: str, roles: frozenset[str] | set[str]) -> None:
    if not set(roles) & SOURCE_ROLES[source]:
        raise UploadRejected(
            "Your role cannot register evidence from this source.", "source", "source_not_allowed"
        )


def default_source(roles: frozenset[str] | set[str]) -> str:
    """Source for a caller that did not name one (a single-role scope)."""
    for role in ("taxpayer", "auditor", "specialist", "supervisor", "service"):
        if role in roles:
            return DEFAULT_SOURCE[role]
    raise UploadRejected("No evidence source for this role.", "source", "source_not_allowed")


def supersede_key(tenant_id: str, supersedes_evidence_id: str) -> str:
    """Unique per superseded version, so a lineage never forks."""
    return key(tenant_id, "supersedes", supersedes_evidence_id)


# --- extraction revisions ----------------------------------------------------------

ExtractionStatus = Literal["success", "partial", "failure"]
EXTRACTION_STATUSES: Final[tuple[ExtractionStatus, ...]] = ("success", "partial", "failure")
EXTRACTORS: Final = ("docling", "synthetic_fixture")
COORD_ORIGINS: Final = ("TOPLEFT", "BOTTOMLEFT")
MAX_ITEMS: Final = 5_000
MAX_ITEM_TEXT: Final = 20_000
MAX_TOTAL_TEXT: Final = 2_000_000
MAX_PAGES: Final = 1_000
CODE_RE: Final = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
LABEL_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
VERSION_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
REF_MAX: Final = 128
RECORD_MAX: Final = 255
FIELD_RE: Final = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")


@dataclass(frozen=True)
class BBox:
    left: float
    top: float
    right: float
    bottom: float
    coord_origin: str


@dataclass(frozen=True)
class ProvenanceItem:
    """One extracted element and where it came from in the original document."""

    element_ref: str | None
    label: str
    page: int | None
    bbox: BBox | None
    record: str | None
    field: str | None
    text: str | None
    text_sha256: str | None

    def locator(self) -> dict[str, Any]:
        """Contract ``EvidenceLocator``."""
        return {"page": self.page, "record": self.record}


@dataclass(frozen=True)
class ExtractionResult:
    status: ExtractionStatus
    extractor: str
    extractor_version: str
    error_code: str | None
    error_category: str | None
    page_count: int | None
    items: tuple[ProvenanceItem, ...]

    def digest(self, evidence_id: str, document_sha256: str) -> str:
        """Digest of the normalised result; replays must match it exactly."""
        body = {
            "evidence_id": evidence_id,
            "document_sha256": document_sha256,
            "status": self.status,
            "extractor": self.extractor,
            "extractor_version": self.extractor_version,
            "error_code": self.error_code,
            "error_category": self.error_category,
            "page_count": self.page_count,
            "items": [
                {
                    "element_ref": i.element_ref,
                    "label": i.label,
                    "page": i.page,
                    "bbox": None
                    if i.bbox is None
                    else [
                        i.bbox.left,
                        i.bbox.top,
                        i.bbox.right,
                        i.bbox.bottom,
                        i.bbox.coord_origin,
                    ],
                    "record": i.record,
                    "field": i.field,
                    "text_sha256": i.text_sha256,
                }
                for i in self.items
            ],
        }
        return sha256_hex(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())


def _invalid(message: str, field: str, reason: str = "invalid_extraction") -> InvalidMapping:
    return InvalidMapping(message, field, reason)


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise _invalid("Bounding-box values must be finite numbers.", field, "invalid_bbox")
    if value < 0:
        raise _invalid("Bounding-box values cannot be negative.", field, "invalid_bbox")
    return float(value)


def _bbox(value: object, field: str) -> BBox | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _invalid("bbox must be an object.", field, "invalid_bbox")
    box = BBox(
        _number(value.get("l"), field + ".l"),
        _number(value.get("t"), field + ".t"),
        _number(value.get("r"), field + ".r"),
        _number(value.get("b"), field + ".b"),
        choice(value.get("coord_origin", "BOTTOMLEFT"), COORD_ORIGINS, field + ".coord_origin"),
    )
    if box.left > box.right:
        raise _invalid("bbox left exceeds right.", field, "invalid_bbox")
    # Docling's default origin is bottom-left, where top >= bottom.
    if box.coord_origin == "BOTTOMLEFT" and box.top < box.bottom:
        raise _invalid("bbox top is below bottom.", field, "invalid_bbox")
    if box.coord_origin == "TOPLEFT" and box.top > box.bottom:
        raise _invalid("bbox top is below bottom.", field, "invalid_bbox")
    return box


def _optional_text(value: object, field: str, limit: int) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > limit or value != value.strip():
        raise _invalid(f"{field} must be at most {limit} characters.", field)
    if any(ord(ch) < 32 for ch in value):
        raise _invalid(f"{field} has control characters.", field)
    return value


def _item(raw: object, index: int, page_count: int | None) -> ProvenanceItem:
    field = f"items[{index}]"
    if not isinstance(raw, dict):
        raise _invalid("Each item must be an object.", field)
    label = raw.get("label")
    if not isinstance(label, str) or not LABEL_RE.fullmatch(label):
        raise _invalid("label must be a short lower-case name.", field + ".label")
    page = raw.get("page")
    if page is not None:
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise _invalid(
                "page must be an integer of at least 1.", field + ".page", "invalid_page"
            )
        if page_count is not None and page > page_count:
            raise _invalid("page is beyond page_count.", field + ".page", "invalid_page")
    record = _optional_text(raw.get("record"), field + ".record", RECORD_MAX)
    if page is None and record is None:
        # EvidenceLocator: an item without a page or record cannot be cited.
        raise _invalid("Each item needs a page or a record locator.", field, "missing_locator")
    bbox = _bbox(raw.get("bbox"), field + ".bbox")
    if bbox is not None and page is None:
        raise _invalid("A bounding box needs a page.", field + ".bbox", "invalid_bbox")
    text = raw.get("text")
    if text is not None and (not isinstance(text, str) or len(text) > MAX_ITEM_TEXT):
        raise _invalid(f"text must be at most {MAX_ITEM_TEXT} characters.", field + ".text")
    item_field = raw.get("field")
    if item_field is not None and (
        not isinstance(item_field, str) or not FIELD_RE.fullmatch(item_field)
    ):
        raise _invalid("field must be a short lower-case name.", field + ".field")
    return ProvenanceItem(
        element_ref=_optional_text(raw.get("element_ref"), field + ".element_ref", REF_MAX),
        label=label,
        page=page,
        bbox=bbox,
        record=record,
        field=item_field,
        text=text,
        text_sha256=None if text is None else sha256_hex(text.encode("utf-8")),
    )


def parse_extraction(payload: object) -> ExtractionResult:
    """Validate an extraction result before it is recorded.

    ``payload`` keys: ``status`` (success/partial/failure), ``extractor``,
    ``extractor_version``, ``error_code`` (required unless success, forbidden on
    success), optional ``error_category``, optional ``page_count`` and ``items``.
    A ``failure`` must not carry items.
    """
    if not isinstance(payload, dict):
        raise _invalid("Extraction payload must be an object.", "payload")
    status = cast(ExtractionStatus, choice(payload.get("status"), EXTRACTION_STATUSES, "status"))
    extractor = choice(payload.get("extractor"), EXTRACTORS, "extractor")
    version = payload.get("extractor_version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise _invalid("extractor_version is required (max 64 characters).", "extractor_version")
    error_code = payload.get("error_code") or None
    if error_code is not None and (
        not isinstance(error_code, str) or not CODE_RE.fullmatch(error_code)
    ):
        raise _invalid("error_code must be a short lower-case code.", "error_code")
    if status == "success" and error_code is not None:
        raise _invalid(
            "A successful extraction has no error_code.", "error_code", "status_mismatch"
        )
    if status != "success" and error_code is None:
        # Never let a failed or partial extraction look like a clean empty result.
        raise _invalid(
            "A partial or failed extraction needs an error_code.", "error_code", "status_mismatch"
        )
    category = payload.get("error_category") or None
    if category is not None and (not isinstance(category, str) or not LABEL_RE.fullmatch(category)):
        raise _invalid("error_category must be a short lower-case name.", "error_category")
    page_count = payload.get("page_count")
    if page_count is not None and (
        isinstance(page_count, bool)
        or not isinstance(page_count, int)
        or not 0 <= page_count <= MAX_PAGES
    ):
        raise _invalid(f"page_count must be 0-{MAX_PAGES}.", "page_count")
    raw_items = payload.get("items") or []
    if not isinstance(raw_items, list):
        raise _invalid("items must be a list.", "items")
    if len(raw_items) > MAX_ITEMS:
        raise _invalid(f"At most {MAX_ITEMS} items per revision.", "items")
    if status == "failure" and raw_items:
        raise _invalid("A failed extraction carries no items.", "items", "status_mismatch")
    items = tuple(_item(raw, i, page_count) for i, raw in enumerate(raw_items))
    if sum(len(i.text or "") for i in items) > MAX_TOTAL_TEXT:
        raise _invalid("Extracted text is too large for one revision.", "items")
    return ExtractionResult(status, extractor, version, error_code, category, page_count, items)


def extraction_key(tenant_id: str, idempotency_key: str) -> str:
    return key(tenant_id, "extraction", idempotency_key)


def revision_key(evidence_id: str, revision: int) -> str:
    return key(evidence_id, "revision", str(revision))


# Contract Evidence.extraction.status: pending | running | completed | failed.
CONTRACT_EXTRACTION_STATUS: Final[dict[str, str]] = {
    "success": "completed",
    "partial": "completed",
    "failure": "failed",
}
PARTIAL_ERROR_CODE: Final = "partial_extraction"


def contract_extraction(latest: dict[str, Any] | None) -> dict[str, Any]:
    """``Evidence.extraction`` from the latest revision (None means not yet run).

    A partial result is ``completed`` but keeps an error code, so consumers can
    tell it from a full result.
    """
    if latest is None:
        return {"status": "pending", "extractor_version": None, "error_code": None}
    status = latest["status"]
    error_code = latest.get("error_code") or None
    if status == "partial" and error_code is None:
        error_code = PARTIAL_ERROR_CODE
    return {
        "status": CONTRACT_EXTRACTION_STATUS[status],
        "extractor_version": latest.get("extractor_version") or None,
        "error_code": error_code,
    }


def evidence_ref(evidence_id: str, document_sha256: str, item: ProvenanceItem) -> dict[str, Any]:
    """Contract ``EvidenceRef`` citing one extracted item."""
    return {
        "evidence_id": evidence_id,
        "document_sha256": document_sha256,
        "locator": item.locator(),
    }
