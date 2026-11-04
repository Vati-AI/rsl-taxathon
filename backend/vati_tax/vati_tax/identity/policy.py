"""Tenant, taxpayer and decision-action policy (TAX-029).

Pure functions with no Frappe import, so the rules are testable without a bench.
``vati_tax.identity.permissions`` applies them to Frappe sessions, DocType
queries/documents and whitelisted methods.

Model:

- A principal is a Frappe user plus its enabled tenant memberships. Each
  membership grants exactly one Vati role inside one tenant. Taxpayer
  memberships also bind the user to one ``taxpayer_id``.
- Tenant-scoped records carry ``tenant_id``; taxpayer-owned records also carry
  ``taxpayer_id``. A taxpayer only reaches records of its own taxpayer in its
  own tenant. Staff and service principals reach records inside their member
  tenants only. Nothing is reachable without a membership (fail closed).
- Decision actions are human-only: a service identity can never perform them,
  and nobody approves a decision they proposed.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Final, Literal

Role = Literal["taxpayer", "auditor", "specialist", "supervisor", "service"]
Operation = Literal["read", "write", "create", "delete"]

TAXPAYER: Final = "taxpayer"
AUDITOR: Final = "auditor"
SPECIALIST: Final = "specialist"
SUPERVISOR: Final = "supervisor"
SERVICE: Final = "service"

ROLES: Final[tuple[Role, ...]] = (TAXPAYER, AUDITOR, SPECIALIST, SUPERVISOR, SERVICE)
STAFF_ROLES: Final[frozenset[Role]] = frozenset({AUDITOR, SPECIALIST, SUPERVISOR})

# Frappe Role records that carry DocType permissions for each Vati role.
FRAPPE_ROLES: Final[dict[Role, str]] = {
    TAXPAYER: "Vati Taxpayer",
    AUDITOR: "Vati Auditor",
    SPECIALIST: "Vati Specialist",
    SUPERVISOR: "Vati Supervisor",
    SERVICE: "Vati Service",
}
# Only staff use Desk; taxpayers use the portal and services use the API.
DESK_ACCESS: Final[dict[Role, bool]] = {role: role in STAFF_ROLES for role in ROLES}

# Actions. Names are stable identifiers used by callers (TAX-031/046 and the
# Vati API); the matrix below is the single source of who may do what.
SUBMISSION_CREATE: Final = "submission.create"
SUBMISSION_READ: Final = "submission.read"
EVIDENCE_RESPOND: Final = "evidence.respond"
CASE_READ: Final = "case.read"
EVIDENCE_READ: Final = "evidence.read"
INVESTIGATION_REQUEST: Final = "investigation.request"
FINDING_REVIEW: Final = "finding.review"
EVIDENCE_REQUEST_AUTHORISE: Final = "evidence_request.authorise"
SPECIALIST_REFER: Final = "specialist.refer"
SPECIALIST_OPINION_RECORD: Final = "specialist.opinion.record"
DECISION_PROPOSE: Final = "decision.propose"
DECISION_APPROVE: Final = "decision.approve"
CASE_ASSIGN: Final = "case.assign"
PLAYBOOK_APPROVE: Final = "playbook.approve"
INTEGRATION_CALLBACK: Final = "integration.callback"
DRAFT_RECORD: Final = "draft.record"

ACTION_ROLES: Final[dict[str, frozenset[Role]]] = {
    SUBMISSION_CREATE: frozenset({TAXPAYER}),
    SUBMISSION_READ: frozenset({TAXPAYER, AUDITOR, SPECIALIST, SUPERVISOR, SERVICE}),
    EVIDENCE_RESPOND: frozenset({TAXPAYER}),
    CASE_READ: frozenset({AUDITOR, SPECIALIST, SUPERVISOR, SERVICE}),
    EVIDENCE_READ: frozenset({AUDITOR, SPECIALIST, SUPERVISOR, SERVICE}),
    INVESTIGATION_REQUEST: frozenset({AUDITOR, SPECIALIST, SUPERVISOR}),
    FINDING_REVIEW: frozenset({AUDITOR, SPECIALIST, SUPERVISOR}),
    EVIDENCE_REQUEST_AUTHORISE: frozenset({AUDITOR, SUPERVISOR}),
    SPECIALIST_REFER: frozenset({AUDITOR, SUPERVISOR}),
    SPECIALIST_OPINION_RECORD: frozenset({SPECIALIST}),
    DECISION_PROPOSE: frozenset({AUDITOR, SPECIALIST, SUPERVISOR}),
    DECISION_APPROVE: frozenset({SUPERVISOR}),
    CASE_ASSIGN: frozenset({SUPERVISOR}),
    PLAYBOOK_APPROVE: frozenset({SUPERVISOR}),
    # Service identities deliver integration results (extraction, matching,
    # risk callbacks) and record agent drafts that a human must then review.
    INTEGRATION_CALLBACK: frozenset({SERVICE}),
    DRAFT_RECORD: frozenset({AUDITOR, SPECIALIST, SUPERVISOR, SERVICE}),
}

# Consequential human decisions. Never granted to a service identity, even if
# the matrix above is edited by mistake (checked independently below).
HUMAN_ONLY_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        EVIDENCE_REQUEST_AUTHORISE,
        SPECIALIST_REFER,
        SPECIALIST_OPINION_RECORD,
        DECISION_PROPOSE,
        DECISION_APPROVE,
        CASE_ASSIGN,
        PLAYBOOK_APPROVE,
    }
)
# Actions on a taxpayer's own records; taxpayers need a matching taxpayer_id.
TAXPAYER_OWNED_ACTIONS: Final[frozenset[str]] = frozenset(
    {SUBMISSION_CREATE, SUBMISSION_READ, EVIDENCE_RESPOND}
)
# The approver must differ from the proposer (separation of duties).
SEPARATION_OF_DUTIES: Final[frozenset[str]] = frozenset({DECISION_APPROVE})


class PolicyError(Exception):
    """Base for policy refusals; ``code`` follows docs/API_STANDARD.md."""

    code = "forbidden"


class MissingTenantContext(PolicyError):
    code = "missing_tenant_context"


class TenantScopeDenied(PolicyError):
    code = "tenant_scope_denied"


class ActionDenied(PolicyError):
    code = "forbidden"


class InvalidMembership(ValueError):
    """A membership row violates the identity model."""


def canonical_uuid(value: object) -> str | None:
    """Lower-case hyphenated form of a hyphenated UUID (any case), else None."""
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return None
    text = str(parsed)
    return text if text == value.lower() else None


def is_stored_uuid(value: object) -> bool:
    """Stored identifiers must already be canonical, so equality checks hold."""
    return value is not None and canonical_uuid(value) == value


@dataclass(frozen=True)
class Membership:
    tenant_id: str
    role: Role
    taxpayer_id: str | None = None


@dataclass(frozen=True)
class Principal:
    user: str
    memberships: tuple[Membership, ...] = field(default_factory=tuple)

    def tenant_ids(self) -> frozenset[str]:
        return frozenset(m.tenant_id for m in self.memberships)

    def in_tenant(self, tenant_id: str) -> tuple[Membership, ...]:
        return tuple(m for m in self.memberships if m.tenant_id == tenant_id)

    def has_role(self, role: Role) -> bool:
        return any(m.role == role for m in self.memberships)


@dataclass(frozen=True)
class TenantScope:
    """The principal's memberships inside one selected tenant."""

    user: str
    tenant_id: str
    roles: frozenset[Role]
    taxpayer_ids: frozenset[str]


def validate_membership(membership: Membership) -> Membership:
    """Reject malformed memberships before they can grant access."""
    if membership.role not in ROLES:
        raise InvalidMembership(f"Unknown Vati role: {membership.role!r}")
    if not is_stored_uuid(membership.tenant_id):
        raise InvalidMembership("tenant_id must be a lower-case UUID")
    if membership.role == TAXPAYER:
        if not is_stored_uuid(membership.taxpayer_id):
            raise InvalidMembership("A taxpayer membership needs a lower-case UUID taxpayer_id")
    elif membership.taxpayer_id:
        raise InvalidMembership("Only taxpayer memberships carry a taxpayer_id")
    return membership


def validate_user_memberships(memberships: Iterable[Membership]) -> None:
    """Rules across one user's memberships.

    A taxpayer identity is never also staff or a service, and a service identity
    is never also a human role: mixing them would let one login act on both
    sides of a case. One membership per tenant keeps the role unambiguous.
    """
    items = [validate_membership(m) for m in memberships]
    tenants = [m.tenant_id for m in items]
    if len(tenants) != len(set(tenants)):
        raise InvalidMembership("A user has at most one membership per tenant")
    kinds = {
        "taxpayer" if m.role == TAXPAYER else "service" if m.role == SERVICE else "staff"
        for m in items
    }
    if len(kinds) > 1:
        raise InvalidMembership(
            "Taxpayer, staff and service identities must be separate users: "
            + ", ".join(sorted(kinds))
        )


def resolve_tenant(principal: Principal, requested_tenant_id: str | None) -> TenantScope:
    """Select one tenant for a request (ADR-003 ``X-Vati-Tenant-ID`` semantics).

    The header only selects; it never grants. It is matched case-insensitively
    against the stored lower-case IDs. Missing/malformed -> 400
    ``missing_tenant_context``; outside the principal's memberships -> 403
    ``tenant_scope_denied``.
    """
    tenant_id = canonical_uuid(requested_tenant_id)
    if tenant_id is None:
        raise MissingTenantContext("A valid X-Vati-Tenant-ID is required")
    memberships = principal.in_tenant(tenant_id)
    if not memberships:
        raise TenantScopeDenied("The selected tenant is outside your permitted scope")
    return TenantScope(
        user=principal.user,
        tenant_id=tenant_id,
        roles=frozenset(m.role for m in memberships),
        taxpayer_ids=frozenset(m.taxpayer_id for m in memberships if m.taxpayer_id),
    )


def can_access_record(
    principal: Principal,
    record_tenant_id: str | None,
    record_taxpayer_id: str | None,
    *,
    taxpayer_owned: bool,
) -> bool:
    """Whether ``principal`` may read or change a tenant-scoped record.

    Role permissions decide *which* operations a role has on a DocType; this
    decides *which records* those operations reach. ``taxpayer_owned`` is True
    when the record type carries a ``taxpayer_id`` column. Taxpayers only reach
    taxpayer-owned records of their own taxpayer; records without a tenant are
    unreachable.
    """
    if not record_tenant_id:
        return False
    for membership in principal.in_tenant(record_tenant_id):
        if membership.role != TAXPAYER:
            return True
        if taxpayer_owned and record_taxpayer_id and record_taxpayer_id == membership.taxpayer_id:
            return True
    return False


def authorize_action(
    scope: TenantScope,
    action: str,
    *,
    taxpayer_id: str | None = None,
    proposed_by: str | None = None,
) -> None:
    """Raise ``ActionDenied`` unless the scope may perform ``action``.

    ``taxpayer_id`` is the taxpayer that owns the target record (required for
    taxpayer actions). ``proposed_by`` is the user who proposed a decision that
    is now being approved.
    """
    allowed = ACTION_ROLES.get(action)
    if allowed is None:
        raise ActionDenied(f"Unknown action: {action}")
    if action in HUMAN_ONLY_ACTIONS and SERVICE in scope.roles:
        raise ActionDenied("Service identities cannot take human decision actions")
    roles = scope.roles & allowed
    if not roles:
        raise ActionDenied(f"Your role cannot perform {action}")
    if roles == {TAXPAYER} and action in TAXPAYER_OWNED_ACTIONS:
        if not taxpayer_id or taxpayer_id not in scope.taxpayer_ids:
            raise ActionDenied("Taxpayers can only act on their own records")
    if action in SEPARATION_OF_DUTIES:
        if not proposed_by:
            raise ActionDenied("Approval needs the proposing user")
        if proposed_by.lower() == scope.user.lower():
            raise ActionDenied("You cannot approve a decision you proposed")


def allowed_actions(scope: TenantScope) -> list[str]:
    """Actions the scope's roles permit, before record-level checks."""
    return sorted(
        action
        for action, roles in ACTION_ROLES.items()
        if scope.roles & roles and not (action in HUMAN_ONLY_ACTIONS and SERVICE in scope.roles)
    )


def query_condition(
    principal: Principal,
    table: str,
    *,
    taxpayer_owned: bool,
    quote: Callable[[str], str],
) -> str:
    """SQL condition restricting ``table`` rows to the principal's scope.

    ``quote`` must escape and quote a string literal (``frappe.db.escape``).
    Every value is a validated UUID and is still quoted. Returns a condition
    that matches nothing when the principal has no usable membership.
    """
    tenant_col = f"{table}.`tenant_id`"
    taxpayer_col = f"{table}.`taxpayer_id`"
    staff_tenants: set[str] = set()
    taxpayer_pairs: set[tuple[str, str]] = set()
    for membership in principal.memberships:
        tenant_id = membership.tenant_id
        if not is_stored_uuid(tenant_id):
            continue
        if membership.role != TAXPAYER:
            staff_tenants.add(tenant_id)
        elif taxpayer_owned and is_stored_uuid(membership.taxpayer_id):
            taxpayer_pairs.add((tenant_id, str(membership.taxpayer_id)))
    clauses: list[str] = []
    if staff_tenants:
        values = ", ".join(quote(t) for t in sorted(staff_tenants))
        clauses.append(f"{tenant_col} in ({values})")
    for tenant_id, taxpayer_id in sorted(taxpayer_pairs):
        clauses.append(
            f"({tenant_col} = {quote(tenant_id)} and {taxpayer_col} = {quote(taxpayer_id)})"
        )
    if not clauses:
        return "1=0"
    return "(" + " or ".join(clauses) + ")"
