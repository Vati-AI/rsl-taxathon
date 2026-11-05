"""The taxpayer's view of their own live submissions (TAX-092).

Pure functions, no Frappe: ``portal.api`` reads the Vati Submission, Vati Refund
Claim and Vati Taxpayer rows for the caller's own taxpayer scope and passes the
plain values here. The view is an allowlist: reference, type, period, amount,
the taxpayer-facing status (TAX-039 ``bridge.rules.taxpayer_status``), dates,
next step and progress steps. Case IDs, Jube state, risk scores, lanes,
findings and officer details are never read, so they cannot be shown.
"""

from __future__ import annotations

from typing import Any, TypedDict

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


def _step(label: str, state: str, at: str | None = None) -> Step:
    return {"label": label, "state": state, "at": at}


def steps(status: str, submitted_at: str | None, status_at: str | None) -> list[Step]:
    """Progress for one status; dates only where the system actually recorded one."""
    sent = _step("Submitted", "complete", submitted_at)
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
) -> SubmissionView:
    """One submission as the taxpayer sees it."""
    if status not in STATUSES:
        # A status this view does not know is shown as under review, never raw.
        status = "under_review"
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
        "next_step": NEXT_STEPS[status],
        "steps": steps(status, submitted_at, status_at),
    }


def newest_first(items: list[SubmissionView], created: dict[str, Any]) -> list[SubmissionView]:
    """Sent submissions by send time, newest first; unsent drafts by creation time."""
    return sorted(
        items,
        key=lambda item: (item["submitted_at"] or str(created.get(item["id"], "")), item["id"]),
        reverse=True,
    )
