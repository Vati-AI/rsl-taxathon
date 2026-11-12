"""Whitelisted objection and appeal methods (TAX-069).

``X-Vati-Tenant-ID`` selects the tenant (TAX-029). Mutations take an
``Idempotency-Key`` (argument or header); ``X-Vati-Correlation-ID`` is optional.
Every step, read and refusal is kept in the tenant audit ledger (TAX-059).

- ``lodge_challenge`` (POST): the taxpayer, or an auditor or supervisor on the
  taxpayer's behalf. ``stage`` is ``objection`` (with ``decision_id``) or
  ``appeal`` (with ``parent_challenge_id``).
- ``review_challenge`` (POST): a supervisor who did not propose or approve the
  decision, lodge the challenge or review the objection being appealed.
- ``get_challenge_history`` (GET): the decision, its challenges and the final
  reviewed outcome. Taxpayers see only their own, without staff identities.

No deadline or statutory route is enforced: none has been verified by RSL.
"""

from __future__ import annotations

from typing import Any, NoReturn

import frappe

from vati_tax.challenges import store
from vati_tax.identity import permissions
from vati_tax.ids.canonical import MappingError
from vati_tax.refund_access import store as refund_access


class ChallengeBadRequest(frappe.ValidationError):
    http_status_code = 400


class ChallengeConflict(frappe.ValidationError):
    http_status_code = 422


def _raise(exc: MappingError) -> NoReturn:
    if exc.http_status == 404:
        frappe.throw(str(exc), frappe.DoesNotExistError, title=exc.code)
    cls = ChallengeBadRequest if exc.http_status == 400 else ChallengeConflict
    reason = exc.field_errors[0]["code"] if exc.field_errors else exc.code
    frappe.throw(str(exc), cls, title=f"{exc.code}:{reason}")
    raise AssertionError("unreachable")


def _tenant() -> str:
    return permissions.request_tenant_header() or ""


def _header(name: str) -> str | None:
    request = getattr(frappe.local, "request", None)
    return request.headers.get(name) if request is not None else None


def _ids(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return frappe.parse_json(value)
        except Exception:
            return value
    return value


@frappe.whitelist(methods=["POST"])
def lodge_challenge(
    stage: str,
    grounds: str,
    decision_id: str | None = None,
    parent_challenge_id: str | None = None,
    evidence_ids: Any = None,
    route_reference: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    refund_access.require_record("Vati Decision", decision_id, "challenge.lodge")  # TAX-142
    body = {
        "stage": stage,
        "grounds": grounds,
        "decision_id": decision_id or None,
        "parent_challenge_id": parent_challenge_id or None,
        "evidence_ids": _ids(evidence_ids) or [],
        "route_reference": route_reference or None,
    }
    try:
        return store.lodge(
            tenant_id=_tenant(),
            body=body,
            idempotency_key=idempotency_key or _header("Idempotency-Key") or "",
            correlation_id=_header("X-Vati-Correlation-ID"),
        )
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["POST"])
def review_challenge(
    challenge_id: str, expected_version: int | str, outcome: str, reason: str
) -> dict[str, Any]:
    refund_access.require_record(
        "Vati Decision Challenge", challenge_id, "challenge.review"
    )  # TAX-142
    try:
        return store.review(
            tenant_id=_tenant(),
            challenge_id=challenge_id,
            expected_version=frappe.utils.cint(expected_version)
            if expected_version not in (None, "")
            else None,
            outcome=outcome,
            reason=reason,
            correlation_id=_header("X-Vati-Correlation-ID"),
        )
    except MappingError as exc:
        _raise(exc)


@frappe.whitelist(methods=["GET"])
def get_challenge_history(decision_id: str) -> dict[str, Any]:
    refund_access.require_record("Vati Decision", decision_id, "challenge.read")  # TAX-142
    try:
        return store.history(tenant_id=_tenant(), decision_id=decision_id)
    except MappingError as exc:
        _raise(exc)
