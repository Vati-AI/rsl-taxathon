"""Frappe adapter for the TAX-031 submission rules.

Record isolation (which tenant/taxpayer a user reaches) comes from the TAX-029
wildcard hooks, because every DocType here has ``tenant_id`` and
``taxpayer_id``. This module adds what those hooks cannot know: who is acting
inside the tenant, which fields that actor may change, and whether the parent
submission is still editable. Refusals raise ``frappe.PermissionError`` (403);
invalid figures raise ``InvalidDomainInput`` (422).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, TypeVar

import frappe
from frappe import _

from vati_tax.identity import permissions
from vati_tax.submissions import domain

SUBMISSION = "Vati Submission"
TAXPAYER = "Vati Taxpayer"

T = TypeVar("T")


class InvalidDomainInput(frappe.ValidationError):
    """Invalid figures, periods or references (API ``invalid_domain_input``)."""

    http_status_code = 422


def domain_call(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a pure rule, converting its errors to Frappe exceptions."""
    try:
        return fn(*args, **kwargs)
    except domain.DomainError as exc:
        frappe.throw(_(str(exc)), InvalidDomainInput, title=exc.reason)
    except (domain.TransitionDenied, domain.WriteDenied) as exc:
        frappe.throw(_(str(exc)), frappe.PermissionError)
    raise AssertionError("unreachable")  # pragma: no cover - frappe.throw raises


def actor_for(tenant_id: str | None, user: str | None = None) -> str:
    """The session user's role inside ``tenant_id`` (``operator`` for site operators).

    One membership per user and tenant (TAX-029), so the role is unambiguous.
    """
    user = user or frappe.session.user
    if permissions.is_operator(user):
        return "operator"
    memberships = permissions.get_principal(user).in_tenant(tenant_id or "")
    return memberships[0].role if memberships else "none"


def changed_fields(doc: Any, fields: Iterable[str]) -> set[str]:
    """Fields whose value differs from the stored document (empty for inserts)."""
    if doc.is_new():
        return set()
    before = doc.get_doc_before_save()
    if before is None:
        return set()
    return {
        field
        for field in fields
        if domain.normalise(before.get(field)) != domain.normalise(doc.get(field))
    }


def check_writes(
    doc: Any,
    actor: str,
    writable: Mapping[str, frozenset[str]],
    server_fields: frozenset[str],
    watched: Iterable[str],
) -> None:
    domain_call(domain.check_writes, changed_fields(doc, watched), actor, writable, server_fields)


def money(doc: Any, field: str, **kwargs: Any) -> None:
    """Parse an amount strictly before Frappe casts it, then store the exact Decimal."""
    doc.set(field, domain_call(domain.parse_money, doc.get(field), field, **kwargs))


def today() -> Any:
    return frappe.utils.getdate(frappe.utils.nowdate())


def parent_submission(doc: Any) -> Any:
    """The parent submission, checked for matching tenant and taxpayer.

    Reads with the database API (no permission check) on purpose: the caller's
    own scope was already enforced on ``doc`` by the tenant hooks, and the
    equality checks below make a reference to a submission outside that scope
    fail. A mismatch is reported the same way whether the parent is missing or
    belongs to someone else, so other tenants cannot be probed.
    """
    submission_id = domain_call(domain.canonical_uuid, doc.get("submission_id"), "submission_id")
    parent = frappe.db.get_value(
        SUBMISSION,
        submission_id,
        ["name", "tenant_id", "taxpayer_id", "submission_type", "status", "period"],
        as_dict=True,
    )
    if (
        not parent
        or parent.tenant_id != doc.get("tenant_id")
        or parent.taxpayer_id != doc.get("taxpayer_id")
    ):
        frappe.throw(
            _("submission_id does not reference a submission of this taxpayer."),
            InvalidDomainInput,
            title="invalid_reference",
        )
    return parent


def require_editable(parent: Any) -> None:
    if parent.status not in domain.EDITABLE_STATUSES:
        frappe.throw(
            _("The submission is {0}; its figures can no longer change.").format(parent.status),
            frappe.PermissionError,
        )
