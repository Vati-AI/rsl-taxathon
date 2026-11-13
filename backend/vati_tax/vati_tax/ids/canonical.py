"""Canonical case IDs and cross-service mapping rules (TAX-030).

Pure functions with no Frappe import, so the rules are testable without a bench.
``vati_tax.ids.mapping`` applies them to the ``Vati Case Identity`` and
``Vati Service Link`` DocTypes, whose unique columns enforce the same keys in the
database.

Model (see docs/decisions/ADR-006-canonical-ids-service-links.md):

- ``case_id`` is an immutable UUID shared by every component; ``display_id`` is a
  human-readable label unique per tenant.
- A service link maps a case to one upstream record. It is unique on
  (tenant_id, service, namespace, external_id). The namespace is the service
  itself, except for Frappe, whose document names are only unique per DocType.
- Some external types allow one record per case (a Jube case, an OpenAleph
  collection); others allow many (OpenAleph entities, Frappe records).
- RSL's own systems (TAX-133, ADR-043 §7) join the same register under the same rules,
  so an RSL claim or audit case reference maps to exactly one Vati case in a tenant.
  A TIN is not a link: it identifies the taxpayer, who may have many cases.
- Recording the same link again (for example a replayed event) is a replay, not
  a new row. A changed mapping under a reused event ID, an external ID already
  mapped to another case, a second record in a one-per-case slot and any
  cross-tenant reference are rejected.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Literal

CORE_SERVICES = ("jube", "frappe", "openaleph", "yente", "docling", "langgraph")
# RSL systems Vati reads from or refers to (docs/reference/RSL_LANDSCAPE.md). Vati never
# writes to them through a link; a link only records which RSL record a case is about.
RSL_SERVICES = ("online_refunds", "audit_system", "psrm", "etax", "idms", "asycuda")
SERVICES = CORE_SERVICES + RSL_SERVICES

Cardinality = Literal["one", "many"]

# External types per service, matching the names used in integration/api examples.
# "one" means at most one record of that type per case.
EXTERNAL_TYPES: dict[tuple[str, str], Cardinality] = {
    ("jube", "case"): "one",
    ("openaleph", "collection"): "one",
    ("openaleph", "entity"): "many",
    ("openaleph", "document"): "many",
    ("yente", "entity"): "many",
    ("docling", "conversion_job"): "many",
    ("docling", "document"): "many",
    ("docling", "extraction"): "many",
    ("langgraph", "investigation_run"): "many",
    # RSL (TAX-133). A Vati case is about one RSL claim or one RSL audit case; returns,
    # invoices, customs entries and PSRM records it relies on may be many. Types match
    # the record kinds of the TAX-111 RSL source contracts (integration/rsl_sources).
    ("online_refunds", "claim"): "one",
    ("audit_system", "audit_case"): "one",
    ("psrm", "objection"): "many",
    ("psrm", "payment"): "many",
    ("etax", "vat_return"): "many",
    ("etax", "wht_return"): "many",
    ("etax", "cit_return"): "many",
    ("etax", "paye_roster"): "many",
    ("idms", "invoice"): "many",
    ("asycuda", "entry"): "many",
}

# Frappe records are identified by DocType name plus document name. Domain DocTypes
# arrive in TAX-031/032, so any DocType-shaped name is accepted and may repeat.
FRAPPE_DOCTYPE = re.compile(r"^[A-Z][A-Za-z0-9 ]{0,63}$")
DISPLAY_ID = re.compile(r"^[A-Z]{2,5}-[0-9]{3,10}$")
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9:._-]{16,255}$")
EXTERNAL_ID_MAX = 255
_SEP = "\x1f"


class MappingError(Exception):
    """Structured mapping failure; ``code`` is an ErrorCode from openapi.json."""

    code = "invalid_request"
    http_status = 400
    retryable = False

    def __init__(self, message: str, field: str | None = None, reason: str | None = None):
        super().__init__(message)
        self.field_errors = [{"field": field, "code": reason or self.code}] if field else []

    def as_error(self, correlation_id: str) -> dict[str, dict[str, object]]:
        error: dict[str, object] = {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "correlation_id": correlation_id,
            "service": "vati",
        }
        if self.field_errors:
            error["field_errors"] = self.field_errors
        return {"error": error}


class InvalidMapping(MappingError):
    """Malformed tenant, case, service or external identifier."""


class CaseNotFound(MappingError):
    code = "not_found"
    http_status = 404


class TenantBoundaryError(CaseNotFound):
    """The record belongs to another tenant.

    Reported exactly like a missing case so a caller cannot probe other tenants;
    the subclass lets logs and tests distinguish it.
    """


class MappingConflict(MappingError):
    """Valid request that would break a uniqueness rule of the mapping."""

    code = "invalid_domain_input"
    http_status = 422


class IdempotencyConflict(MappingError):
    """Same event or idempotency key replayed with a different request."""

    code = "idempotency_conflict"
    http_status = 422


def canonical_uuid(value: object, field: str) -> str:
    """Return the lowercase hyphenated form of a UUID or raise ``InvalidMapping``."""
    if not isinstance(value, str) or not value.strip():
        raise InvalidMapping(f"{field} must be a UUID.", field, "invalid_uuid")
    try:
        parsed = uuid.UUID(value.strip())
    except ValueError:
        raise InvalidMapping(f"{field} must be a UUID.", field, "invalid_uuid") from None
    return str(parsed)


def optional_uuid(value: object, field: str) -> str | None:
    if value is None or value == "":
        return None
    return canonical_uuid(value, field)


def new_case_id() -> str:
    return str(uuid.uuid4())


def validate_display_id(value: object) -> str:
    if not isinstance(value, str) or not DISPLAY_ID.fullmatch(value):
        raise InvalidMapping(
            "display_id must look like ABC-000123.", "display_id", "invalid_display_id"
        )
    return value


def validate_idempotency_key(value: object) -> str:
    if not isinstance(value, str) or not IDEMPOTENCY_KEY.fullmatch(value):
        raise InvalidMapping(
            "idempotency_key must be 16-255 characters of A-Z a-z 0-9 : . _ -.",
            "idempotency_key",
            "invalid_idempotency_key",
        )
    return value


def cardinality(service: str, external_type: str) -> Cardinality:
    """Validate the (service, external_type) pair and return its cardinality."""
    if service not in SERVICES:
        raise InvalidMapping("Unknown service.", "service", "unknown_service")
    if service == "frappe":
        if not isinstance(external_type, str) or not FRAPPE_DOCTYPE.fullmatch(external_type):
            raise InvalidMapping(
                "Frappe external_type must be a DocType name.",
                "external_type",
                "unknown_external_type",
            )
        return "many"
    try:
        return EXTERNAL_TYPES[(service, external_type)]
    except KeyError:
        raise InvalidMapping(
            "Unknown external_type for this service.", "external_type", "unknown_external_type"
        ) from None


def validate_external_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > EXTERNAL_ID_MAX
        or any(ord(ch) < 32 for ch in value)
    ):
        raise InvalidMapping(
            "external_id must be 1-255 printable characters without surrounding spaces.",
            "external_id",
            "invalid_external_id",
        )
    return value


def _digest(*parts: str) -> str:
    return hashlib.sha256(_SEP.join(("v1", *parts)).encode()).hexdigest()


def namespace(service: str, external_type: str) -> str:
    """ID namespace inside a service: per DocType for Frappe, service-wide otherwise."""
    return external_type if service == "frappe" else ""


def link_key(tenant_id: str, service: str, external_type: str, external_id: str) -> str:
    return _digest("link", tenant_id, service, namespace(service, external_type), external_id)


def slot_key(tenant_id: str, case_id: str, service: str, external_type: str) -> str | None:
    """Key for one-per-case external types; ``None`` when many are allowed."""
    if cardinality(service, external_type) != "one":
        return None
    return _digest("slot", tenant_id, case_id, service, external_type)


def event_key(tenant_id: str, source_event_id: str | None) -> str | None:
    return _digest("event", tenant_id, source_event_id) if source_event_id else None


def display_key(tenant_id: str, display_id: str) -> str:
    return _digest("display", tenant_id, display_id)


def registration_key(tenant_id: str, idempotency_key: str | None) -> str | None:
    return _digest("register", tenant_id, idempotency_key) if idempotency_key else None


@dataclass(frozen=True)
class Link:
    """A requested or stored service link (canonical values)."""

    tenant_id: str
    case_id: str
    service: str
    external_type: str
    external_id: str
    source_event_id: str | None = None

    @classmethod
    def build(
        cls,
        tenant_id: object,
        case_id: object,
        service: object,
        external_type: object,
        external_id: object,
        source_event_id: object = None,
    ) -> Link:
        tenant = canonical_uuid(tenant_id, "tenant_id")
        case = canonical_uuid(case_id, "case_id")
        if not isinstance(service, str) or not isinstance(external_type, str):
            raise InvalidMapping("service and external_type are required.", "service")
        cardinality(service, external_type)
        return cls(
            tenant,
            case,
            service,
            external_type,
            validate_external_id(external_id),
            optional_uuid(source_event_id, "source_event_id"),
        )

    @property
    def link_key(self) -> str:
        return link_key(self.tenant_id, self.service, self.external_type, self.external_id)

    @property
    def slot_key(self) -> str | None:
        return slot_key(self.tenant_id, self.case_id, self.service, self.external_type)

    @property
    def event_key(self) -> str | None:
        return event_key(self.tenant_id, self.source_event_id)

    def same_mapping(self, other: Link) -> bool:
        return (
            self.tenant_id == other.tenant_id
            and self.case_id == other.case_id
            and self.service == other.service
            and self.external_type == other.external_type
            and self.external_id == other.external_id
        )


def decide_link(
    request: Link,
    case_tenant_id: str | None,
    by_link_key: Link | None,
    by_slot_key: Link | None,
    by_event_key: Link | None,
) -> Literal["create", "replay"]:
    """Decide how to apply ``request`` given the rows that share its unique keys.

    ``case_tenant_id`` is the tenant of the referenced case, or ``None`` when the
    case does not exist. Raises a ``MappingError`` when the request must be rejected.
    """
    if case_tenant_id is None:
        raise CaseNotFound("Case not found.", "case_id", "not_found")
    if case_tenant_id != request.tenant_id:
        raise TenantBoundaryError("Case not found.", "case_id", "not_found")
    if by_event_key is not None:
        if not request.same_mapping(by_event_key):
            raise IdempotencyConflict(
                "This event was already applied with a different mapping.",
                "source_event_id",
                "event_reused",
            )
        return "replay"
    if by_link_key is not None:
        if not request.same_mapping(by_link_key):
            raise MappingConflict(
                "This external record is already mapped to another case or type.",
                "external_id",
                "already_mapped",
            )
        return "replay"
    if by_slot_key is not None:
        raise MappingConflict(
            "This case already has a record of this type in the service.",
            "external_type",
            "slot_taken",
        )
    return "create"


@dataclass(frozen=True)
class CaseRegistration:
    """Request to register (or replay registration of) a canonical case."""

    tenant_id: str
    idempotency_key: str
    case_id: str | None = None
    display_id: str | None = None
    taxpayer_id: str | None = None
    submission_id: str | None = None

    @classmethod
    def build(
        cls,
        tenant_id: object,
        idempotency_key: object,
        case_id: object = None,
        display_id: object = None,
        taxpayer_id: object = None,
        submission_id: object = None,
    ) -> CaseRegistration:
        return cls(
            canonical_uuid(tenant_id, "tenant_id"),
            validate_idempotency_key(idempotency_key),
            optional_uuid(case_id, "case_id"),
            None if display_id in (None, "") else validate_display_id(display_id),
            optional_uuid(taxpayer_id, "taxpayer_id"),
            optional_uuid(submission_id, "submission_id"),
        )

    def matches(self, stored: StoredCase) -> bool:
        """True when ``stored`` is what this request created (generated fields aside)."""
        return (
            stored.tenant_id == self.tenant_id
            and (self.case_id is None or stored.case_id == self.case_id)
            and (self.display_id is None or stored.display_id == self.display_id)
            and stored.taxpayer_id == self.taxpayer_id
            and stored.submission_id == self.submission_id
        )


@dataclass(frozen=True)
class StoredCase:
    tenant_id: str
    case_id: str
    display_id: str
    taxpayer_id: str | None
    submission_id: str | None
    registration_key: str | None = None


def decide_case(
    request: CaseRegistration,
    by_registration_key: StoredCase | None,
    by_case_id: StoredCase | None,
    by_display_key: StoredCase | None,
) -> tuple[Literal["create", "replay"], StoredCase | None]:
    """Decide how to apply a registration; returns the replayed case when replaying."""
    if by_case_id is not None and by_case_id.tenant_id != request.tenant_id:
        # Never reveal that the UUID is used by another tenant.
        raise TenantBoundaryError("case_id cannot be registered.", "case_id", "not_found")
    if by_registration_key is not None:
        if not request.matches(by_registration_key):
            raise IdempotencyConflict(
                "idempotency_key was already used for a different case registration.",
                "idempotency_key",
                "key_reused",
            )
        return "replay", by_registration_key
    if by_case_id is not None:
        raise MappingConflict(
            "case_id is already registered under another idempotency key.",
            "case_id",
            "already_registered",
        )
    if by_display_key is not None:
        raise MappingConflict(
            "display_id is already used in this tenant.", "display_id", "already_used"
        )
    return "create", None
