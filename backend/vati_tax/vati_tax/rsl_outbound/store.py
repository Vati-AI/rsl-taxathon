"""Frappe binding: officer-approved results go back to RSL (TAX-124, ADR-043 §1, ADR-048).

Enqueue (``doc_events``, in the officer's own transaction):

- ``on_decision_update``: a ``Vati Decision`` that becomes ``approved`` on a case that
  came from RSL queues a ``findings_pack``, and a ``lane_recommendation`` when the case
  is an Online Refunds claim with a TAX-114 lane proposal;
- ``on_evidence_request_insert``: an officer-authorised ``Vati Evidence Request`` on such
  a case queues an ``evidence_requested`` notice.

Each item is one TAX-033 outbox event (``rsl_outbound.requested``) under a stable
idempotency key, and a ``rsl_outbound.<kind>.queued`` entry in the audit ledger. Nothing
else is queued: a draft, proposed, rejected or withdrawn decision never reaches here.

Deliver (``handle_rsl_outbound``, the outbox worker): the message is rebuilt from the
frozen records, approval is checked again, the outbound contract is checked by the sink,
and the message goes to the site's RSL sink through the outbox effect ledger, so a retry
after a crash never sends it twice. A delivery is recorded as
``rsl_outbound.<kind>.delivered`` with the message digest. An RSL sink that does not
answer is a retryable failure: the item stays queued and shows as not delivered.

Nothing here sets an RSL status, pays, offsets or assesses (the contracts have no such
field). ``case_outbound`` shows officers each item's delivery state.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Any

import frappe

from vati_tax.audit import ledger
from vati_tax.audit import store as audit_store
from vati_tax.identity import permissions, policy
from vati_tax.ids.canonical import CaseNotFound, canonical_uuid
from vati_tax.integrations import config, factories
from vati_tax.outbox import policy as outbox_policy
from vati_tax.outbox import store as outbox_store
from vati_tax.rsl_outbound import rules

CASE = "Vati Case Identity"
DECISION = "Vati Decision"
FINDING = "Vati Case Finding"
LANE = "Vati Lane Proposal"
REQUEST = "Vati Evidence Request"
REFERRAL = "Vati RSL Referral"
SUBMISSION = "Vati Submission"
TAXPAYER = "Vati Taxpayer"
SERVICE_ROLE = "service"
_LEDGER_NS = uuid.UUID("5d1c0e4a-8f7b-4b8e-a3f2-3e0b7c9d0124")

_sinks: Callable[[str], Any] | None = None


def set_sinks(sinks: Callable[[str], Any] | None = None) -> None:
    """Tests inject a sink factory; ``None`` resets to the site's configuration."""
    global _sinks
    _sinks = sinks


def sink_for(tenant_id: str) -> Any:
    return _sinks(tenant_id) if _sinks is not None else factories.rsl_sinks(tenant_id)


def log(action: str, outcome: str, **ids: object) -> None:
    # IDs, kinds and codes only: never amounts, findings text or taxpayer details.
    frappe.logger("vati_tax.rsl_outbound").info({"action": action, "outcome": outcome, **ids})


# --- case context -------------------------------------------------------------------


def case_reference(tenant_id: str, case_id: str) -> rules.RslReference | None:
    """The RSL record behind a case: its audit referral, else its Online Refunds claim."""
    refs = frappe.get_all(
        REFERRAL, filters={"tenant_id": tenant_id, "case_id": case_id}, pluck="audit_case_ref"
    )
    submission_id = frappe.db.get_value(CASE, case_id, "submission_id")
    submission = (
        frappe.db.get_value(
            SUBMISSION,
            submission_id,
            ["tenant_id", "source", "source_ref"],
            as_dict=True,
        )
        if submission_id
        else None
    )
    if submission and submission.tenant_id != tenant_id:
        submission = None
    return rules.rsl_reference(list(refs), submission)


def _case(tenant_id: str, case_id: str) -> dict[str, Any]:
    row = frappe.db.get_value(
        CASE, case_id, ["case_id", "tenant_id", "taxpayer_id", "display_id"], as_dict=True
    )
    if not row or row.tenant_id != tenant_id:
        raise outbox_policy.PermanentDeliveryError("case_not_found")
    tin = frappe.db.get_value(TAXPAYER, row.taxpayer_id, "tin") if row.taxpayer_id else None
    if not tin:
        raise outbox_policy.PermanentDeliveryError("taxpayer_tin_missing")
    return {**row, "tin": tin}


def _latest_lane(tenant_id: str, case_id: str, claim_ref: str) -> Any:
    rows = frappe.get_all(
        LANE,
        filters={"tenant_id": tenant_id, "case_id": case_id, "claim_ref": claim_ref},
        fields=["proposal_id", "revision", "period", "lane", "claimed_amount",
                "undisputed_amount", "disputed_amount", "currency", "decision_due",
                "rsl_claim_status"],
        order_by="revision desc",
        limit=1,
    )  # fmt: skip
    return rows[0] if rows else None


# --- ledger -------------------------------------------------------------------------


def _ledger(
    *,
    tenant_id: str,
    case_id: str,
    taxpayer_id: str | None,
    action: str,
    actor: str,
    role: str,
    subject_type: str,
    subject_id: str,
    payload: Any,
    correlation_id: str,
    event_id: str | None = None,
) -> None:
    if event_id is not None and frappe.db.exists(audit_store.AUDIT, event_id):
        return  # a replayed delivery: its entry is already in the chain
    entry = ledger.Unsealed(
        event_id=event_id or ledger.new_event_id(),
        tenant_id=tenant_id,
        action=action,
        outcome="succeeded",
        actor=actor,
        actor_role=role,
        occurred_at=datetime.datetime.now(datetime.UTC),
        correlation_id=correlation_id,
        subject_type=subject_type,
        subject_id=subject_id,
        payload_hash=ledger.digest(payload),
        case_id=case_id,
        taxpayer_id=taxpayer_id,
        category="write",
    )
    try:
        ledger.append(audit_store.FrappeLedger(), entry)
    except ledger.Duplicate:
        if event_id is None:
            raise
        # A replayed delivery: its entry is already in the chain.


# --- enqueue ------------------------------------------------------------------------


def _enqueue(
    *,
    tenant_id: str,
    case_id: str,
    taxpayer_id: str | None,
    kind: str,
    subject_type: str,
    subject_id: str,
    reference: rules.RslReference,
    role: str,
) -> dict[str, Any]:
    payload = rules.event_payload(kind, subject_id, reference)
    correlation = str(uuid.uuid4())
    event = outbox_store.enqueue(
        tenant_id=tenant_id,
        case_id=case_id,
        type=rules.EVENT_TYPE,
        payload=payload,
        idempotency_key=rules.idempotency_key(kind, subject_id),
        correlation_id=correlation,
        source_service=rules.SOURCE_SERVICE,
        max_attempts=rules.MAX_ATTEMPTS,
    )
    if not event["replayed"]:
        _ledger(
            tenant_id=tenant_id,
            case_id=case_id,
            taxpayer_id=taxpayer_id,
            action=f"rsl_outbound.{kind}.queued",
            actor=str(frappe.session.user),
            role=role or "none",
            subject_type=subject_type,
            subject_id=subject_id,
            payload={**payload, "event_id": event["event_id"]},
            correlation_id=correlation,
        )
    log("enqueue", "replayed" if event["replayed"] else "queued", tenant_id=tenant_id,
        case_id=case_id, kind=kind, event_id=event["event_id"])  # fmt: skip
    return event


def rsl_claim_status_now(tenant_id: str, taxpayer_id: str | None, claim_ref: str) -> str | None:
    """TAX-132: the status RSL's Online Refunds Application reports for the claim now.

    ``None`` when the taxpayer has no TIN, RSL's source is not configured or does not
    answer, or the record carries no status: an unknown status never withholds anything.
    """
    tin = frappe.db.get_value(TAXPAYER, taxpayer_id, "tin") if taxpayer_id else None
    if not tin:
        return None
    try:
        fixture = factories.rsl_sources(tenant_id)
        envelope = fixture.sources.read("online_refunds", "claim",
                                        tenant_id=str(fixture.params["tenant_id"]),
                                        taxpayer=str(tin))  # fmt: skip
    except config.ServiceNotConfigured:
        return None
    if envelope.get("status") != "ok":
        return None
    for record in envelope.get("records") or []:
        if str(record.get("claim_id")) == claim_ref:
            status = record.get("status")
            return str(status) if status else None
    return None


def on_decision_update(doc: Any, method: str | None = None) -> None:
    """``doc_events``: a decision that has just been approved queues its results for RSL."""
    before = doc.get_doc_before_save()
    if doc.status != "approved" or (before is not None and before.status == "approved"):
        return
    try:
        rules.require_approved_decision(doc.as_dict())
    except rules.NotApproved as exc:
        log("enqueue", "refused", case_id=doc.case_id, reason=exc.code)
        return
    reference = case_reference(doc.tenant_id, doc.case_id)
    if reference is None:
        log("enqueue", "not_from_rsl", case_id=doc.case_id, decision_id=doc.decision_id)
        return
    has_lane = reference.system == rules.ONLINE_REFUNDS and bool(
        _latest_lane(doc.tenant_id, doc.case_id, reference.record_id)
    )
    status_now = (
        rsl_claim_status_now(doc.tenant_id, doc.taxpayer_id, reference.record_id)
        if has_lane
        else None
    )
    items = rules.items_for_decision(doc.decision_id, reference, has_lane, status_now)
    if has_lane and len(items) == 1:
        log("enqueue", "lane_not_queued", case_id=doc.case_id, reason=f"rsl_claim_{status_now}")
    for kind, subject_id in items:
        _enqueue(
            tenant_id=doc.tenant_id,
            case_id=doc.case_id,
            taxpayer_id=doc.taxpayer_id,
            kind=kind,
            subject_type="decision",
            subject_id=subject_id,
            reference=reference,
            role=doc.reviewer_role,
        )


def on_evidence_request_insert(doc: Any, method: str | None = None) -> None:
    """``doc_events``: an officer-authorised evidence request queues a notice for RSL."""
    try:
        rules.require_authorised_request(doc.as_dict())
    except rules.NotApproved as exc:
        log("enqueue", "refused", case_id=doc.case_id, reason=exc.code)
        return
    reference = case_reference(doc.tenant_id, doc.case_id)
    if reference is None:
        log("enqueue", "not_from_rsl", case_id=doc.case_id, request_id=doc.request_id)
        return
    _enqueue(
        tenant_id=doc.tenant_id,
        case_id=doc.case_id,
        taxpayer_id=doc.taxpayer_id,
        kind=rules.EVIDENCE_REQUESTED,
        subject_type="evidence_request",
        subject_id=doc.request_id,
        reference=reference,
        role=doc.authoriser_role,
    )


# --- deliver ------------------------------------------------------------------------


def _decision(tenant_id: str, case_id: str, decision_id: str) -> Any:
    row = frappe.db.get_value(
        DECISION,
        decision_id,
        ["decision_id", "tenant_id", "case_id", "status", "outcome", "proposed_by",
         "reviewed_by", "reviewer_role", "reviewed_at", "finding_ids"],
        as_dict=True,
    )  # fmt: skip
    if not row or row.tenant_id != tenant_id or row.case_id != case_id:
        raise outbox_policy.PermanentDeliveryError("decision_not_found")
    return row


def _findings(tenant_id: str, case_id: str, decision: Any) -> list[Any]:
    named = json.loads(decision.finding_ids or "[]")
    if not named:
        return []
    return frappe.get_all(
        FINDING,
        filters={"tenant_id": tenant_id, "case_id": case_id, "finding_id": ["in", named]},
        fields=["finding_id", "revision", "kind", "category", "code", "confidence",
                "legal_route_status", "explanation", "unresolved_amount",
                "unresolved_currency", "evidence_refs"],
    )  # fmt: skip


def _request(tenant_id: str, case_id: str, request_id: str) -> Any:
    row = frappe.db.get_value(
        REQUEST,
        request_id,
        ["request_id", "tenant_id", "case_id", "status", "authorised_by", "due_date", "items"],
        as_dict=True,
    )
    if not row or row.tenant_id != tenant_id or row.case_id != case_id:
        raise outbox_policy.PermanentDeliveryError("evidence_request_not_found")
    return row


def portal_base_url() -> str:
    return str(frappe.conf.get("vati_portal_base_url") or frappe.utils.get_url())


def build_message(env: outbox_policy.Envelope) -> tuple[str, str, dict[str, Any]]:
    """``(kind, subject_id, message)`` for an outbox event, from the stored records."""
    payload = env.payload
    kind, subject_id = str(payload.get("kind")), str(payload.get("subject_id"))
    if kind not in rules.KINDS or not payload.get("rsl_system"):
        raise outbox_policy.PermanentDeliveryError("invalid_payload")
    reference = rules.RslReference(str(payload["rsl_system"]), str(payload["rsl_record_id"]))
    if case_reference(env.tenant_id, env.case_id) != reference:
        raise outbox_policy.PermanentDeliveryError("rsl_reference_changed")
    case = _case(env.tenant_id, env.case_id)
    common: dict[str, Any] = {"key": env.idempotency_key, "reference": reference, "case": case,
              "issued_at": env.occurred_at}  # fmt: skip
    if kind == rules.EVIDENCE_REQUESTED:
        request = _request(env.tenant_id, env.case_id, subject_id)
        message = rules.evidence_requested(request=request, base_url=portal_base_url(), **common)
        return kind, subject_id, message
    decision = _decision(env.tenant_id, env.case_id, subject_id)
    if kind == rules.FINDINGS_PACK:
        findings = _findings(env.tenant_id, env.case_id, decision)
        return kind, subject_id, rules.findings_pack(decision=decision, findings=findings, **common)
    lane = _latest_lane(env.tenant_id, env.case_id, reference.record_id)
    if lane is None:
        raise outbox_policy.PermanentDeliveryError("lane_proposal_not_found")
    return kind, subject_id, rules.lane_recommendation(decision=decision, lane=lane, **common)


def handle_rsl_outbound(delivery: Any) -> None:
    """Outbox handler for ``rsl_outbound.requested`` (see the module docstring)."""
    env = delivery.envelope
    try:
        kind, subject_id, message = build_message(env)
    except rules.NotApproved as exc:
        # Approval no longer holds (e.g. the request was withdrawn): never sent.
        log("deliver", "refused", case_id=env.case_id, reason=exc.code)
        raise outbox_policy.PermanentDeliveryError(exc.code) from None
    except (KeyError, TypeError, ValueError):
        raise outbox_policy.PermanentDeliveryError("message_not_buildable") from None
    try:
        sink = sink_for(env.tenant_id)
    except config.ServiceNotConfigured as exc:
        raise outbox_policy.RetryableDeliveryError(exc.reason) from None

    def send(upstream_key: str) -> str:
        try:
            receipt = sink.deliver(message, idempotency_key=upstream_key)
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code == "rsl_sink_unavailable":
                raise outbox_policy.RetryableDeliveryError(code) from None
            if code in {"idempotency_key_reused"}:
                raise outbox_policy.PermanentDeliveryError(code) from None
            if isinstance(exc, ValueError):  # the outbound contract refused the message
                raise outbox_policy.PermanentDeliveryError("outbound_contract_refused") from None
            raise
        return str(receipt["receipt_id"])

    receipt_id = delivery.effect(f"rsl.{kind}", send, idempotent=True)
    digest = hashlib.sha256(
        json.dumps(message, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    _ledger(
        tenant_id=env.tenant_id,
        case_id=env.case_id,
        taxpayer_id=None,
        action=f"rsl_outbound.{kind}.delivered",
        actor=str(frappe.session.user),
        role=SERVICE_ROLE,
        subject_type="evidence_request" if kind == rules.EVIDENCE_REQUESTED else "decision",
        subject_id=subject_id,
        payload={
            "message_sha256": digest,
            "receipt_id": receipt_id,
            "contract_id": message["contract_id"],
        },  # fmt: skip
        correlation_id=env.correlation_id,
        # One delivered entry per event, even if the handler runs again after a crash.
        event_id=str(uuid.uuid5(_LEDGER_NS, f"{env.tenant_id}:{env.event_id}:delivered")),
    )
    log("deliver", "delivered", tenant_id=env.tenant_id, case_id=env.case_id, kind=kind,
        receipt_id=receipt_id)  # fmt: skip


# --- officer view -------------------------------------------------------------------


def case_outbound(*, tenant_id: str, case_id: str) -> list[dict[str, Any]]:
    """Each outbound item of a case and its delivery state, for officers (``case.read``)."""
    scope = permissions.require_action(policy.CASE_READ, tenant_id)
    case = canonical_uuid(case_id, "case_id")
    if frappe.db.get_value(CASE, case, "tenant_id") != scope.tenant_id:
        raise CaseNotFound("Case not found.", "case_id", "not_found")
    events = frappe.get_all(
        outbox_store.EVENT,
        filters={"tenant_id": scope.tenant_id, "case_id": case, "event_type": rules.EVENT_TYPE},
        fields=["name", "event_id", "status", "attempts", "last_error_code", "payload_json"],
        order_by="sequence asc",
    )
    receipts = {
        row.event: row.result_ref
        for row in frappe.get_all(
            outbox_store.EFFECT,
            filters={"event": ["in", [e.name for e in events] or [""]], "state": "completed"},
            fields=["event", "result_ref"],
        )
    }
    return [rules.delivery_view(event, receipts.get(event.name)) for event in events]
