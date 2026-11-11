"""Frappe handlers for the Vati /api/v1 operations the auditor app uses (TAX-090).

Each handler reads Frappe records (or calls the existing domain store, which
authorises, records the TAX-059 ledger entry and keeps idempotency) and returns a
contract body through ``projections``. Sources (ADR-046):

- cases: ``Vati Case Identity`` (TAX-030), status from ``Vati Case Sync`` (the
  TAX-039 Jube projection), version from the decision trail (TAX-046), refund claim
  from ``Vati Refund Claim`` or the TAX-121 lane proposal, lane from ``Vati Lane
  Proposal``, links from ``Vati Service Link``;
- taxpayers: ``Vati Taxpayer``; evidence: ``evidence.store`` (TAX-032);
- entities: the TAX-087 FtM mapping of the case, for entities indexed in OpenAleph
  (``Vati Entity Index``) with yente candidates (``Vati Screening Result``);
- findings and feedback: ``findings.store`` (TAX-086);
- review, decisions and evidence requests: ``decisions.store`` (TAX-046);
- management summary: the same records plus the TAX-059 ledger; integrity review:
  ``integrity.service`` (TAX-077);
- briefings: the reader TAX-088 registers in the ``vati_briefing_reader`` hook.

Nothing falls back to fixture data. Reads that the domain stores do not record
are authorised here with the TAX-029 matrix; refusals are kept in the ledger.
"""

from __future__ import annotations

import datetime
import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import frappe

from vati_tax.api_v1 import management, projections
from vati_tax.api_v1.core import ApiError, Call, Handler, Result, officer_id, page
from vati_tax.audit import service as audit_service
from vati_tax.audit import store as audit_store
from vati_tax.decisions import store as decisions_store
from vati_tax.evidence import store as evidence_store
from vati_tax.findings import engine as findings_engine
from vati_tax.findings import store as findings_store
from vati_tax.identity import permissions, policy
from vati_tax.ids.canonical import MappingError
from vati_tax.integrations import config
from vati_tax.integrity import service as integrity_service
from vati_tax.outbox import policy as outbox_policy
from vati_tax.pipeline import store as pipeline_store

CASE = "Vati Case Identity"
SYNC = "Vati Case Sync"
TAXPAYER = "Vati Taxpayer"
CLAIM = "Vati Refund Claim"
LANE = "Vati Lane Proposal"
LINK = "Vati Service Link"
AUDIT = "Vati Audit Event"
DECISION = "Vati Decision"
REQUEST = "Vati Evidence Request"
FINDING = "Vati Case Finding"
INDEX = "Vati Entity Index"
SCREENING = "Vati Screening Result"
BRIEFING_HOOK = "vati_briefing_reader"

_CASE_FIELDS = [
    "case_id", "tenant_id", "display_id", "taxpayer_id", "submission_id", "synthetic",
    "creation", "modified",
]  # fmt: skip
WORKLOAD_GAP = (
    "Officer assignments are held in Jube and are not yet mirrored to Vati, "
    "so no open case shows an assigned officer."
)


# --- authorisation --------------------------------------------------------------------


def _authorise(call: Call, action: str, *, case_id: str | None = None) -> policy.TenantScope:
    """The caller's scope in the requested tenant, if it may perform ``action``.

    A refusal is kept in the TAX-059 ledger (``audit_store.guarded``)."""
    scope = permissions.resolve_scope(call.tenant_id)
    ctx = audit_service.Context(
        scope=scope, now=datetime.datetime.now(datetime.UTC), correlation_id=call.correlation_id
    )
    audit_store.guarded(
        ctx,
        attempted_action=action,
        subject_type="case" if case_id else "tenant",
        subject_id=case_id or scope.tenant_id,
        case_id=case_id,
        fn=lambda: policy.authorize_action(scope, action),
    )
    return scope


def translate(exc: Exception) -> ApiError | None:
    """Contract error for an exception from the domain stores, or None."""
    if isinstance(exc, MappingError):
        current = getattr(exc, "current_version", None)
        return ApiError(
            exc.code,
            str(exc),
            field_errors=exc.field_errors,
            retryable=exc.retryable,
            current_version=current if isinstance(current, int) else None,
        )
    if isinstance(exc, permissions.MissingTenantContext | policy.MissingTenantContext):
        return ApiError("missing_tenant_context")
    if isinstance(exc, permissions.TenantScopeDenied | policy.TenantScopeDenied):
        return ApiError("tenant_scope_denied")
    if isinstance(exc, policy.ActionDenied | frappe.PermissionError):
        return ApiError("forbidden")
    if isinstance(exc, frappe.DoesNotExistError):
        return ApiError("not_found")
    if isinstance(exc, outbox_policy.DeliveryError):
        return ApiError("upstream_unavailable", "A service the case needs is unavailable.")
    return None


# --- cases ----------------------------------------------------------------------------


def _by(rows: Iterable[Any], key: str) -> dict[str, Any]:
    return {row[key]: row for row in rows}


def _case_rows(tenant_id: str, **filters: Any) -> list[Any]:
    """Case identities the contract ``Case`` can represent.

    ``Case`` requires a taxpayer and a submission. A case registered without them
    (none is today: the TAX-039 bridge registers cases from sent submissions) is
    left out rather than shown with invented values (ADR-046)."""
    rows: list[Any] = frappe.get_all(
        CASE,
        filters={
            "tenant_id": tenant_id,
            "taxpayer_id": ["is", "set"],
            "submission_id": ["is", "set"],
            **filters,
        },
        fields=_CASE_FIELDS,
    )
    return rows


def _latest(rows: Iterable[Any], key: str, order: Callable[[Any], Any]) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for row in sorted(rows, key=order):
        latest[row[key]] = row
    return latest


def project_cases(tenant_id: str, rows: list[Any]) -> list[dict[str, Any]]:
    """Contract ``Case`` objects for case identity rows of one tenant (bulk reads)."""
    if not rows:
        return []
    ids = [r.case_id for r in rows]
    taxpayers = _by(
        frappe.get_all(
            TAXPAYER,
            filters={"tenant_id": tenant_id, "name": ["in", sorted({r.taxpayer_id for r in rows})]},
            fields=["taxpayer_id", "tenant_id", "display_name", "taxpayer_type"],
        ),
        "taxpayer_id",
    )
    syncs = _by(
        frappe.get_all(
            SYNC,
            filters={"tenant_id": tenant_id, "case_id": ["in", ids]},
            fields=["case_id", "case_status", "observed_at", "closed"],
        ),
        "case_id",
    )
    versions = Counter(
        frappe.get_all(
            AUDIT,
            filters={"tenant_id": tenant_id, "case_id": ["in", ids], "sequence": [">", 0]},
            pluck="case_id",
        )
    )
    claims = _by(
        frappe.get_all(
            CLAIM,
            filters={
                "tenant_id": tenant_id,
                "submission_id": ["in", sorted({r.submission_id for r in rows if r.submission_id})],
            },
            fields=["submission_id", "claimed_amount", "currency"],
        ),
        "submission_id",
    )
    lanes = _latest(
        frappe.get_all(
            LANE,
            filters={"tenant_id": tenant_id, "case_id": ["in", ids]},
            fields=["case_id", "lane", "claimed_amount", "currency", "created_at", "revision"],
        ),
        "case_id",
        lambda r: (r.created_at, r.revision),
    )
    links: dict[str, list[Any]] = {}
    for link in frappe.get_all(
        LINK,
        filters={"tenant_id": tenant_id, "case_id": ["in", ids]},
        fields=[
            "case_id",
            "service",
            "external_type",
            "external_id",
            "sync_state",
            "last_attempt_at",
            "error_code",
        ],  # fmt: skip
    ):
        links.setdefault(link.case_id, []).append(link)
    out = []
    for row in rows:
        taxpayer = taxpayers.get(row.taxpayer_id) or {"taxpayer_id": row.taxpayer_id}
        sync = syncs.get(row.case_id)
        claim = claims.get(row.submission_id)
        lane = lanes.get(row.case_id)
        claimed = (
            projections.money(claim.claimed_amount, claim.currency)
            if claim
            else projections.money(lane.claimed_amount, lane.currency)
            if lane
            else None
        )
        updated = max(
            (v for v in (row.modified, sync.observed_at if sync else None) if v),
            default=row.creation,
        )
        out.append(
            projections.case(
                row,
                taxpayer=taxpayer,
                status=sync.case_status if sync else None,
                version=1 + versions[row.case_id],
                refund_claim=claimed,
                lane=lane.lane if lane else None,
                links=projections.service_links(links.get(row.case_id, [])),
                updated_at=updated,
            )
        )
    return out


def _one_case(tenant_id: str, case_id: str) -> dict[str, Any]:
    rows = _case_rows(tenant_id, case_id=case_id)
    if not rows:
        raise ApiError("not_found")
    return project_cases(tenant_id, rows)[0]


def _case_key(item: Mapping[str, Any]) -> tuple[str, ...]:
    return (item["display_id"], item["case_id"])


def list_cases(call: Call) -> Result:
    scope = _authorise(call, policy.CASE_READ)
    cases = project_cases(scope.tenant_id, _case_rows(scope.tenant_id))
    assigned = call.query.get("assigned_to")
    if assigned == "me":
        me = officer_id(scope.user)
        cases = [c for c in cases if c["assigned_officer_id"] == me]
    elif assigned == "unassigned":
        cases = [c for c in cases if c["assigned_officer_id"] is None]
    if call.query.get("status"):
        cases = [c for c in cases if c["status"] == call.query["status"]]
    if call.query.get("refund_lane"):
        cases = [c for c in cases if c["refund_lane"] == call.query["refund_lane"]]
    return Result(page(cases, _case_key, call.query))


def get_case(call: Call) -> Result:
    case_id = call.params["case_id"]
    scope = _authorise(call, policy.CASE_READ, case_id=case_id)
    return Result({"data": _one_case(scope.tenant_id, case_id)})


def get_taxpayer(call: Call) -> Result:
    scope = _authorise(call, policy.CASE_READ)
    taxpayer_id = call.params["taxpayer_id"]
    record = frappe.db.get_value(
        TAXPAYER,
        {"name": taxpayer_id, "tenant_id": scope.tenant_id},
        [
            "taxpayer_id",
            "tenant_id",
            "display_name",
            "taxpayer_type",
            "tin_masked",
            "registered_on",
            "sector",
            "synthetic",
        ],  # fmt: skip
        as_dict=True,
    )
    cases = project_cases(scope.tenant_id, _case_rows(scope.tenant_id, taxpayer_id=taxpayer_id))
    if not record or not cases:
        # Officers reach taxpayers through cases, as in the fixture contract (TAX-040).
        raise ApiError("not_found")
    open_cases = [c for c in cases if c["status"] in projections.OPEN_STATUSES]
    return Result({"data": projections.taxpayer_profile(record, open_cases=open_cases)})


# --- case records ---------------------------------------------------------------------


def list_evidence(call: Call) -> Result:
    case_id = call.params["case_id"]
    views = evidence_store.case_evidence(tenant_id=call.tenant_id, case_id=case_id)
    items = [projections.evidence(v, case_id=case_id) for v in views]
    return Result(page(items, lambda e: (e["received_at"], e["evidence_id"]), call.query))


def _mapped_entities(tenant_id: str, case_id: str) -> Any:
    """The TAX-087 FtM mapping of the case (the same input OpenAleph indexed)."""
    bundle = pipeline_store.case_bundle(tenant_id, case_id)
    tax_mapping = config.adapter_module("tax_mapping", "openaleph")
    return tax_mapping.map_bundle(bundle)


def list_entities(call: Call) -> Result:
    case_id = call.params["case_id"]
    scope = _authorise(call, policy.CASE_READ, case_id=case_id)
    identity = frappe.db.get_value(
        CASE, {"name": case_id, "tenant_id": scope.tenant_id}, ["synthetic"], as_dict=True
    )
    if not identity:
        raise ApiError("not_found")
    indexed = _latest(
        frappe.get_all(
            INDEX,
            filters={"tenant_id": scope.tenant_id, "case_id": case_id},
            fields=["entity_id", "indexed_at", "reindex_required"],
        ),
        "entity_id",
        lambda r: r.indexed_at,
    )
    if not indexed:
        # The pipeline has not mapped this case yet: no entities exist for it.
        return Result(page([], lambda e: (e["entity_id"],), call.query))
    screened = _latest(
        frappe.get_all(
            SCREENING,
            filters={"tenant_id": scope.tenant_id, "case_id": case_id},
            fields=["entity_id", "candidates_json", "retrieved_at"],
        ),
        "entity_id",
        lambda r: r.retrieved_at,
    )
    mapped = _mapped_entities(scope.tenant_id, case_id)
    items = []
    for ftm in mapped.entities:
        ftm_id = str(ftm["id"])
        if ftm_id not in indexed:
            continue
        candidates = json.loads(screened[ftm_id].candidates_json) if ftm_id in screened else []
        item = projections.entity(
            tenant_id=scope.tenant_id,
            case_id=case_id,
            ftm=ftm,
            extension=mapped.extension(ftm_id),
            index_row=indexed[ftm_id],
            candidates=candidates,
            synthetic=bool(identity.synthetic),
        )
        if item is not None:
            items.append(item)
    return Result(page(items, lambda e: (e["entity_id"],), call.query))


def list_findings(call: Call) -> Result:
    case_id = call.params["case_id"]
    views = findings_store.list_findings(tenant_id=call.tenant_id, case_id=case_id)
    items = [findings_engine.contract_finding(v) for v in views]
    return Result(page(items, lambda f: (f["created_at"], f["finding_id"]), call.query))


def record_finding_feedback(call: Call) -> Result:
    view = findings_store.record_finding_feedback(
        tenant_id=call.tenant_id,
        case_id=call.params["case_id"],
        finding_id=call.params["finding_id"],
        body=dict(call.body),
        idempotency_key=call.idempotency_key or "",
        correlation_id=call.correlation_id,
    )
    data = findings_engine.contract_feedback(
        view, officer_id=officer_id(view["recorded_by"]["user"])
    )
    return Result({"data": data}, replayed=bool(view.get("replayed")))


# --- review, decisions and evidence requests -------------------------------------------


def get_case_review(call: Call) -> Result:
    state = decisions_store.case_review_state(
        tenant_id=call.tenant_id, case_id=call.params["case_id"]
    )
    return Result({"data": projections.case_review(state)})


def propose_decision(call: Call) -> Result:
    body = {"draft_id": None, **dict(call.body)}
    view = decisions_store.propose_decision(
        tenant_id=call.tenant_id,
        case_id=call.params["case_id"],
        body=body,
        idempotency_key=call.idempotency_key or "",
        correlation_id=call.correlation_id,
    )
    return Result({"data": projections.decision(view)}, replayed=bool(view.get("replayed")))


def review_decision(call: Call) -> Result:
    case_id, decision_id = call.params["case_id"], call.params["decision_id"]
    scope = permissions.resolve_scope(call.tenant_id)
    owner = frappe.db.get_value(DECISION, decision_id, ["tenant_id", "case_id"], as_dict=True)
    if not owner or owner.tenant_id != scope.tenant_id or owner.case_id != case_id:
        raise ApiError("not_found")
    view = decisions_store.review_decision(
        tenant_id=call.tenant_id,
        decision_id=decision_id,
        action=call.body["action"],
        body={"expected_version": call.body["expected_version"], "reason": call.body.get("reason")},
        idempotency_key=call.idempotency_key or "",
        correlation_id=call.correlation_id,
    )
    return Result({"data": projections.decision(view)}, replayed=bool(view.get("replayed")))


def request_evidence(call: Call) -> Result:
    view = decisions_store.authorise_evidence_request(
        tenant_id=call.tenant_id,
        case_id=call.params["case_id"],
        body=dict(call.body),
        idempotency_key=call.idempotency_key or "",
        correlation_id=call.correlation_id,
    )
    return Result({"data": projections.evidence_request(view)}, replayed=bool(view.get("replayed")))


# --- briefing -------------------------------------------------------------------------


def get_case_briefing(call: Call) -> Result:
    """The TAX-096 briefing of a case run, read through the TAX-088 reader.

    Until a reader is registered (``vati_briefing_reader`` hook), no briefing has
    been recorded anywhere, so every case answers 404 as the contract specifies."""
    case_id = call.params["case_id"]
    scope = _authorise(call, policy.CASE_READ, case_id=case_id)
    if not frappe.db.exists(CASE, {"name": case_id, "tenant_id": scope.tenant_id}):
        raise ApiError("not_found")
    briefing = None
    for path in frappe.get_hooks(BRIEFING_HOOK):
        briefing = frappe.get_attr(path)(
            tenant_id=scope.tenant_id, case_id=case_id, run_id=call.query.get("run_id")
        )
        if briefing is not None:
            break
    if briefing is None:
        raise ApiError("not_found", "No briefing has been recorded for this case.")
    return Result({"data": briefing})


# --- supervisor views -----------------------------------------------------------------


def _closed_outcomes(tenant_id: str) -> dict[str, str]:
    """The latest approved closing outcome per case (ADR-013 decision records)."""
    rows = frappe.get_all(
        DECISION,
        filters={"tenant_id": tenant_id, "status": "approved"},
        fields=["case_id", "outcome", "reviewed_at"],
    )
    latest = _latest(rows, "case_id", lambda r: r.reviewed_at or datetime.datetime.min)
    return {case_id: row.outcome for case_id, row in latest.items()}


def _current_findings(tenant_id: str) -> list[dict[str, Any]]:
    rows = frappe.get_all(
        FINDING,
        filters={"tenant_id": tenant_id},
        fields=["finding_id", "case_id", "legal_route_status", "supersedes_finding_id"],
    )
    superseded = {r.supersedes_finding_id for r in rows if r.supersedes_finding_id}
    return [
        {"case_id": r.case_id, "legal_route_status": r.legal_route_status}
        for r in rows
        if r.finding_id not in superseded
    ]


def _aware(value: Any) -> datetime.datetime | None:
    if not value:
        return None
    if isinstance(value, str):
        value = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=datetime.UTC)


def get_management_summary(call: Call) -> Result:
    scope = _authorise(call, policy.LEDGER_READ)
    tenant = scope.tenant_id
    until = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
    window = int(call.query.get("window_days") or 30)
    identities = _by(_case_rows(tenant), "case_id")
    cases = project_cases(tenant, list(identities.values()))
    syncs = _by(
        frappe.get_all(SYNC, filters={"tenant_id": tenant}, fields=["case_id", "observed_at"]),
        "case_id",
    )
    outcomes = _closed_outcomes(tenant)
    open_cases, closed_cases = [], []
    for item in cases:
        created = _aware(identities[item["case_id"]].creation)
        if item["status"] in projections.OPEN_STATUSES:
            open_cases.append({**item, "created_at": created})
        else:
            sync = syncs.get(item["case_id"])
            closed_cases.append(
                {
                    "case_id": item["case_id"],
                    "created_at": created,
                    "closed_at": _aware(sync.observed_at) if sync else None,
                    "outcome": outcomes.get(item["case_id"]),
                }
            )
    requests = [
        {"case_id": r.case_id, "status": r.status, "due_date": r.due_date}
        for r in frappe.get_all(
            REQUEST, filters={"tenant_id": tenant}, fields=["case_id", "status", "due_date"]
        )
    ]
    ledger_store = audit_store.FrappeLedger()
    body = management.summary(
        tenant_id=tenant,
        until=until,
        window_days=window,
        open_cases=open_cases,
        closed_cases=closed_cases,
        evidence_requests=requests,
        findings=_current_findings(tenant),
        officers={},
        viewer_login=scope.user,
        entries=list(ledger_store.entries(tenant)),
        synthetic=all(c["synthetic"] for c in cases),
        extra_missing=[{"metric": "workload", "detail": WORKLOAD_GAP}] if open_cases else [],
    )
    ctx = audit_service.Context(scope=scope, now=until, correlation_id=call.correlation_id)
    audit_store.record_read(ctx, action="ledger.read", subject_type="ledger", subject_id=tenant)
    return Result({"data": body})


def get_integrity_review(call: Call) -> Result:
    ctx = audit_store.context(call.tenant_id, call.correlation_id)
    try:
        body = integrity_service.integrity_review(
            ctx,
            audit_store.FrappeLedger(),
            gaps=None,
            gap_labels={},
            window_days=int(call.query.get("window_days") or 30),
        )
    except policy.ActionDenied:
        frappe.db.commit()  # nosemgrep: keep the recorded refusal
        raise
    frappe.db.commit()  # nosemgrep: keep the recorded read (GET)
    return Result({"data": body})


HANDLERS: dict[str, Handler] = {
    "listCases": list_cases,
    "getCase": get_case,
    "getTaxpayer": get_taxpayer,
    "listEvidence": list_evidence,
    "listEntities": list_entities,
    "listFindings": list_findings,
    "recordFindingFeedback": record_finding_feedback,
    "getCaseReview": get_case_review,
    "proposeDecision": propose_decision,
    "reviewDecision": review_decision,
    "requestEvidence": request_evidence,
    "getCaseBriefing": get_case_briefing,
    "getManagementSummary": get_management_summary,
    "getIntegrityReview": get_integrity_review,
}
