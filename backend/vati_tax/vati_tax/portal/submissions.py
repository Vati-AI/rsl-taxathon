"""The taxpayer's view of their own live submissions (TAX-092).

Pure functions, no Frappe: ``portal.api`` reads the Vati Submission, Vati Refund
Claim and Vati Taxpayer rows for the caller's own taxpayer scope and passes the
plain values here. The view is an allowlist: reference, type, period, amount,
the taxpayer-facing status (TAX-039 ``bridge.rules.taxpayer_status``), dates,
next step and progress steps. Case IDs, Jube state, risk scores, lanes,
findings and officer details are never read, so they cannot be shown.

TAX-125 (ADR-043): a claim filed in RSL's Online Refunds Application and read into Vati
(TAX-122) also carries an ``rsl`` block: RSL's claim reference and the status RSL shows
for it, labelled with its source, and a link to RSL's dashboard when one is configured.
RSL keeps that status; the portal only displays it.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit

TAX_TYPES = {
    "refund_claim": "VAT refund claim",
    "vat_return": "VAT return",
    "paye_return": "PAYE return",
    "withholding_return": "Withholding tax return",
}

# Taxpayer-facing statuses: Frappe's own (draft, correction_needed, withdrawn) and
# the three mirrored review words once a submission is sent (TAX-039).
STATUSES = (
    "draft",
    "under_review",
    "evidence_requested",
    "correction_needed",
    "completed",
    "withdrawn",
)

NEXT_STEPS = {
    "draft": "Finish and send this submission when you are ready.",
    "under_review": "An officer is reviewing your submission. No action is needed from you yet.",
    "evidence_requested": (
        "An officer has asked for more evidence. Open Evidence requests to see what is"
        " needed and by when."
    ),
    "correction_needed": "This submission needs a correction before it can be reviewed.",
    "completed": "The review is complete. Keep your records for future reference.",
    "withdrawn": "This submission was withdrawn. No further action is needed.",
}


# Where a submission came from (``Vati Submission.source``).
RSL_ONLINE_REFUNDS = "rsl_online_refunds"
RSL_SYSTEM = "RSL's Online Refunds Application"
# The statuses RSL reports for a claim (TAX-111 contract ``claim_status``, assumed codes).
RSL_STATUS_LABELS = {
    "processing": "Processing",
    "pending": "Pending",
    "audit_in_progress": "Audit in progress",
    "processed": "Processed",
    "rejected": "Rejected",
    "cancelled": "Cancelled",
}
RSL_NEXT_STEPS = {
    "under_review": (
        "RSL is reviewing this claim. If RSL needs more evidence from you, the request will"
        " appear under Evidence requests."
    ),
    "evidence_requested": NEXT_STEPS["evidence_requested"],
    "completed": (
        "The evidence review is complete. RSL's Online Refunds Application shows the outcome"
        " of your claim."
    ),
}
RslState = Literal["reported", "not_reported", "unavailable"]


class RslClaim(TypedDict):
    claim_ref: str
    system: str
    status: str | None
    status_label: str | None
    # reported: RSL gave a status; not_reported: RSL's record has none;
    # unavailable: RSL's system did not answer.
    status_state: str
    # True when RSL's record came from a recorded synthetic fixture, not a live system.
    recorded: bool
    dashboard_url: str | None


def dashboard_url(value: object) -> str | None:
    """A configured RSL dashboard link: an https URL without credentials, otherwise none."""
    if not isinstance(value, str) or not value:
        return None
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    return value


def rsl_claim(
    *,
    claim_ref: str,
    status: str | None,
    available: bool,
    recorded: bool,
    link: str | None,
) -> RslClaim:
    """RSL's reference and status for one claim, labelled with where it came from."""
    label = RSL_STATUS_LABELS.get(status or "")
    state: RslState
    if not available:
        state, status, label = "unavailable", None, None
    elif label is None:
        # No status, or one this view does not know: never shown raw.
        state, status = "not_reported", None
    else:
        state = "reported"
    return {
        "claim_ref": claim_ref,
        "system": RSL_SYSTEM,
        "status": status,
        "status_label": label,
        "status_state": state,
        "recorded": recorded,
        "dashboard_url": dashboard_url(link),
    }


class Step(TypedDict):
    label: str
    state: str
    at: str | None


class SubmissionView(TypedDict):
    id: str
    reference: str
    taxpayer_name: str
    tax_type: str
    period: str
    amount: str | None
    currency: str
    status: str
    submitted_at: str | None
    next_step: str
    steps: list[Step]
    # TAX-125: rsl_online_refunds (filed with RSL) or portal (sent in this portal).
    origin: str
    rsl: RslClaim | None


def _step(label: str, state: str, at: str | None = None) -> Step:
    return {"label": label, "state": state, "at": at}


def steps(
    status: str, submitted_at: str | None, status_at: str | None, *, sent_label: str = "Submitted"
) -> list[Step]:
    """Progress for one status; dates only where the system actually recorded one."""
    sent = _step(sent_label, "complete", submitted_at)
    if status == "draft":
        return [_step("Draft saved", "current"), _step("Submitted", "upcoming")]
    if status == "withdrawn":
        return [sent, _step("Withdrawn", "complete")]
    if status == "correction_needed":
        return [sent, _step("Correction needed", "current"), _step("Officer review", "upcoming")]
    if status == "evidence_requested":
        return [
            sent,
            _step("Evidence requested", "current", status_at),
            _step("Officer review", "upcoming"),
            _step("Outcome issued", "upcoming"),
        ]
    if status == "completed":
        return [
            sent,
            _step("Officer review", "complete"),
            _step("Outcome issued", "complete", status_at),
        ]
    return [sent, _step("Officer review", "current"), _step("Outcome issued", "upcoming")]


def view(
    *,
    submission_id: str,
    reference: str | None,
    submission_type: str | None,
    period: str | None,
    currency: str | None,
    status: str,
    submitted_at: str | None,
    status_at: str | None,
    amount: str | None,
    taxpayer_name: str | None,
    source: str | None = None,
    rsl: RslClaim | None = None,
) -> SubmissionView:
    """One submission as the taxpayer sees it."""
    if status not in STATUSES:
        # A status this view does not know is shown as under review, never raw.
        status = "under_review"
    filed_with_rsl = source == RSL_ONLINE_REFUNDS
    next_step = (RSL_NEXT_STEPS if filed_with_rsl else NEXT_STEPS).get(status, NEXT_STEPS[status])
    sent_label = "Filed with RSL" if filed_with_rsl else "Submitted"
    return {
        "id": submission_id,
        "reference": reference or submission_id,
        "taxpayer_name": taxpayer_name or "",
        "tax_type": TAX_TYPES.get(submission_type or "", "Submission"),
        "period": period or "",
        "amount": amount,
        "currency": currency or "LSL",
        "status": status,
        "submitted_at": submitted_at,
        "next_step": next_step,
        "steps": steps(status, submitted_at, status_at, sent_label=sent_label),
        "origin": RSL_ONLINE_REFUNDS if filed_with_rsl else "portal",
        "rsl": rsl if filed_with_rsl else None,
    }


def newest_first(items: list[SubmissionView], created: dict[str, Any]) -> list[SubmissionView]:
    """Sent submissions by send time, newest first; unsent drafts by creation time."""
    return sorted(
        items,
        key=lambda item: (item["submitted_at"] or str(created.get(item["id"], "")), item["id"]),
        reverse=True,
    )
