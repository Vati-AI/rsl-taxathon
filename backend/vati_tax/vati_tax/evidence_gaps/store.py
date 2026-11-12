"""Frappe binding: evidence requests that ask only for the gap (TAX-127, ADR-050).

``propose_gap_request`` builds (and does not store) the itemised request for a case's
Online Refunds claim: the latest lane proposal's TAX-112 ``line_match`` plus RSL's own
claim lines, read through the site's RSL source. Officers only (``case.read``, and the
TAX-142 refund gate).

``approve_gap_request`` is the officer's approval. It rebuilds the proposal, refuses it
if it no longer matches the digest the officer reviewed, and records it through the
TAX-046 evidence-request authorisation (so roles, case version, idempotency, the audit
ledger and the TAX-124 notice to RSL all apply). In the same transaction it stores the
per-item claim lines, answer options and the lines RSL already holds on the request.
Nothing reaches the taxpayer before that approval.
"""

from __future__ import annotations

import json
from typing import Any

import frappe

from vati_tax.assurance import store as assurance_store
from vati_tax.decisions import store as decisions_store
from vati_tax.evidence_gaps import rules
from vati_tax.identity import permissions, policy
from vati_tax.ids.canonical import (
    CaseNotFound,
    InvalidMapping,
    MappingConflict,
    canonical_uuid,
)
from vati_tax.integrations import config
from vati_tax.refund_access import store as refund_access

CASE = "Vati Case Identity"
SUBMISSION = "Vati Submission"
TAXPAYER = "Vati Taxpayer"
LANE = "Vati Lane Proposal"
RUN = "Vati Assurance Run"
REQUEST = "Vati Evidence Request"
CLAIM_SOURCE = "rsl_online_refunds"


class ProposalUnavailable(MappingConflict):
    """The proposal cannot be built now (no claim, no line match, RSL not answering)."""


class ProposalChanged(MappingConflict):
    """The inputs changed since the officer reviewed the proposal (409)."""

    code = "version_conflict"
    http_status = 409


def _case(tenant_id: str, case_id: str) -> Any:
    row = frappe.db.get_value(
        CASE, case_id, ["case_id", "tenant_id", "taxpayer_id", "submission_id"], as_dict=True
    )
    if not row or row.tenant_id != tenant_id:
        raise CaseNotFound("Case not found.", "case_id", "not_found")
    return row


def _unavailable(reason: str, message: str) -> ProposalUnavailable:
    return ProposalUnavailable(message, "case_id", reason)


def build(tenant_id: str, case_id: str) -> dict[str, Any]:
    """The proposal for a case (no permission check; callers check)."""
    case = _case(tenant_id, case_id)
    sub = (
        frappe.db.get_value(SUBMISSION, case.submission_id, ["source", "source_ref"], as_dict=True)
        if case.submission_id
        else None
    )
    if not sub or sub.source != CLAIM_SOURCE or not sub.source_ref:
        raise _unavailable("not_an_online_refunds_claim",
                           "Only a claim read from the Online Refunds Application is itemised.")  # fmt: skip
    claim_ref = str(sub.source_ref)
    lanes = frappe.get_all(
        LANE,
        filters={"tenant_id": tenant_id, "case_id": case_id, "claim_ref": claim_ref},
        fields=["proposal_id", "revision", "body_json", "currency"],
        order_by="revision desc",
        limit=1,
    )
    if not lanes:
        raise _unavailable("line_match_missing", "The claim has not been matched yet.")
    body = json.loads(lanes[0].body_json or "{}")
    tin = frappe.db.get_value(TAXPAYER, case.taxpayer_id, "tin")
    try:
        fixture = assurance_store.sources_for(tenant_id)
    except config.ServiceNotConfigured as exc:
        raise _unavailable("rsl_sources_unavailable", "RSL's claim record cannot be read.") from exc
    envelope = fixture.sources.read(
        "online_refunds", "claim", tenant_id=str(fixture.params["tenant_id"]), taxpayer=str(tin)
    )
    if envelope.get("status") != "ok":
        raise _unavailable("rsl_sources_unavailable", "RSL's claim record cannot be read.")
    claim = rules.find_claim(envelope["records"], claim_ref)
    if claim is None:
        raise _unavailable("claim_not_found", "RSL's claim record was not found.")
    try:
        proposal = rules.build_proposal(
            claim, body.get("line_match") or [], currency=lanes[0].currency or "LSL"
        )
    except rules.GapProposalError as exc:
        raise _unavailable(exc.code, str(exc)) from None
    return {
        **proposal,
        "case_id": case_id,
        "lane_proposal_id": lanes[0].proposal_id,
        "source": {
            "system": "online_refunds",
            "fixture": bool(envelope.get("fixture")),
            "contract_id": envelope.get("contract_id"),
        },  # fmt: skip
    }


def _officer_scope(tenant_id: str, case_id: str, action: str) -> tuple[policy.TenantScope, str]:
    scope = permissions.require_action(action, tenant_id)
    case = canonical_uuid(case_id, "case_id")
    _case(scope.tenant_id, case)
    refund_access.check_case(scope, case, action)
    return scope, case


def propose_gap_request(*, tenant_id: str, case_id: str) -> dict[str, Any]:
    """What an approved request would ask (officers only; nothing is stored)."""
    scope, case = _officer_scope(tenant_id, case_id, policy.CASE_READ)
    if not scope.roles & policy.STAFF_ROLES:
        raise frappe.PermissionError("Only officers review evidence-request proposals.")
    return build(scope.tenant_id, case)


def _default_findings(tenant_id: str, case_id: str) -> list[str]:
    rows = frappe.get_all(
        RUN,
        filters={"tenant_id": tenant_id, "case_id": case_id, "status": ["in", ["completed",
                                                                            "degraded"]]},
        fields=["finding_ids"],
        order_by="started_at desc",
        limit=1,
    )  # fmt: skip
    return list(json.loads(rows[0].finding_ids or "[]")) if rows else []


def approve_gap_request(
    *,
    tenant_id: str,
    case_id: str,
    proposal_digest: str,
    expected_version: int,
    due_date: str,
    message_to_taxpayer: str,
    idempotency_key: str,
    finding_ids: list[str] | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """The officer approves the reviewed proposal; it becomes a TAX-046 evidence request."""
    scope, case = _officer_scope(tenant_id, case_id, policy.EVIDENCE_REQUEST_AUTHORISE)
    proposal = build(scope.tenant_id, case)
    if proposal["proposal_digest"] != proposal_digest:
        raise ProposalChanged(
            "The claim's line match changed since you reviewed it. Review it again.",
            "proposal_digest",
            "proposal_changed",
        )
    if not proposal["items"]:
        raise InvalidMapping(
            "Every claim line is already evidenced; there is nothing to ask.",
            "proposal_digest",
            "nothing_to_ask",
        )
    findings = finding_ids or _default_findings(scope.tenant_id, case)
    view = decisions_store.authorise_evidence_request(
        tenant_id=scope.tenant_id,
        case_id=case,
        body={
            "expected_version": expected_version,
            "finding_ids": findings,
            "items": rules.request_items(proposal),
            "due_date": due_date,
            "message_to_taxpayer": message_to_taxpayer,
        },
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )
    request_id = view["request_id"]
    stored = frappe.db.get_value(REQUEST, request_id, "gap_proposal_digest")
    if stored and stored != proposal_digest:
        raise ProposalChanged("This request was approved from another proposal.",
                              "proposal_digest", "proposal_changed")  # fmt: skip
    if not stored:
        # Written once, in the authorising transaction; the controller freezes it after.
        frappe.db.set_value(
            REQUEST,
            request_id,
            {
                "line_items": json.dumps(rules.line_items(proposal), sort_keys=True),
                "already_held": json.dumps(proposal["already_held"], sort_keys=True),
                "gap_proposal_digest": proposal_digest,
            },
            update_modified=False,
        )
    return {**view, "proposal_digest": proposal_digest, "counts": proposal["counts"]}
