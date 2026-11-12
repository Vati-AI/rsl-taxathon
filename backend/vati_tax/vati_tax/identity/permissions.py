"""Frappe adapter for the TAX-029 tenant policy.

Convention: a DocType of the ``vati_tax`` app that has a ``tenant_id`` field is
tenant-scoped; if it also has a ``taxpayer_id`` field it is taxpayer-owned.
The wildcard ``permission_query_conditions`` and ``has_permission`` hooks in
``hooks.py`` apply ``policy`` to every such DocType, so later DocTypes
(TAX-030 to TAX-033) are protected by adding those fields. Role permissions on
the DocType still decide which operations each role has; these hooks only
narrow them to the principal's tenants/taxpayer and can never grant access.

``Administrator`` and ``System Manager`` (site operators) are not narrowed.
``frappe.get_all``, raw SQL and ``ignore_permissions`` bypass these hooks; use
them only on documented internal paths with explicit scope checks.
"""

from __future__ import annotations

from typing import Any, Literal

import frappe
from frappe import _

from vati_tax.identity import policy

APP = "vati_tax"
MEMBERSHIP = "Vati Tenant Membership"
TENANT = "Vati Tenant"
TENANT_FIELD = "tenant_id"
TAXPAYER_FIELD = "taxpayer_id"
TENANT_HEADER = "X-Vati-Tenant-ID"
OPERATOR_ROLES = frozenset({"System Manager"})
CASE_FIELD = "case_id"
SUBMISSION_FIELD = "submission_id"
SUBMISSION = "Vati Submission"
# TAX-142 (ADR-049): records that hold no refund case data, or that must stay readable
# for the ledger review, are not narrowed by refund confidentiality.
REFUND_EXEMPT = frozenset({"Vati Audit Event", "Vati Outbox Event", "Vati Bridge Inbound Event"})


class MissingTenantContext(frappe.ValidationError):
    http_status_code = 400


class TenantScopeDenied(frappe.PermissionError):
    http_status_code = 403


def _cache() -> dict[str, policy.Principal]:
    if not hasattr(frappe.local, "vati_principals"):
        frappe.local.vati_principals = {}
    return frappe.local.vati_principals


def clear_principal_cache(*_args: Any, **_kwargs: Any) -> None:
    """Forget resolved principals (after a membership or tenant changes)."""
    _cache().clear()


def is_operator(user: str) -> bool:
    return user == "Administrator" or bool(OPERATOR_ROLES & set(frappe.get_roles(user)))


def get_principal(user: str | None = None) -> policy.Principal:
    """The user's enabled memberships in enabled tenants, for roles they hold.

    Internal read: this is the permission source itself, so it uses
    ``frappe.get_all`` filtered to exactly this user. A membership whose Frappe
    role the user no longer holds grants nothing.
    """
    user = user or frappe.session.user
    cache = _cache()
    if user in cache:
        return cache[user]
    memberships: list[policy.Membership] = []
    if user and user != "Guest":
        rows = frappe.get_all(
            MEMBERSHIP,
            filters={"user": user, "enabled": 1},
            fields=["tenant_id", "vati_role", "taxpayer_id", "refund_team"],
            order_by="tenant_id asc",
        )
        enabled_tenants = set(
            frappe.get_all(
                TENANT,
                filters={"name": ["in", [r.tenant_id for r in rows] or [""]], "enabled": 1},
                pluck="name",
            )
        )
        held = set(frappe.get_roles(user))
        for row in rows:
            role = row.vati_role
            if row.tenant_id not in enabled_tenants or role not in policy.ROLES:
                continue
            if policy.FRAPPE_ROLES[role] not in held:
                continue
            memberships.append(
                policy.Membership(
                    row.tenant_id, role, row.taxpayer_id or None, bool(row.refund_team)
                )
            )
    principal = policy.Principal(user=user, memberships=tuple(memberships))
    cache[user] = principal
    return principal


def scope_of(doctype: str) -> tuple[bool, bool]:
    """(tenant_scoped, taxpayer_owned) for ``doctype`` under the convention."""
    if frappe.is_table(doctype):
        return False, False
    meta = frappe.get_meta(doctype)
    if meta.issingle or not meta.has_field(TENANT_FIELD):
        return False, False
    if frappe.get_cached_value("Module Def", meta.module, "app_name") != APP:
        return False, False
    return True, bool(meta.has_field(TAXPAYER_FIELD))


def refund_scope_of(doctype: str) -> tuple[str, Literal["case", "submission"]] | None:
    """``(column, by)`` naming the refund case of a ``doctype`` record, or None.

    TAX-142: a record with a ``case_id`` belongs to that case; a submission record (or
    one with a ``submission_id`` and no case) belongs to its refund claim.
    """
    if doctype in REFUND_EXEMPT:
        return None
    if doctype == SUBMISSION:
        return "name", "submission"
    meta = frappe.get_meta(doctype)
    if meta.has_field(CASE_FIELD):
        return CASE_FIELD, "case"
    if meta.has_field(SUBMISSION_FIELD):
        return SUBMISSION_FIELD, "submission"
    return None


def tenant_query_conditions(user: str | None = None, doctype: str | None = None) -> str:
    """``permission_query_conditions`` hook (``*``): narrow list queries."""
    user = user or frappe.session.user
    if not doctype:
        return ""
    scoped, taxpayer_owned = scope_of(doctype)
    if not scoped or is_operator(user):
        return ""
    principal = get_principal(user)
    condition = policy.query_condition(
        principal,
        f"`tab{doctype}`",
        taxpayer_owned=taxpayer_owned,
        quote=frappe.db.escape,
    )
    refund = refund_scope_of(doctype)
    if refund is None:
        return condition
    column, by = refund
    extra = policy.refund_query_condition(
        principal,
        f"`tab{doctype}`.`{column}`",
        by=by,
        quote=frappe.db.escape,
    )
    return f"({condition} and {extra})" if extra else condition


def tenant_has_permission(
    doc: Any, ptype: str | None = None, user: str | None = None, debug: bool = False
) -> bool:
    """``has_permission`` hook (``*``): deny documents outside the scope.

    Runs for read, write, create, delete and every other permission type, so a
    taxpayer cannot create a record for another taxpayer or move a record into
    another tenant.
    """
    user = user or frappe.session.user
    scoped, taxpayer_owned = scope_of(doc.doctype)
    if not scoped or is_operator(user):
        return True
    principal = get_principal(user)
    if not policy.can_access_record(
        principal,
        doc.get(TENANT_FIELD),
        doc.get(TAXPAYER_FIELD) if taxpayer_owned else None,
        taxpayer_owned=taxpayer_owned,
    ):
        return False
    return _refund_permitted(doc, principal, ptype)


def _refund_permitted(doc: Any, principal: policy.Principal, ptype: str | None) -> bool:
    """TAX-142: a refund case's records only for the refund roles; refusals are recorded."""
    refund = refund_scope_of(doc.doctype)
    tenant_id = doc.get(TENANT_FIELD)
    if refund is None or not tenant_id or doc.is_new():
        return True
    memberships = principal.in_tenant(tenant_id)
    if not memberships or all(m.role == policy.TAXPAYER for m in memberships):
        return True
    from vati_tax.refund_access import store as refund_store

    column, by = refund
    value = doc.name if column == "name" else doc.get(column)
    if by == "case":
        _tenant, is_refund, assignee = refund_store.case_info(value)
        case_id = value
    else:
        _tenant, is_refund, assignee = refund_store.submission_info(value)
        case_id = refund_store.submission_case(value) if value else None
    if not is_refund:
        return True
    scope = policy.resolve_tenant(principal, tenant_id)
    if policy.may_read_refund_case(scope, assignee):
        return True
    request = getattr(frappe.local, "request", None)
    # A Desk read (GET) is otherwise never committed; keep the refusal. Inside a write the
    # entry joins the request's transaction.
    is_get = request is not None and request.method == "GET"
    refund_store.record_denied(
        scope, f"{doc.doctype.lower().replace(' ', '_')}.{ptype or 'read'}",
        case_id or value or "", commit=is_get,
    )  # fmt: skip
    return False


def validate_share(doc: Any, method: str | None = None) -> None:
    """``DocShare`` validate hook.

    Frappe document sharing can grant read/write after a controller denial, so
    a tenant-scoped document may only be shared with a user whose own scope
    already reaches it. Sharing can then never cross tenants or taxpayers.
    """
    if not doc.share_doctype or not doc.share_name or not doc.user:
        return
    scoped, taxpayer_owned = scope_of(doc.share_doctype)
    if not scoped or is_operator(doc.user):
        return
    values = frappe.db.get_value(
        doc.share_doctype,
        doc.share_name,
        [TENANT_FIELD, TAXPAYER_FIELD] if taxpayer_owned else [TENANT_FIELD],
        as_dict=True,
    )
    if not values or not policy.can_access_record(
        get_principal(doc.user),
        values.get(TENANT_FIELD),
        values.get(TAXPAYER_FIELD),
        taxpayer_owned=taxpayer_owned,
    ):
        frappe.throw(
            _("This document cannot be shared outside its tenant or taxpayer."),
            frappe.PermissionError,
        )


def request_tenant_header() -> str | None:
    request = getattr(frappe.local, "request", None)
    return request.headers.get(TENANT_HEADER) if request is not None else None


def resolve_scope(tenant_id: str | None = None, user: str | None = None) -> policy.TenantScope:
    """Tenant scope for this request; ``tenant_id`` defaults to the header."""
    principal = get_principal(user)
    requested = tenant_id if tenant_id is not None else request_tenant_header()
    try:
        return policy.resolve_tenant(principal, requested)
    except policy.MissingTenantContext as exc:
        raise MissingTenantContext(_(str(exc))) from exc
    except policy.TenantScopeDenied as exc:
        raise TenantScopeDenied(_(str(exc))) from exc


def require_action(
    action: str,
    tenant_id: str | None = None,
    *,
    taxpayer_id: str | None = None,
    proposed_by: str | None = None,
    user: str | None = None,
) -> policy.TenantScope:
    """Raise ``frappe.PermissionError`` unless the user may perform ``action``.

    Decision endpoints (TAX-046) call this before persisting anything. Operators
    get no bypass here: decisions need an explicit human role membership.
    """
    scope = resolve_scope(tenant_id, user)
    try:
        policy.authorize_action(scope, action, taxpayer_id=taxpayer_id, proposed_by=proposed_by)
    except policy.ActionDenied as exc:
        raise frappe.PermissionError(_(str(exc))) from exc
    return scope
