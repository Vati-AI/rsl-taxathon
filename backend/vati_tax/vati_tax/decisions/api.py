"""Whitelisted decision and evidence-request methods (TAX-046).

``X-Vati-Tenant-ID`` selects the tenant (TAX-029), ``Idempotency-Key`` keys
every mutation (ADR-003) and ``X-Vati-Correlation-ID`` is carried into the
audit events. List and object arguments may arrive as JSON text from a form
post. Errors keep the ADR-003 code and reason in the exception title and never
echo rationale or message text.

There is deliberately no method that assesses tax, approves, denies or pays a
refund (AGENTS.md); outcomes outside ``rules.OUTCOMES`` are refused.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn

import frappe

from vati_tax.decisions import store
from vati_tax.ids.canonical import MappingError
from vati_tax.refund_access import store as refund_access


class DecisionBadRequest(frappe.ValidationError):
    http_status_code = 400


class DecisionConflict(frappe.ValidationError):
    http_status_code = 409


class DecisionUnprocessable(frappe.ValidationError):
    http_status_code = 422


class DecisionUpstreamUnavailable(frappe.ValidationError):
    http_status_code = 503


_BY_STATUS: dict[int, type[Exception]] = {
    400: DecisionBadRequest,
    409: DecisionConflict,
    422: DecisionUnprocessable,
    503: DecisionUpstreamUnavailable,
}


def _raise(exc: MappingError) -> NoReturn:
    if exc.http_status == 404:
        frappe.throw(str(exc), frappe.DoesNotExistError, title=exc.code)
    reason = exc.field_errors[0]["code"] if exc.field_errors else exc.code
    frappe.throw(
        str(exc), _BY_STATUS.get(exc.http_status, DecisionBadRequest), title=f"{exc.code}:{reason}"
    )
    raise AssertionError("unreachable")


def _header(name: str) -> str | None:
    request = getattr(frappe.local, "request", None)
    return request.headers.get(name) if request is not None else None


def _call(fn: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    try:
        return fn(
            tenant_id=store.permissions.request_tenant_header() or "",
            correlation_id=_header("X-Vati-Correlation-ID"),
            **kwargs,
        )
    except MappingError as exc:
        _raise(exc)


def _json(value: Any) -> Any:
    return frappe.parse_json(value) if isinstance(value, str) else value


def _key(idempotency_key: str | None) -> str:
    return idempotency_key or _header("Idempotency-Key") or ""


@frappe.whitelist(methods=["POST"])
def propose_decision(
    case_id: str,
    outcome: str,
    rationale: str,
    rating: int | str,
    expected_case_status: str,
    expected_version: int | str,
    evidence_ids: Any = None,
    finding_ids: Any = None,
    draft_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    refund_access.require_case(case_id, "decision.propose")  # TAX-142
    body = {
        "outcome": outcome,
        "rationale": rationale,
        "rating": frappe.utils.cint(rating),
        "expected_case_status": expected_case_status,
        "expected_version": frappe.utils.cint(expected_version),
        "evidence_ids": _json(evidence_ids) or [],
        "finding_ids": _json(finding_ids) or [],
        "draft_id": draft_id or None,
    }
    return _call(
        store.propose_decision, case_id=case_id, body=body, idempotency_key=_key(idempotency_key)
    )


@frappe.whitelist(methods=["POST"])
def review_decision(
    decision_id: str,
    action: str,
    expected_version: int | str,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    refund_access.require_record("Vati Decision", decision_id, "decision.approve")  # TAX-142
    body = {"expected_version": frappe.utils.cint(expected_version), "reason": reason or None}
    return _call(
        store.review_decision,
        decision_id=decision_id,
        action=action,
        body=body,
        idempotency_key=_key(idempotency_key),
    )


@frappe.whitelist(methods=["POST"])
def retry_decision(decision_id: str) -> dict[str, Any]:
    refund_access.require_record("Vati Decision", decision_id, "decision.execute")  # TAX-142
    return _call(store.retry_decision, decision_id=decision_id)


@frappe.whitelist(methods=["POST"])
def record_decision_draft(
    case_id: str,
    outcome: str,
    rationale: str,
    evidence_ids: Any = None,
    finding_ids: Any = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    refund_access.require_case(case_id, "draft.record")  # TAX-142
    body = {
        "outcome": outcome,
        "rationale": rationale,
        "evidence_ids": _json(evidence_ids) or [],
        "finding_ids": _json(finding_ids) or [],
    }
    return _call(
        store.record_draft, case_id=case_id, body=body, idempotency_key=_key(idempotency_key)
    )


@frappe.whitelist(methods=["POST"])
def request_evidence(
    case_id: str,
    expected_version: int | str,
    finding_ids: Any,
    items: Any,
    due_date: str,
    message_to_taxpayer: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """``requestEvidence`` (integration/api/openapi.json): record an authorised request."""
    refund_access.require_case(case_id, "evidence_request.authorise")  # TAX-142
    body = {
        "expected_version": frappe.utils.cint(expected_version),
        "finding_ids": _json(finding_ids),
        "items": _json(items),
        "due_date": due_date,
        "message_to_taxpayer": message_to_taxpayer,
    }
    return _call(
        store.authorise_evidence_request,
        case_id=case_id,
        body=body,
        idempotency_key=_key(idempotency_key),
    )


@frappe.whitelist(methods=["GET"])
def get_case_review_state(case_id: str) -> dict[str, Any]:
    refund_access.require_case(case_id, "case.read")  # TAX-142
    try:
        return store.case_review_state(
            tenant_id=store.permissions.request_tenant_header() or "", case_id=case_id
        )
    except MappingError as exc:
        _raise(exc)
