"""Outbound results from Vati to RSL: pure rules (TAX-124, ADR-043 §1, ADR-048).

No Frappe import, so the rules are testable without a bench. ``store`` binds them to the
DocTypes, the TAX-033 outbox and the audit ledger.

What leaves Vati, and when:

- an officer-approved ``Vati Decision`` on a case that came from RSL emits a
  ``findings_pack`` to the RSL record behind the case, plus a ``lane_recommendation``
  when the case is an Online Refunds claim with a TAX-114 lane proposal;
- an officer-authorised ``Vati Evidence Request`` emits an ``evidence_requested`` notice
  with a deep link into the taxpayer portal.

A case that did not come from RSL emits nothing. Each item is one outbox event with a
stable idempotency key, so approving, replaying or retrying never sends it twice. The
message is rebuilt from frozen records at delivery and checked again: an item whose
approval no longer holds (a withdrawn request) is never sent.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final

EVENT_TYPE: Final = "rsl_outbound.requested"
SOURCE_SERVICE: Final = "vati"
# Kinds and RSL system names: the same values as integration/rsl_sinks/contracts.py.
FINDINGS_PACK: Final = "findings_pack"
LANE_RECOMMENDATION: Final = "lane_recommendation"
EVIDENCE_REQUESTED: Final = "evidence_requested"
KINDS: Final = (FINDINGS_PACK, LANE_RECOMMENDATION, EVIDENCE_REQUESTED)
AUDIT_SYSTEM: Final = "audit_system"
ONLINE_REFUNDS: Final = "online_refunds"
# TAX-122 records an Online Refunds claim as a sent Vati Submission with this source.
CLAIM_SOURCE: Final = "rsl_online_refunds"
ROUTE: Final = "wso2_api_manager"
ASSUMED_SHAPE_NOTICE: Final = (
    "Assumed message shape pending an RSL-supplied specification; not an RSL interface."
)
AUTHORITY: Final = {
    FINDINGS_PACK: "findings_for_rsl_review",
    LANE_RECOMMENDATION: "recommendation_only",
    EVIDENCE_REQUESTED: "notice_only",
}
# An RSL endpoint that does not answer keeps the item queued: the largest budget the
# outbox allows (TAX-033), about 15 hours of capped backoff before an operator is needed.
MAX_ATTEMPTS: Final = 20
TEXT_MAX: Final = 2000
_NS: Final = uuid.UUID("0b5e7f0e-2a3c-4f4e-9b1d-7d1e24a0c124")

# Delivery state an officer sees for each outbox status.
QUEUED: Final = "queued"
DELIVERED: Final = "delivered"
NOT_DELIVERED: Final = "not_delivered"
STATE: Final = {
    "pending": QUEUED,
    "in_progress": QUEUED,
    "retry": QUEUED,
    "delivered": DELIVERED,
    "dead_letter": NOT_DELIVERED,
}


class NotApproved(Exception):
    """The item is not officer-approved (or no longer is): it must not leave Vati."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RslReference:
    """The RSL record a Vati case came from (TAX-122 claim or TAX-123 audit referral)."""

    system: str
    record_id: str

    def as_dict(self) -> dict[str, str]:
        return {"system": self.system, "record_id": self.record_id}


def rsl_reference(
    referral_refs: list[str], submission: Mapping[str, Any] | None
) -> RslReference | None:
    """The case's RSL record: its audit referral first, else its Online Refunds claim.

    A referral case has no submission; a claim case has no referral. If both somehow
    exist, the audit case is where RSL's auditors work, so the pack goes there.
    """
    if referral_refs:
        return RslReference(AUDIT_SYSTEM, sorted(referral_refs)[0])
    if submission and submission.get("source") == CLAIM_SOURCE and submission.get("source_ref"):
        return RslReference(ONLINE_REFUNDS, str(submission["source_ref"]))
    return None


# --- outbox event -------------------------------------------------------------------


def idempotency_key(kind: str, subject_id: str) -> str:
    """One event per item: a decision's pack, a decision's lane, a request's notice."""
    if kind not in KINDS:
        raise ValueError(kind)
    return f"rsl-outbound:{kind}:{subject_id}"


def event_payload(kind: str, subject_id: str, reference: RslReference) -> dict[str, str]:
    return {
        "kind": kind,
        "subject_id": subject_id,
        "rsl_system": reference.system,
        "rsl_record_id": reference.record_id,
    }


# TAX-132: once RSL has processed, rejected or cancelled a claim, a lane recommendation
# for it has nothing left to inform; the findings pack still goes.
RSL_DECIDED_STATUSES = frozenset({"processed", "rejected", "cancelled"})


def items_for_decision(
    decision_id: str,
    reference: RslReference,
    has_lane: bool,
    rsl_claim_status: str | None = None,
) -> list[tuple[str, str]]:
    """``(kind, subject_id)`` for an approved decision.

    ``rsl_claim_status`` is the status RSL reports for the claim now (``None`` when RSL's
    record has none or the source did not answer)."""
    items = [(FINDINGS_PACK, decision_id)]
    if (
        has_lane
        and reference.system == ONLINE_REFUNDS
        and rsl_claim_status not in RSL_DECIDED_STATUSES
    ):
        items.append((LANE_RECOMMENDATION, decision_id))
    return items


# --- approval -----------------------------------------------------------------------


def require_approved_decision(decision: Mapping[str, Any]) -> None:
    """Approved by an officer other than its proposer (TAX-046 separation of duties)."""
    if decision.get("status") != "approved":
        raise NotApproved("decision_not_approved")
    reviewer = decision.get("reviewed_by")
    if not reviewer or reviewer == decision.get("proposed_by"):
        raise NotApproved("decision_not_independently_approved")


def require_authorised_request(request: Mapping[str, Any]) -> None:
    """Authorised by an officer and not withdrawn."""
    if request.get("status") not in {"recorded", "sent", "responded"}:
        raise NotApproved("evidence_request_withdrawn")
    if not request.get("authorised_by"):
        raise NotApproved("evidence_request_not_authorised")


# --- messages -----------------------------------------------------------------------


def message_id(key: str) -> str:
    """Stable per item, so a rebuilt message is identical on every retry."""
    return str(uuid.uuid5(_NS, key))


def iso_utc(value: datetime | str) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def money(value: object) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("amount") from None
    if amount < 0:
        raise ValueError("amount")
    text = str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return text


def _text(value: object) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > TEXT_MAX:
        text = text[: TEXT_MAX - 1] + "…"
    return text or "-"


def _date(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)[:10]


def _ids(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    loaded = json.loads(str(value))
    return loaded if isinstance(loaded, list) else []


def _envelope(
    kind: str, key: str, reference: RslReference, case: Mapping[str, Any], issued_at: datetime
) -> dict[str, Any]:
    return {
        "message_id": message_id(key),
        "contract_id": f"rsl.{reference.system}.{kind}.v0-assumed",
        "kind": kind,
        "authority": AUTHORITY[kind],
        "route": ROUTE,
        "notice": ASSUMED_SHAPE_NOTICE,
        "rsl_reference": reference.as_dict(),
        "tin": str(case["tin"]),
        "vati_case_id": str(case["case_id"]),
        "vati_case_ref": str(case["display_id"]),
        "issued_at": iso_utc(issued_at),
    }


def finding_view(row: Mapping[str, Any]) -> dict[str, Any]:
    amount = row.get("unresolved_amount")
    return {
        "finding_id": str(row["finding_id"]),
        "revision": int(row.get("revision") or 1),
        "kind": str(row["kind"]),
        "category": str(row["category"]),
        "code": str(row["code"]),
        "confidence": str(row["confidence"]),
        "legal_route_status": str(row.get("legal_route_status") or "unverified"),
        "explanation": _text(row.get("explanation")),
        "unresolved_amount": money(amount) if amount not in (None, "") else None,
        "currency": str(row["unresolved_currency"]) if amount not in (None, "") else None,
        "evidence_count": len(_ids(row.get("evidence_refs"))),
    }


def findings_pack(
    *,
    key: str,
    reference: RslReference,
    case: Mapping[str, Any],
    decision: Mapping[str, Any],
    findings: list[Mapping[str, Any]],
    issued_at: datetime,
) -> dict[str, Any]:
    """The approved decision and the findings it names, for the RSL record."""
    require_approved_decision(decision)
    named = _ids(decision.get("finding_ids"))
    by_id = {str(f["finding_id"]): f for f in findings}
    missing = [fid for fid in named if str(fid) not in by_id]
    if missing:
        raise NotApproved("finding_not_found")
    return {
        **_envelope(FINDINGS_PACK, key, reference, case, issued_at),
        "decision": {
            "decision_id": str(decision["decision_id"]),
            "outcome": str(decision["outcome"]),
            "approved_at": iso_utc(decision["reviewed_at"]),
            "approver_role": str(decision["reviewer_role"]),
        },
        "findings": [finding_view(by_id[str(fid)]) for fid in named],
    }


def lane_recommendation(
    *,
    key: str,
    reference: RslReference,
    case: Mapping[str, Any],
    decision: Mapping[str, Any],
    lane: Mapping[str, Any],
    issued_at: datetime,
) -> dict[str, Any]:
    """The latest lane proposal for the claim, sent as a recommendation once an officer
    approved the decision. RSL sets the claim's status and pays; this only informs it."""
    require_approved_decision(decision)
    if reference.system != ONLINE_REFUNDS:
        raise NotApproved("lane_needs_online_refunds_claim")
    return {
        **_envelope(LANE_RECOMMENDATION, key, reference, case, issued_at),
        "period": str(lane["period"]),
        "lane": str(lane["lane"]),
        "claimed_amount": money(lane["claimed_amount"]),
        "undisputed_amount": money(lane["undisputed_amount"]),
        "disputed_amount": money(lane["disputed_amount"]),
        "currency": str(lane["currency"]),
        "decision_due": _date(lane["decision_due"]),
        "proposal_id": str(lane["proposal_id"]),
        "proposal_revision": int(lane["revision"]),
        "approved_decision_id": str(decision["decision_id"]),
        "rsl_claim_status_at_proposal": str(lane.get("rsl_claim_status") or "not_reported"),
    }


def portal_link(base_url: str, request_id: str) -> str:
    """Deep link to the request in the taxpayer portal (``/taxpayer/requests/<id>``)."""
    return f"{base_url.rstrip('/')}/taxpayer/requests/{request_id}"


def evidence_requested(
    *,
    key: str,
    reference: RslReference,
    case: Mapping[str, Any],
    request: Mapping[str, Any],
    base_url: str,
    issued_at: datetime,
) -> dict[str, Any]:
    """Notice that Vati asked the taxpayer for evidence: document types only, no text."""
    require_authorised_request(request)
    items = _ids(request.get("items"))
    types = sorted({str(item["document_type"]) for item in items if item.get("document_type")})
    return {
        **_envelope(EVIDENCE_REQUESTED, key, reference, case, issued_at),
        "request_id": str(request["request_id"]),
        "due_date": _date(request["due_date"]),
        "item_count": len(items),
        "document_types": types,
        "portal_link": portal_link(base_url, str(request["request_id"])),
    }


# --- views --------------------------------------------------------------------------


def delivery_view(event: Mapping[str, Any], receipt_ref: str | None) -> dict[str, Any]:
    """What an officer sees for one outbound item: queued, delivered or not delivered."""
    payload = json.loads(event["payload_json"]) if event.get("payload_json") else {}
    state = STATE.get(str(event["status"]), QUEUED)
    return {
        "event_id": event["event_id"],
        "kind": payload.get("kind"),
        "subject_id": payload.get("subject_id"),
        "rsl_reference": {
            "system": payload.get("rsl_system"),
            "record_id": payload.get("rsl_record_id"),
        },  # fmt: skip
        "route": ROUTE,
        "state": state,
        "delivered": state == DELIVERED,
        "attempts": int(event.get("attempts") or 0),
        "last_error_code": event.get("last_error_code") or None,
        "receipt_id": receipt_ref if state == DELIVERED else None,
        # The fixture sink's receipts say so; there is no live RSL endpoint yet.
        "fixture_receipt": bool(receipt_ref and receipt_ref.startswith("FIXTURE-")),
    }
