"""Authenticated taxpayer portal API.

None of these methods allow guests: Frappe rejects an unauthenticated call with
HTTP 403 before the function body runs. Callers also need an enabled taxpayer
membership (TAX-029), and only see their own taxpayer's records: another
taxpayer's submission is reported as not found.

The session, the submissions list and the submission status (TAX-092) are live:
they read the caller's own Vati Submission and Vati Refund Claim records with the
taxpayer-facing review status mirrored from Jube (TAX-039), never case or risk
detail (``portal.submissions``). ``submit_refund_claim`` (TAX-043) stores a VAT
refund claim in the TAX-031 DocTypes (``mode: live``). Supporting documents
are uploaded first through ``vati_tax.evidence.api.upload_evidence`` (TAX-032)
and referenced here by evidence ID.

The evidence request methods (TAX-045) are live too: the caller's own TAX-046
evidence requests in their taxpayer-facing form, and a structured response to
one of them (``vati_tax.responses``).

``get_correction_reminders`` (TAX-078) lists the caller's own reminders from
reviewed omissions in earlier claims, and ``review_refund_claim`` marks the
prompts they cover (``vati_tax.reminders``).

TAX-125 (ADR-043): taxpayers file refund claims in RSL's Online Refunds Application and
answer RSL's evidence requests here. A claim read from RSL (TAX-122) shows RSL's claim
reference and the status RSL reports for it, read from the site's RSL source when the
list is opened (RSL keeps that status). ``submit_refund_claim`` and
``review_refund_claim`` are the fixture-only Online Refunds simulator: they answer only
on a site with ``vati_online_refunds_simulator`` set (``portal.rsl``).
"""

from __future__ import annotations

import uuid
from typing import Any, NoReturn

import frappe
from frappe import _

from vati_tax.bridge import rules as bridge_rules
from vati_tax.bridge import store as bridge_store
from vati_tax.identity.permissions import get_principal
from vati_tax.identity.policy import TAXPAYER, canonical_uuid
from vati_tax.ids.canonical import CaseNotFound, InvalidMapping, MappingError
from vati_tax.integrations import config as integration_config
from vati_tax.integrations import factories
from vati_tax.portal import claims, presubmission
from vati_tax.portal import rsl as portal_rsl
from vati_tax.portal import submissions as portal_submissions
from vati_tax.prefiling import rules as prefiling_rules
from vati_tax.prefiling import store as prefiling_store
from vati_tax.reminders import rules as reminder_rules
from vati_tax.reminders import store as reminder_store
from vati_tax.responses import rules as response_rules
from vati_tax.responses import store as response_store
from vati_tax.submissions import domain
from vati_tax.submissions.guard import InvalidDomainInput
from vati_tax.vati_tax.doctype.vati_refund_claim.vati_refund_claim import foreign_evidence_ids

SUBMISSION = "Vati Submission"
REFUND_CLAIM = "Vati Refund Claim"
TAXPAYER_DOCTYPE = "Vati Taxpayer"
# Read for the taxpayer's own list; nothing here is case, risk or staff data.
SUBMISSION_FIELDS = (
    "name",
    "reference",
    "submission_type",
    "period",
    "currency",
    "status",
    "submitted_at",
    "review_status",
    "review_status_at",
    "taxpayer_id",
    "creation",
    # TAX-125: where the submission came from and RSL's claim reference (TAX-122).
    "tenant_id",
    "source",
    "source_ref",
)
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
        **LIVE_META,
        "user": user,
        "full_name": frappe.utils.get_fullname(user),
        # Lets the portal send X-Vati-Tenant-ID with evidence uploads (TAX-043).
        "taxpayers": [
            {"tenant_id": tenant_id, "taxpayer_id": taxpayer_id} for tenant_id, taxpayer_id in scope
        ],
        # TAX-125: the claim form is a fixture-only Online Refunds simulator.
        "simulator_enabled": portal_rsl.simulator_enabled(frappe.conf),
        "rsl_online_refunds_url": portal_rsl.online_refunds_url(frappe.conf),
    }


def _require_simulator() -> None:
    """The Online Refunds simulator answers only where the site enables it (TAX-125)."""
    if not portal_rsl.simulator_enabled(frappe.conf):
        _log("online_refunds_simulator", "simulator_not_available")
        raise frappe.PermissionError(
            _("Refund claims are filed in RSL's Online Refunds Application.")
        )


def _rsl_claim_statuses(rows: list[Any]) -> dict[str, portal_submissions.RslClaim]:
    """RSL's status for each claim read from RSL (TAX-122), by submission name.

    One read per taxpayer from the site's RSL source. A source that is not configured or
    does not answer is reported as unavailable, never as a status.
    """
    link = portal_rsl.online_refunds_url(frappe.conf)
    out: dict[str, portal_submissions.RslClaim] = {}
    filed = [r for r in rows if r.source == portal_submissions.RSL_ONLINE_REFUNDS and r.source_ref]
    by_taxpayer: dict[tuple[str, str], list[Any]] = {}
    for row in filed:
        by_taxpayer.setdefault((row.tenant_id, row.taxpayer_id), []).append(row)
    for (tenant_id, taxpayer_id), items in by_taxpayer.items():
        statuses: dict[str, str | None] = {}
        available, recorded = False, True
        tin = frappe.db.get_value(TAXPAYER_DOCTYPE, taxpayer_id, "tin")
        try:
            fixture = factories.rsl_sources(tenant_id)
            recorded = fixture.mode == "recorded"
            envelope = fixture.sources.read(
                "online_refunds", "claim", tenant_id=str(fixture.params["tenant_id"]),
                taxpayer=str(tin),
            )  # fmt: skip
            if envelope.get("status") == "ok" and tin:
                available = True
                statuses = {str(r["claim_id"]): r.get("status") for r in envelope["records"]}
        except integration_config.ServiceNotConfigured:
            available = False
        for row in items:
            out[row.name] = portal_submissions.rsl_claim(
                claim_ref=str(row.source_ref),
                status=statuses.get(str(row.source_ref)),
                available=available,
                recorded=recorded,
                link=link,
            )
    return out


def _local_iso(value: Any) -> str | None:
    """A stored Datetime as the portal shows it elsewhere (TAX-043 confirmation)."""
    if not value:
        return None
    return frappe.utils.get_datetime(value).strftime("%Y-%m-%dT%H:%M:%S")


def _utc_iso(value: Any) -> str | None:
    """The bridge's UTC timestamp (review_status_at), as bridge.store.taxpayer_view gives it."""
    if not value:
        return None
    return bridge_rules.iso(frappe.utils.get_datetime(value))


def _own_submissions(
    scope: list[tuple[str, str]], submission_id: str | None = None
) -> list[portal_submissions.SubmissionView]:
    """The caller's own submissions; filtered by tenant and taxpayer in the query itself."""
    rows: list[Any] = []
    for tenant_id, taxpayer_id in scope:
        filters: dict[str, Any] = {"tenant_id": tenant_id, "taxpayer_id": taxpayer_id}
        if submission_id is not None:
            filters["name"] = submission_id
        rows.extend(frappe.get_all(SUBMISSION, filters=filters, fields=list(SUBMISSION_FIELDS)))
    if not rows:
        return []
    names = [row.name for row in rows]
    amounts = {
        claim.submission_id: claim.claimed_amount
        for claim in frappe.get_all(
            REFUND_CLAIM,
            filters={"submission_id": ["in", names]},
            fields=["submission_id", "claimed_amount"],
        )
    }
    rsl_claims = _rsl_claim_statuses(rows)
    taxpayers = {row.taxpayer_id for row in rows}
    display = {
        item.name: item.display_name
        for item in frappe.get_all(
            TAXPAYER_DOCTYPE,
            filters={"name": ["in", list(taxpayers)]},
            fields=["name", "display_name"],
        )
    }
    items = []
    for row in rows:
        amount = amounts.get(row.name)
        items.append(
            portal_submissions.view(
                submission_id=row.name,
                reference=row.reference,
                submission_type=row.submission_type,
                period=row.period,
                currency=row.currency,
                status=bridge_rules.taxpayer_status(row.status, row.review_status),
                submitted_at=_local_iso(row.submitted_at),
                status_at=_utc_iso(row.review_status_at),
                amount=None
                if amount is None
                else domain.format_money(domain.parse_money(amount, "claimed_amount")),
                taxpayer_name=display.get(row.taxpayer_id),
                source=row.source,
                rsl=rsl_claims.get(row.name),
            )
        )
    created = {row.name: _local_iso(row.creation) for row in rows}
    return portal_submissions.newest_first(items, created)


@frappe.whitelist()
def get_submissions() -> dict[str, Any]:
    _user, scope = _require_taxpayer()
    return {**LIVE_META, "data": _own_submissions(scope)}


@frappe.whitelist()
def get_submission(submission_id: str) -> dict[str, Any]:
    _user, scope = _require_taxpayer()
    found = _own_submissions(scope, submission_id=str(submission_id or ""))
    if not found:
        # Another taxpayer's submission is concealed as not found.
        raise frappe.DoesNotExistError(_("Submission not found."))
    return {**LIVE_META, "data": found[0]}


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
    _require_simulator()
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


@frappe.whitelist(methods=["POST"])
def review_evidence_response(request_id: str | None = None, items: Any = None) -> dict[str, Any]:
    """TAX-126: guidance on a draft response before it is sent; stores nothing.

    For each item answered with documents: whether each document could be read, and
    whether its dates, VAT amount and invoice number fit what the item asks for. The
    guidance never blocks ``respond_to_evidence_request``, which does not run it.
    """
    _user, scope = _require_taxpayer()
    action = "review_evidence_response"
    try:
        result = response_store.review(scope, request_id or "", items)
    except CaseNotFound:
        raise frappe.DoesNotExistError(_("Evidence request not found.")) from None
    except InvalidMapping as exc:
        reason = exc.field_errors[0]["code"] if exc.field_errors else exc.code
        text = _RESPONSE_MESSAGES.get(str(reason), "Check the highlighted answers.")
        _fail(422, "invalid_domain_input", text,
              [response_rules.FieldError("items", str(reason), text)], action)  # fmt: skip
    except MappingError as exc:
        reason = exc.field_errors[0]["code"] if exc.field_errors else exc.code
        field = exc.field_errors[0]["field"] if exc.field_errors else "request_id"
        text = _RESPONSE_MESSAGES.get(str(reason), "The response could not be checked.")
        _fail(422, str(exc.code), text, [response_rules.FieldError(str(field), str(reason), text)],
              action)  # fmt: skip
    _log(action, "reviewed", request_id=request_id,
         attention=sum(i["attention"] for i in result["items"]))  # fmt: skip
    return {**LIVE_META, "data": result}


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
    _require_simulator()
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


@frappe.whitelist()
def check_before_filing(period: str | None = None, tenant_id: str | None = None) -> dict[str, Any]:
    """TAX-129 (ADR-051): guidance from RSL-held data before filing in Online Refunds.

    Reads the caller's own e-Tax return, Lekuka invoices and ASYCUDA entries for the
    period; nothing is typed, stored, filed, blocked or scored.
    """
    _user, scope = _require_taxpayer()
    tenant, taxpayer = _claim_owner(scope, tenant_id)
    try:
        data = prefiling_store.check(tenant_id=tenant, taxpayer_id=taxpayer, period=period or "")
    except prefiling_rules.InvalidPeriod as exc:
        _fail(422, "invalid_period", str(exc), action="check_before_filing")
    except prefiling_store.TaxpayerNotFound:
        _fail(422, "taxpayer_not_found", "Your taxpayer record could not be found.",
              action="check_before_filing")  # fmt: skip
    return {**LIVE_META, "data": data}
