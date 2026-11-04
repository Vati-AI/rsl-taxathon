"""Authenticated taxpayer portal API (fixture mode).

None of these methods allow guests: Frappe rejects an unauthenticated call with
HTTP 403 before the function body runs. Callers also need an enabled taxpayer
membership (TAX-029), and only see their own taxpayer's records: another
taxpayer's submission is reported as not found. Every payload is labelled as
synthetic fixture data until TAX-031 adds real Submission DocTypes.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from vati_tax.identity.permissions import get_principal
from vati_tax.identity.policy import TAXPAYER
from vati_tax.portal.fixtures import FIXTURE_META, find_submission, list_submissions


def _require_taxpayer() -> tuple[str, list[tuple[str, str]]]:
    # Defence in depth: whitelisted methods already exclude guests.
    user = frappe.session.user
    if not user or user == "Guest":
        raise frappe.PermissionError(_("Log in to access the taxpayer portal."))
    scope = [
        (m.tenant_id, m.taxpayer_id)
        for m in get_principal(user).memberships
        if m.role == TAXPAYER and m.taxpayer_id
    ]
    if not scope:
        raise frappe.PermissionError(_("This account is not linked to a taxpayer."))
    return user, scope


@frappe.whitelist()
def get_session() -> dict[str, Any]:
    user, _scope = _require_taxpayer()
    return {
        **FIXTURE_META,
        "user": user,
        "full_name": frappe.utils.get_fullname(user),
    }


@frappe.whitelist()
def get_submissions() -> dict[str, Any]:
    _user, scope = _require_taxpayer()
    return {**FIXTURE_META, "data": list_submissions(scope)}


@frappe.whitelist()
def get_submission(submission_id: str) -> dict[str, Any]:
    _user, scope = _require_taxpayer()
    item = find_submission(submission_id, scope)
    if item is None:
        raise frappe.DoesNotExistError(_("Submission not found."))
    return {**FIXTURE_META, "data": item}
