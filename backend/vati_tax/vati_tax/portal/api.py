"""Authenticated taxpayer portal API.

None of these methods allow guests: Frappe rejects an unauthenticated call with
HTTP 403 before the function body runs. Callers also need an enabled taxpayer
membership (TAX-029), and only see their own taxpayer's records: another
taxpayer's submission is reported as not found.

The submissions list and status pages still serve explicitly synthetic fixture
data (``mode: fixture``). ``submit_refund_claim`` (TAX-043) is live: it stores a
VAT refund claim in the TAX-031 DocTypes (``mode: live``). Supporting documents
are uploaded first through ``vati_tax.evidence.api.upload_evidence`` (TAX-032)
and referenced here by evidence ID.
"""

from __future__ import annotations

import uuid
from typing import Any, NoReturn

import frappe
from frappe import _

from vati_tax.identity.permissions import get_principal
from vati_tax.identity.policy import TAXPAYER, canonical_uuid
from vati_tax.portal import claims
from vati_tax.portal.fixtures import FIXTURE_META, find_submission, list_submissions
from vati_tax.submissions import domain
from vati_tax.submissions.guard import InvalidDomainInput
from vati_tax.vati_tax.doctype.vati_refund_claim.vati_refund_claim import foreign_evidence_ids

SUBMISSION = "Vati Submission"
REFUND_CLAIM = "Vati Refund Claim"
LIVE_META: dict[str, Any] = {"mode": "live"}


class PortalRequestError(frappe.ValidationError):
    """Invalid domain input or idempotency conflict (API standard: 422)."""

    http_status_code = 422


class PortalBadRequest(frappe.ValidationError):
    """Malformed request, such as a missing tenant context (API standard: 400)."""

    http_status_code = 400


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


def _log(action: str, outcome: str, **ids: Any) -> None:
    # IDs and outcome codes only: never amounts, explanations or names.
    frappe.logger("vati_tax.portal").info({"action": action, "outcome": outcome, **ids})


def _fail(
    status: int,
    code: str,
    message: str,
    field_errors: list[claims.FieldError] | None = None,
) -> NoReturn:
    """Raise with the docs/API_STANDARD.md error body; Frappe rolls back the request."""
    request = getattr(frappe.local, "request", None)
    header = request.headers.get("X-Vati-Correlation-ID") if request is not None else None
    correlation_id = header or str(uuid.uuid4())
    frappe.local.response["error"] = {
        "code": code,
        "message": message,
        "retryable": False,
        "correlation_id": correlation_id,
        "field_errors": [error.as_dict() for error in field_errors or []],
    }
    _log("submit_refund_claim", code, correlation_id=correlation_id)
    raise (PortalRequestError if status == 422 else PortalBadRequest)(message)


@frappe.whitelist()
def get_session() -> dict[str, Any]:
    user, scope = _require_taxpayer()
    return {
        **FIXTURE_META,
        "user": user,
        "full_name": frappe.utils.get_fullname(user),
        # Lets the portal send X-Vati-Tenant-ID with evidence uploads (TAX-043).
        "taxpayers": [
            {"tenant_id": tenant_id, "taxpayer_id": taxpayer_id} for tenant_id, taxpayer_id in scope
        ],
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


# --- VAT refund claim (TAX-043) ----------------------------------------------------


def _claim_owner(scope: list[tuple[str, str]], tenant_id: str | None) -> tuple[str, str]:
    """The one (tenant, taxpayer) this claim is for, from the caller's own memberships."""
    if tenant_id:
        wanted = canonical_uuid(tenant_id)
        matches = [item for item in scope if item[0] == wanted]
    else:
        matches = scope
    if len(matches) != 1:
        _fail(
            400,
            "missing_tenant_context",
            "Choose which taxpayer account this claim is for, then send it again.",
        )
    return matches[0]


def _stored(submission_key: str) -> Any:
    """The submission (and its refund claim) created under this scoped key, if any."""
    row = frappe.db.get_value(
        SUBMISSION,
        {"submission_key": submission_key},
        ["name", "submission_type", "period", "status", "contact_email"],
        as_dict=True,
    )
    if not row:
        return None
    claim = frappe.db.get_value(
        REFUND_CLAIM,
        {"submission_id": row.name},
        ["claimed_amount", "claim_reason", "explanation", "evidence_ids"],
        as_dict=True,
    )
    return {**row, **(claim or {})}


def _view(submission_id: str, *, replayed: bool) -> dict[str, Any]:
    sub = frappe.db.get_value(
        SUBMISSION,
        submission_id,
        ["name", "reference", "status", "period", "currency", "submitted_at"],
        as_dict=True,
    )
    claim = frappe.db.get_value(
        REFUND_CLAIM,
        {"submission_id": submission_id},
        ["claimed_amount", "claim_reason", "evidence_ids"],
        as_dict=True,
    )
    amount = domain.parse_money(claim.claimed_amount, "claimed_amount")
    return {
        **LIVE_META,
        "replayed": replayed,
        "data": {
            "submission_id": sub.name,
            "reference": sub.reference,
            "status": sub.status,
            "period": sub.period,
            "submitted_at": frappe.utils.get_datetime(sub.submitted_at).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
            if sub.submitted_at
            else None,
            "claimed_amount": domain.format_money(amount),
            "currency": sub.currency,
            "claim_reason": claim.claim_reason,
            "evidence_count": len(domain.evidence_ids(claim.evidence_ids)),
        },
    }


def _replay_or_conflict(stored: Any, request: claims.ClaimRequest) -> dict[str, Any]:
    if stored.get("status") == domain.SUBMITTED and claims.same_request(request, stored):
        _log("submit_refund_claim", "replayed", submission_id=stored["name"])
        return _view(stored["name"], replayed=True)
    _fail(
        422,
        "idempotency_conflict",
        "This claim was already sent with different details. "
        "Check your submissions before starting a new claim.",
    )


@frappe.whitelist(methods=["POST"])
def submit_refund_claim(
    idempotency_key: str | None = None,
    period: str | None = None,
    claimed_amount: Any = None,
    claim_reason: str | None = None,
    explanation: str | None = None,
    contact_email: str | None = None,
    evidence_ids: Any = None,
    declaration: Any = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Send a VAT refund claim with its uploaded supporting documents.

    One request creates the ``refund_claim`` submission and its ``Vati Refund
    Claim``, records the declaration and moves the submission to ``submitted``.
    Any failure rolls the whole request back, so no half-made draft is left.

    Idempotent per taxpayer: resending the same ``idempotency_key`` with the same
    answers returns the original result with ``replayed: true``; with different
    answers it is refused with ``idempotency_conflict`` (422).
    """
    _user, scope = _require_taxpayer()
    tenant, taxpayer = _claim_owner(scope, tenant_id)
    try:
        request = claims.validate_claim(
            {
                "idempotency_key": idempotency_key,
                "period": period,
                "claimed_amount": claimed_amount,
                "claim_reason": claim_reason,
                "explanation": explanation,
                "contact_email": contact_email,
                "evidence_ids": evidence_ids,
                "declaration": declaration,
            },
            frappe.utils.getdate(frappe.utils.nowdate()),
        )
    except claims.ClaimInputError as exc:
        _fail(422, "invalid_domain_input", "Check the highlighted answers.", exc.errors)

    submission_key = domain.scoped_key(tenant, taxpayer, request.idempotency_key)
    stored = _stored(submission_key)
    if stored:
        return _replay_or_conflict(stored, request)

    if foreign_evidence_ids(tenant, taxpayer, request.evidence_ids):
        _fail(
            422,
            "invalid_domain_input",
            "Check the highlighted answers.",
            [
                claims.FieldError(
                    "evidence_ids",
                    "not_found",
                    "A document could not be found. Remove it and upload the file again.",
                )
            ],
        )

    try:
        submission = frappe.get_doc(
            {
                "doctype": SUBMISSION,
                "tenant_id": tenant,
                "taxpayer_id": taxpayer,
                "submission_type": "refund_claim",
                "period": request.period,
                "contact_email": request.contact_email,
                "declaration": 1,
                "source": "portal",
                "idempotency_key": request.idempotency_key,
            }
        ).insert()
        frappe.get_doc(
            {
                "doctype": REFUND_CLAIM,
                "submission_id": submission.name,
                "tenant_id": tenant,
                "taxpayer_id": taxpayer,
                "claimed_amount": domain.format_money(request.claimed_amount),
                "claim_reason": request.claim_reason,
                "explanation": request.explanation,
                "evidence_ids": frappe.as_json(list(request.evidence_ids)),
            }
        ).insert()
        submission.reload()
        submission.status = domain.SUBMITTED
        submission.save()
    except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
        # A concurrent request with the same key won the insert: answer as a retry.
        frappe.db.rollback()
        stored = _stored(submission_key)
        if stored:
            return _replay_or_conflict(stored, request)
        raise
    except InvalidDomainInput:
        _fail(
            422,
            "invalid_domain_input",
            "The claim could not be saved. Check your answers and send it again.",
        )

    _log("submit_refund_claim", "created", submission_id=submission.name)
    return _view(submission.name, replayed=False)
