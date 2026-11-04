"""Explicitly synthetic taxpayer-facing submission fixtures.

These records exist only so the portal shell can render realistic list and
status screens before TAX-031 adds the Submission DocTypes. Every payload is
labelled ``mode: fixture`` and ``synthetic: true``. Names echo the synthetic
scenarios in ``data/scenarios/cases.json``; nothing here is a real taxpayer,
assessment or decision. Taxpayers never see risk scores or investigation state.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, TypedDict

FIXTURE_META: dict[str, Any] = {"mode": "fixture", "synthetic": True}


class SubmissionStep(TypedDict):
    label: str
    state: str
    at: str | None


class SubmissionFixture(TypedDict):
    id: str
    reference: str
    taxpayer_name: str
    tax_type: str
    period: str
    amount: str | None
    currency: str
    status: str
    submitted_at: str
    next_step: str
    steps: list[SubmissionStep]


def _steps(*states: tuple[str, str, str | None]) -> list[SubmissionStep]:
    return [{"label": label, "state": state, "at": at} for label, state, at in states]


SUBMISSIONS: tuple[SubmissionFixture, ...] = (
    {
        "id": "SUB-SYN-0001",
        "reference": "VAT-2026-09-000184",
        "taxpayer_name": "Synthetic Mountain Wholesale (Pty) Ltd",
        "tax_type": "VAT refund claim",
        "period": "2026-09",
        "amount": "15000.00",
        "currency": "LSL",
        "status": "under_review",
        "submitted_at": "2026-10-06T09:14:00Z",
        "next_step": "An officer is reviewing your claim. No action is needed from you yet.",
        "steps": _steps(
            ("Submitted", "complete", "2026-10-06T09:14:00Z"),
            ("Documents checked", "complete", "2026-10-06T09:20:00Z"),
            ("Officer review", "current", None),
            ("Outcome issued", "upcoming", None),
        ),
    },
    {
        "id": "SUB-SYN-0002",
        "reference": "VAT-2026-08-000921",
        "taxpayer_name": "Synthetic Highland Imports and Logistics Cooperative Society",
        "tax_type": "VAT return",
        "period": "2026-08",
        "amount": "48210.55",
        "currency": "LSL",
        "status": "evidence_requested",
        "submitted_at": "2026-09-19T13:02:00Z",
        "next_step": (
            "Please upload the service contract and proof of withholding for imported"
            " consulting services by 2026-11-17."
        ),
        "steps": _steps(
            ("Submitted", "complete", "2026-09-19T13:02:00Z"),
            ("Documents checked", "complete", "2026-09-19T13:10:00Z"),
            ("Evidence requested", "current", "2026-10-27T08:00:00Z"),
            ("Officer review", "upcoming", None),
            ("Outcome issued", "upcoming", None),
        ),
    },
    {
        "id": "SUB-SYN-0003",
        "reference": "PAYE-2026-09-000057",
        "taxpayer_name": "Synthetic Valley Services",
        "tax_type": "PAYE return",
        "period": "2026-09",
        "amount": None,
        "currency": "LSL",
        "status": "draft",
        "submitted_at": "2026-10-30T16:45:00Z",
        "next_step": "Add the total amount withheld, then submit the return.",
        "steps": _steps(
            ("Draft saved", "current", "2026-10-30T16:45:00Z"),
            ("Submitted", "upcoming", None),
        ),
    },
    {
        "id": "SUB-SYN-0004",
        "reference": "VAT-2026-07-000410",
        "taxpayer_name": "Synthetic Border Retail",
        "tax_type": "VAT return",
        "period": "2026-07",
        "amount": "9875.00",
        "currency": "LSL",
        "status": "completed",
        "submitted_at": "2026-08-14T07:30:00Z",
        "next_step": "Processing is complete. Keep your records for future reference.",
        "steps": _steps(
            ("Submitted", "complete", "2026-08-14T07:30:00Z"),
            ("Documents checked", "complete", "2026-08-14T07:41:00Z"),
            ("Officer review", "complete", "2026-08-21T10:00:00Z"),
            ("Outcome issued", "complete", "2026-08-22T09:00:00Z"),
        ),
    },
)


# TAX-029 ownership: the synthetic tenant used by the API contract examples and
# one synthetic taxpayer per fixture. Kept out of the payload; it only decides
# which taxpayer membership may see each record.
FIXTURE_TENANT_ID = "10000000-0000-4000-8000-000000000001"
FIXTURE_OWNERS: dict[str, str] = {
    "SUB-SYN-0001": "80015243-dd34-4e97-b085-7356ffdd042f",
    "SUB-SYN-0002": "c9c9f393-d1c3-4c1b-9cbe-88ddbb5a65ab",
    "SUB-SYN-0003": "4d1bafec-0bf6-4e66-a2de-16774e9fecd6",
    "SUB-SYN-0004": "af5967a0-b7e0-40f5-b0b6-e1162e45d60e",
}

# (tenant_id, taxpayer_id) pairs from the caller's taxpayer memberships.
TaxpayerScope = Iterable[tuple[str, str]]


def _visible(item: SubmissionFixture, scope: frozenset[tuple[str, str]]) -> bool:
    return (FIXTURE_TENANT_ID, FIXTURE_OWNERS[item["id"]]) in scope


def list_submissions(scope: TaxpayerScope) -> list[SubmissionFixture]:
    """The caller's own fixture submissions, newest first."""
    pairs = frozenset(scope)
    return sorted(
        (item for item in SUBMISSIONS if _visible(item, pairs)),
        key=lambda item: (item["submitted_at"], item["id"]),
        reverse=True,
    )


def find_submission(submission_id: str, scope: TaxpayerScope) -> SubmissionFixture | None:
    """The submission if the caller owns it; another taxpayer's is concealed."""
    pairs = frozenset(scope)
    return next(
        (item for item in SUBMISSIONS if item["id"] == submission_id and _visible(item, pairs)),
        None,
    )
