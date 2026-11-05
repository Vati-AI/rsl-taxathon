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

The evidence request methods (TAX-045) are live too: the caller's own TAX-046
evidence requests in their taxpayer-facing form, and a structured response to
one of them (``vati_tax.responses``).

``get_correction_reminders`` (TAX-078) lists the caller's own reminders from
reviewed omissions in earlier claims, and ``review_refund_claim`` marks the
prompts they cover (``vati_tax.reminders``).
"""

from __future__ import annotations

import uuid
from typing import Any, NoReturn

import frappe
from frappe import _

from vati_tax.bridge import store as bridge_store
from vati_tax.identity.permissions import get_principal
from vati_tax.identity.policy import TAXPAYER, canonical_uuid
from vati_tax.ids.canonical import CaseNotFound, MappingError
from vati_tax.portal import claims, presubmission
from vati_tax.portal.fixtures import FIXTURE_META, find_submission, list_submissions
from vati_tax.reminders import rules as reminder_rules
from vati_tax.reminders import store as reminder_store
from vati_tax.responses import rules as response_rules
from vati_tax.responses import store as response_store
from vati_tax.submissions import domain
from vati_tax.submissions.guard import InvalidDomainInput
from vati_tax.vati_tax.doctype.vati_refund_claim.vati_refund_claim import foreign_evidence_ids

SUBMISSION = "Vati Submission"
REFUND_CLAIM = "Vati Refund Claim"
INVOICE_ROW = "Vati Refund Claim Invoice"
INVOICE_FIELDS = (
    "invoice_number",
    "invoice_date",
    "supplier_name",
    "supplier_tin",
    "net_amount",
    "vat_amount",
    "gross_amount",
    "amount_paid",
    "invoice_evidence_id",
    "contract_evidence_id",
)
LIVE_META: dict[str, Any] = {"mode": "live"}


class PortalRequestError(frappe.ValidationError):
    """Invalid domain input or idempotency conflict (API standard: 422)."""

    http_status_code = 422


class PortalBadRequest(frappe.ValidationError):
    """Malformed request, such as a missing tenant context (API standard: 400)."""

    http_status_code = 400


class PortalUnavailable(frappe.ValidationError):
    """A dependency is unavailable (API standard: 503, retryable)."""

    http_status_code = 503


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
    field_errors: list[claims.FieldError] | list[response_rules.FieldError] | None = None,
    action: str = "submit_refund_claim",
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
    _log(action, code, correlation_id=correlation_id)
    if status == 503:
        frappe.local.response["error"]["retryable"] = True
        raise PortalUnavailable(message)
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
        [
            "name",
            "claimed_amount",
            "claim_reason",
            "explanation",
            "evidence_ids",
            "period_output_vat",
            "period_input_vat",
        ],  # fmt: skip
        as_dict=True,
    )
    if not claim:
        return {**row}
    rows = frappe.get_all(
        INVOICE_ROW,
        filters={"parent": claim.name, "parenttype": REFUND_CLAIM, "parentfield": "invoices"},
        fields=list(INVOICE_FIELDS),
        order_by="idx asc",
    )
    return {**row, **claim, "name": row.name, "invoices": rows}


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
            # TAX-039: the taxpayer-facing review status only; never Jube state.
            **bridge_store.taxpayer_view(submission_id),
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
    period_output_vat: Any = None,
    period_input_vat: Any = None,
    invoices: Any = None,
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
                "period_output_vat": period_output_vat,
                "period_input_vat": period_input_vat,
                "invoices": invoices,
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
                "period_output_vat": domain.format_money(request.period_output_vat),
                "period_input_vat": domain.format_money(request.period_input_vat),
                "invoices": [row.as_dict() for row in request.invoices],
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


# --- evidence request responses (TAX-045) ------------------------------------------

_RESPONSE_MESSAGES = {
    "already_responded": "You have already responded to this request.",
    "request_withdrawn": "This request is no longer needed, so it cannot be answered.",
    "evidence_reused": "A document is already attached to another request. Upload it again for this one.",
    "not_found": "A document could not be found. Remove it and upload the file again.",
}


@frappe.whitelist()
def get_evidence_requests() -> dict[str, Any]:
    """The caller's own evidence requests, in their taxpayer-facing form."""
    _user, scope = _require_taxpayer()
    return {**LIVE_META, "data": response_store.list_requests(scope)}


@frappe.whitelist()
def get_evidence_request(request_id: str) -> dict[str, Any]:
    _user, scope = _require_taxpayer()
    try:
        return {**LIVE_META, "data": response_store.get_request(scope, request_id)}
    except CaseNotFound:
        raise frappe.DoesNotExistError(_("Evidence request not found.")) from None


@frappe.whitelist(methods=["POST"])
def respond_to_evidence_request(
    request_id: str | None = None,
    items: Any = None,
    message: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Answer every item of one of the caller's own open evidence requests.

    Each item gets at least one of the caller's own uploaded documents, or a
    reason it cannot be provided. Resending the same key with the same answers
    returns the stored response with ``replayed: true``.
    """
    _user, scope = _require_taxpayer()
    action = "respond_to_evidence_request"
    request = getattr(frappe.local, "request", None)
    correlation = request.headers.get("X-Vati-Correlation-ID") if request is not None else None
    body = {"items": items, "message": message, "idempotency_key": idempotency_key}
    try:
        result = response_store.respond(scope, request_id or "", body, correlation)
    except response_rules.ResponseInputError as exc:
        _fail(422, "invalid_domain_input", "Check the highlighted answers.", exc.errors, action)
    except CaseNotFound:
        raise frappe.DoesNotExistError(_("Evidence request not found.")) from None
    except MappingError as exc:
        reason = exc.field_errors[0]["code"] if exc.field_errors else exc.code
        if exc.code == "idempotency_conflict":
            _fail(
                422,
                "idempotency_conflict",
                "This response was already sent with different answers.",
                action=action,
            )
        field = exc.field_errors[0]["field"] if exc.field_errors else "request_id"
        text = _RESPONSE_MESSAGES.get(str(reason), "The response could not be sent.")
        _fail(
            422 if exc.http_status == 422 else 400,
            str(exc.code),
            text,
            [response_rules.FieldError(str(field), str(reason), text)],
            action,
        )
    _log(action, "replayed" if result["replayed"] else "responded", request_id=result["request_id"])
    return {**LIVE_META, "replayed": result.pop("replayed"), "data": result}


# --- pre-submission reconciliation (TAX-044) --------------------------------------


@frappe.whitelist(methods=["POST"])
def review_refund_claim(
    period: str | None = None,
    claimed_amount: Any = None,
    claim_reason: str | None = None,
    explanation: str | None = None,
    contact_email: str | None = None,
    evidence_ids: Any = None,
    period_output_vat: Any = None,
    period_input_vat: Any = None,
    invoices: Any = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Check a draft claim before it is sent; stores nothing.

    Validates the same answers as ``submit_refund_claim`` and runs the shared
    TAX-048 reconciliation (``portal.presubmission``). Returns taxpayer-facing
    figures and correction prompts only; prompts never block sending.
    """
    _user, scope = _require_taxpayer()
    tenant, taxpayer = _claim_owner(scope, tenant_id)
    action = "review_refund_claim"
    try:
        request = claims.validate_claim(
            {
                # Review needs no key or declaration; these placeholders only
                # satisfy the shared validator and are never stored.
                "idempotency_key": "review-" + uuid.uuid4().hex,
                "declaration": True,
                "period": period,
                "claimed_amount": claimed_amount,
                "claim_reason": claim_reason,
                "explanation": explanation,
                "contact_email": contact_email,
                "evidence_ids": evidence_ids,
                "period_output_vat": period_output_vat,
                "period_input_vat": period_input_vat,
                "invoices": invoices,
            },
            frappe.utils.getdate(frappe.utils.nowdate()),
        )
    except claims.ClaimInputError as exc:
        _fail(422, "invalid_domain_input", "Check the highlighted answers.", exc.errors, action)
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
            action,
        )
    try:
        result = presubmission.review(
            request.claimed_amount,
            request.period_output_vat,
            request.period_input_vat,
            request.invoices,
        )
    except presubmission.ReconciliationUnavailable as exc:
        _fail(503, "service_unavailable", str(exc), action=action)
    # TAX-078: mark prompts the taxpayer was reminded about after an earlier claim.
    reminders = reminder_store.taxpayer_reminders([(tenant, taxpayer)])
    result["prompts"] = reminder_rules.annotate_prompts(result["prompts"], reminders)
    _log(action, "reviewed", prompts=len(result["prompts"]))
    return {**LIVE_META, "data": result}


# --- correction reminders (TAX-078) -------------------------------------------------


@frappe.whitelist()
def get_correction_reminders() -> dict[str, Any]:
    """The caller's own active reminders from reviewed omissions in earlier claims.

    One per topic, newest first. A reminder whose decision is challenged or was
    overturned, or that staff withdrew, is not returned. Guidance only.
    """
    _user, scope = _require_taxpayer()
    data = reminder_store.taxpayer_reminders(scope)
    _log("get_correction_reminders", "listed", reminders=len(data))
    return {**LIVE_META, "data": data}
