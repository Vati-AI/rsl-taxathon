"""Authenticated taxpayer portal API (fixture mode).

None of these methods allow guests: Frappe rejects an unauthenticated call with
HTTP 403 before the function body runs. Every payload is labelled as synthetic
fixture data until TAX-031 adds real Submission DocTypes.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from vati_tax.portal.fixtures import FIXTURE_META, find_submission, list_submissions


def _require_session() -> str:
    # Defence in depth: whitelisted methods already exclude guests.
    user = frappe.session.user
    if not user or user == "Guest":
        raise frappe.PermissionError(_("Log in to access the taxpayer portal."))
    return user


@frappe.whitelist()
def get_session() -> dict[str, Any]:
    user = _require_session()
    return {
        **FIXTURE_META,
        "user": user,
        "full_name": frappe.utils.get_fullname(user),
    }


@frappe.whitelist()
def get_submissions() -> dict[str, Any]:
    _require_session()
    return {**FIXTURE_META, "data": list_submissions()}


@frappe.whitelist()
def get_submission(submission_id: str) -> dict[str, Any]:
    _require_session()
    item = find_submission(submission_id)
    if item is None:
        raise frappe.DoesNotExistError(_("Submission not found."))
    return {**FIXTURE_META, "data": item}
