"""Frappe binding for case findings and officer finding feedback (TAX-086).

``FrappeRepository`` implements ``engine.Repository`` on the ``Vati Case Finding``
and ``Vati Finding Feedback`` DocTypes. The public functions below resolve the
session user's tenant scope (TAX-029) and run the engine, which authorises every
action with the policy matrix; a refused attempt is kept in the TAX-059 ledger.

Inserts use ``ignore_permissions`` because both DocTypes are read-only in Desk; the
engine's action, tenant and case checks replace the DocType create permission.
Reads use ``frappe.get_all`` inside an explicit tenant and case filter after the
engine checked ``case.read``. Unique keys (idempotency key, one row per finding
revision, one successor per officer feedback) are database indexes, so a concurrent
writer loses with ``Duplicate`` rather than doubling a record.

Callers (the pipeline, TAX-090's /api/v1 layer):

- ``record_rule_signal`` for each TAX-035 rule result of a case,
- ``record_playbook_result`` for a TAX-049 related-party review or proposal,
- ``record_finding`` for any contract-shaped finding body,
- ``list_findings`` / ``get_finding`` to read them back,
- ``record_finding_feedback`` / ``list_finding_feedback`` for officer feedback,
- ``feedback_for_review`` for the TAX-070 feedback loop.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import uuid
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import frappe

from vati_tax.audit import service as audit_service
from vati_tax.audit import store as audit_store
from vati_tax.findings import engine
from vati_tax.findings.engine import CaseRef, Duplicate, FeedbackRecord, FindingRecord
from vati_tax.identity import permissions, policy

FINDING = "Vati Case Finding"
FEEDBACK = "Vati Finding Feedback"
CASE = "Vati Case Identity"
EVIDENCE = "Vati Evidence"
_UNIQUE = (frappe.UniqueValidationError, frappe.DuplicateEntryError)


def _naive(value: datetime.datetime) -> datetime.datetime:
    return value.astimezone(datetime.UTC).replace(tzinfo=None)


def _aware(value: Any) -> datetime.datetime:
    return frappe.utils.get_datetime(value).replace(tzinfo=datetime.UTC)


def _json(value: Any) -> Any:
    return json.loads(value) if value else None


# --- repository ---------------------------------------------------------------------


class FrappeRepository:
    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        savepoint = "vati_finding_" + uuid.uuid4().hex[:12]
        frappe.db.savepoint(savepoint)
        try:
            yield
        except BaseException:
            frappe.db.rollback(save_point=savepoint)
            raise

    def get_case(self, tenant_id: str, case_id: str) -> CaseRef | None:
        row = frappe.db.get_value(CASE, case_id, ["tenant_id", "taxpayer_id"], as_dict=True)
        if not row:
            return None
        return CaseRef(row.tenant_id, case_id, row.taxpayer_id or None)

    def evidence_hashes(
        self, tenant_id: str, taxpayer_id: str | None, evidence_ids: tuple[str, ...]
    ) -> dict[str, str]:
        filters: dict[str, Any] = {"name": ["in", list(evidence_ids)], "tenant_id": tenant_id}
        if taxpayer_id:
            filters["taxpayer_id"] = taxpayer_id
        rows = frappe.get_all(EVIDENCE, filters=filters, fields=["name", "document_sha256"])
        return {row.name: row.document_sha256 for row in rows if row.document_sha256}

    # findings

    _FINDING_FIELDS = (
        "finding_id", "tenant_id", "case_id", "revision", "supersedes_finding_id",
        "lineage_key", "revision_key", "kind", "category", "code", "explanation",
        "evidence_refs", "unresolved_amount", "unresolved_currency", "confidence",
        "alternative_explanations", "legal_route_status", "proposed_action", "producer",
        "producer_version", "subject", "rule_id", "rule_version", "ruleset_id",
        "ruleset_version", "input_digest", "playbook_id", "playbook_version",
        "playbook_content_digest", "registry_version", "run_id", "analysis_provenance",
        "recorded_by", "recorder_role", "created_at", "correlation_id", "registration_key",
        "request_digest", "source_refs", "candidate_route",
    )  # fmt: skip

    @staticmethod
    def _finding(row: Any) -> FindingRecord:
        amount = (
            {"amount": row.unresolved_amount, "currency": row.unresolved_currency}
            if row.unresolved_amount
            else None
        )
        return FindingRecord(
            finding_id=row.finding_id,
            tenant_id=row.tenant_id,
            case_id=row.case_id,
            revision=int(row.revision),
            supersedes_finding_id=row.supersedes_finding_id or None,
            lineage_key=row.lineage_key,
            revision_key=row.revision_key,
            kind=row.kind,
            category=row.category,
            code=row.code or None,
            explanation=row.explanation,
            evidence_refs=tuple(_json(row.evidence_refs) or ()),
            unresolved_amount=amount,
            confidence=row.confidence,
            alternative_explanations=tuple(_json(row.alternative_explanations) or ()),
            legal_route_status=row.legal_route_status,
            proposed_action=row.proposed_action,
            producer=row.producer,
            producer_version=row.producer_version,
            subject=row.subject or None,
            rule_id=row.rule_id or None,
            rule_version=row.rule_version or None,
            ruleset_id=row.ruleset_id or None,
            ruleset_version=row.ruleset_version or None,
            input_digest=row.input_digest or None,
            playbook_id=row.playbook_id or None,
            playbook_version=row.playbook_version or None,
            playbook_content_digest=row.playbook_content_digest or None,
            registry_version=row.registry_version or None,
            run_id=row.run_id or None,
            analysis_provenance=_json(row.analysis_provenance),
            recorded_by=row.recorded_by,
            recorder_role=row.recorder_role,
            created_at=_aware(row.created_at),
            correlation_id=row.correlation_id,
            registration_key=row.registration_key,
            request_digest=row.request_digest,
            source_refs=tuple(_json(row.source_refs) or ()),
            candidate_route=_json(row.candidate_route),
        )

    def _findings(self, filters: dict[str, Any], **kwargs: Any) -> list[FindingRecord]:
        rows = frappe.get_all(FINDING, filters=filters, fields=list(self._FINDING_FIELDS), **kwargs)
        return [self._finding(row) for row in rows]

    def get_finding(self, finding_id: str) -> FindingRecord | None:
        rows = self._findings({"name": finding_id})
        return rows[0] if rows else None

    def finding_by_key(self, registration_key: str) -> FindingRecord | None:
        rows = self._findings({"registration_key": registration_key})
        return rows[0] if rows else None

    def latest_in_lineage(self, lineage_key: str) -> FindingRecord | None:
        rows = self._findings(
            {"lineage_key": lineage_key}, order_by="revision desc", limit_page_length=1
        )
        return rows[0] if rows else None

    def insert_finding(self, record: FindingRecord) -> None:
        amount = record.unresolved_amount or {}
        doc = frappe.get_doc(
            {
                "doctype": FINDING,
                "finding_id": record.finding_id,
                "tenant_id": record.tenant_id,
                "case_id": record.case_id,
                "revision": record.revision,
                "supersedes_finding_id": record.supersedes_finding_id,
                "lineage_key": record.lineage_key,
                "revision_key": record.revision_key,
                "kind": record.kind,
                "category": record.category,
                "code": record.code,
                "explanation": record.explanation,
                "evidence_refs": json.dumps(list(record.evidence_refs), sort_keys=True),
                "unresolved_amount": amount.get("amount"),
                "unresolved_currency": amount.get("currency"),
                "confidence": record.confidence,
                "alternative_explanations": json.dumps(list(record.alternative_explanations)),
                "legal_route_status": record.legal_route_status,
                "proposed_action": record.proposed_action,
                "producer": record.producer,
                "producer_version": record.producer_version,
                "subject": record.subject,
                "rule_id": record.rule_id,
                "rule_version": record.rule_version,
                "ruleset_id": record.ruleset_id,
                "ruleset_version": record.ruleset_version,
                "input_digest": record.input_digest,
                "playbook_id": record.playbook_id,
                "playbook_version": record.playbook_version,
                "playbook_content_digest": record.playbook_content_digest,
                "registry_version": record.registry_version,
                "run_id": record.run_id,
                "analysis_provenance": (
                    json.dumps(record.analysis_provenance, sort_keys=True)
                    if record.analysis_provenance is not None
                    else None
                ),
                "recorded_by": record.recorded_by,
                "recorder_role": record.recorder_role,
                "created_at": _naive(record.created_at),
                "correlation_id": record.correlation_id,
                "registration_key": record.registration_key,
                "request_digest": record.request_digest,
                "source_refs": (
                    json.dumps(list(record.source_refs), sort_keys=True)
                    if record.source_refs
                    else None
                ),
                "candidate_route": (
                    json.dumps(record.candidate_route, sort_keys=True)
                    if record.candidate_route
                    else None
                ),
            }
        )
        try:
            doc.insert(ignore_permissions=True)
        except _UNIQUE as exc:
            raise Duplicate(str(exc)) from exc

    def case_findings(self, tenant_id: str, case_id: str) -> list[FindingRecord]:
        return self._findings({"tenant_id": tenant_id, "case_id": case_id}, order_by="creation asc")

    # feedback

    _FEEDBACK_FIELDS = (
        "feedback_id", "tenant_id", "case_id", "finding_id", "verdict", "reason_code",
        "reason", "modified_explanation", "recorded_by", "recorder_role", "recorded_at",
        "previous_feedback_id", "chain_key", "finding_snapshot", "correlation_id",
        "registration_key", "request_digest",
    )  # fmt: skip

    @staticmethod
    def _feedback_record(row: Any) -> FeedbackRecord:
        return FeedbackRecord(
            feedback_id=row.feedback_id,
            tenant_id=row.tenant_id,
            case_id=row.case_id,
            finding_id=row.finding_id,
            verdict=row.verdict,
            reason_code=row.reason_code,
            reason=row.reason or None,
            modified_explanation=row.modified_explanation or None,
            recorded_by=row.recorded_by,
            recorder_role=row.recorder_role,
            recorded_at=_aware(row.recorded_at),
            previous_feedback_id=row.previous_feedback_id or None,
            chain_key=row.chain_key,
            snapshot=_json(row.finding_snapshot) or {},
            correlation_id=row.correlation_id,
            registration_key=row.registration_key,
            request_digest=row.request_digest,
        )

    def _feedback(self, filters: dict[str, Any], **kwargs: Any) -> list[FeedbackRecord]:
        rows = frappe.get_all(
            FEEDBACK, filters=filters, fields=list(self._FEEDBACK_FIELDS), **kwargs
        )
        return [self._feedback_record(row) for row in rows]

    def feedback_by_key(self, registration_key: str) -> FeedbackRecord | None:
        rows = self._feedback({"registration_key": registration_key})
        return rows[0] if rows else None

    def latest_feedback(self, finding_id: str, user: str) -> FeedbackRecord | None:
        rows = self._feedback(
            {"finding_id": finding_id, "recorded_by": user},
            order_by="recorded_at desc, creation desc",
            limit_page_length=1,
        )
        return rows[0] if rows else None

    def insert_feedback(self, record: FeedbackRecord) -> None:
        doc = frappe.get_doc(
            {
                "doctype": FEEDBACK,
                "feedback_id": record.feedback_id,
                "tenant_id": record.tenant_id,
                "case_id": record.case_id,
                "finding_id": record.finding_id,
                "verdict": record.verdict,
                "reason_code": record.reason_code,
                "reason": record.reason,
                "modified_explanation": record.modified_explanation,
                "recorded_by": record.recorded_by,
                "recorder_role": record.recorder_role,
                "recorded_at": _naive(record.recorded_at),
                "previous_feedback_id": record.previous_feedback_id,
                "chain_key": record.chain_key,
                "finding_snapshot": json.dumps(record.snapshot, sort_keys=True),
                "changes_finding": 0,
                "correlation_id": record.correlation_id,
                "registration_key": record.registration_key,
                "request_digest": record.request_digest,
            }
        )
        try:
            doc.insert(ignore_permissions=True)
        except _UNIQUE as exc:
            raise Duplicate(str(exc)) from exc

    def case_feedback(self, tenant_id: str, case_id: str) -> list[FeedbackRecord]:
        return self._feedback(
            {"tenant_id": tenant_id, "case_id": case_id}, order_by="recorded_at asc"
        )

    def tenant_feedback(
        self, tenant_id: str, since: datetime.datetime | None
    ) -> list[FeedbackRecord]:
        filters: dict[str, Any] = {"tenant_id": tenant_id}
        if since is not None:
            filters["recorded_at"] = [">=", _naive(since)]
        return self._feedback(filters, order_by="recorded_at asc")


# --- service functions --------------------------------------------------------------


def _context(tenant_id: str, correlation_id: str | None) -> engine.Context:
    scope = permissions.resolve_scope(tenant_id)
    return engine.Context.build(scope, correlation_id, datetime.datetime.now(datetime.UTC))


def _guard(
    ctx: engine.Context,
    attempted_action: str,
    case_id: object,
    fn: Callable[[], Any],
    *,
    subject_id: str | None = None,
) -> Any:
    """Run ``fn``; a policy refusal is kept in the ledger (TAX-059) and re-raised."""
    safe_case = policy.canonical_uuid(case_id)
    return audit_store.guarded(
        audit_service.Context(scope=ctx.scope, now=ctx.now, correlation_id=ctx.correlation_id),
        attempted_action=attempted_action,
        subject_type="case",
        subject_id=subject_id or safe_case or "invalid",
        case_id=safe_case,
        fn=fn,
    )


def _log(action: str, ctx: engine.Context, case_id: object, **detail: object) -> None:
    # IDs, codes and counts only: never explanations, reasons or taxpayer details.
    frappe.logger("vati_tax.findings").info(
        {
            "action": action,
            "tenant_id": ctx.scope.tenant_id,
            "case_id": case_id,
            "correlation_id": ctx.correlation_id,
            **detail,
        }
    )


def record_finding(
    *,
    tenant_id: str,
    case_id: str,
    body: Mapping[str, Any],
    idempotency_key: str,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Record one contract-shaped finding (or its next revision); replay-safe."""
    ctx = _context(tenant_id, correlation_id)
    producer = body.get("producer") if isinstance(body, Mapping) else None
    action = policy.FINDING_REVIEW if producer == "officer" else policy.DRAFT_RECORD
    view: dict[str, Any] = _guard(
        ctx,
        action,
        case_id,
        lambda: engine.record_finding(
            ctx, FrappeRepository(), case_id=case_id, body=body, idempotency_key=idempotency_key
        ),
    )
    _log("record_finding", ctx, case_id, finding_id=view["finding_id"], replayed=view["replayed"])
    return view


def record_rule_signal(
    *,
    tenant_id: str,
    case_id: str,
    signal: Mapping[str, Any],
    evidence: Mapping[str, str],
    currency: str = "LSL",
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any] | None:
    """Record one TAX-035 ``tax_risk.evaluate_record`` row; ``no_signal`` returns ``None``.

    ``evidence`` maps each cited source record ID (``INV-SYN-002-01``) to its
    ``Vati Evidence`` ID in the case's tenant and taxpayer; an unmapped citation is
    refused. Without ``idempotency_key`` the key is derived from the rule, its
    versions and the input digest, so re-recording the same evaluation is a replay.
    """
    ctx = _context(tenant_id, correlation_id)
    view: dict[str, Any] | None = _guard(
        ctx,
        policy.DRAFT_RECORD,
        case_id,
        lambda: engine.record_rule_signal(
            ctx,
            FrappeRepository(),
            case_id=case_id,
            signal=signal,
            evidence=evidence,
            currency=currency,
            idempotency_key=idempotency_key,
        ),
    )
    _log(
        "record_rule_signal",
        ctx,
        case_id,
        rule_id=signal.get("rule_id"),
        finding_id=view["finding_id"] if view else None,
        replayed=view["replayed"] if view else None,
    )
    return view


def record_playbook_result(
    *,
    tenant_id: str,
    case_id: str,
    result: Mapping[str, Any],
    stamp: Mapping[str, Any],
    run_id: str,
    evidence: Mapping[str, str],
    analysis_provenance: Mapping[str, Any] | None = None,
    correlation_id: str | None = None,
) -> list[dict[str, Any]]:
    """Record a TAX-049 related-party review or proposal as findings (all or none).

    ``stamp`` is the TAX-061 registry stamp of the playbook version that ran;
    ``analysis_provenance`` is the contract ``AnalysisProvenance`` of the run
    (defaults to the proposal's ``interpretation.analysis_provenance``).
    """
    ctx = _context(tenant_id, correlation_id)
    views: list[dict[str, Any]] = _guard(
        ctx,
        policy.DRAFT_RECORD,
        case_id,
        lambda: engine.record_playbook_result(
            ctx,
            FrappeRepository(),
            case_id=case_id,
            result=result,
            stamp=stamp,
            run_id=run_id,
            evidence=evidence,
            analysis_provenance=analysis_provenance,
        ),
    )
    _log(
        "record_playbook_result",
        ctx,
        case_id,
        playbook=stamp.get("name"),
        recorded=sum(1 for v in views if not v["replayed"]),
        replayed=sum(1 for v in views if v["replayed"]),
    )
    return views


def list_findings(
    *, tenant_id: str, case_id: str, include_superseded: bool = False
) -> list[dict[str, Any]]:
    ctx = _context(tenant_id, None)
    result: list[dict[str, Any]] = _guard(
        ctx,
        policy.CASE_READ,
        case_id,
        lambda: engine.list_findings(
            ctx, FrappeRepository(), case_id=case_id, include_superseded=include_superseded
        ),
    )
    return result


def get_finding(*, tenant_id: str, case_id: str, finding_id: str) -> dict[str, Any]:
    ctx = _context(tenant_id, None)
    result: dict[str, Any] = _guard(
        ctx,
        policy.CASE_READ,
        case_id,
        lambda: engine.get_finding(ctx, FrappeRepository(), case_id=case_id, finding_id=finding_id),
    )
    return result


def record_finding_feedback(
    *,
    tenant_id: str,
    case_id: str,
    finding_id: str,
    body: Mapping[str, Any],
    idempotency_key: str,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """``recordFindingFeedback``: the actor is the session user, never the body."""
    ctx = _context(tenant_id, correlation_id)
    view: dict[str, Any] = _guard(
        ctx,
        policy.FINDING_REVIEW,
        case_id,
        lambda: engine.record_feedback(
            ctx,
            FrappeRepository(),
            case_id=case_id,
            finding_id=finding_id,
            body=body,
            idempotency_key=idempotency_key,
        ),
    )
    _log(
        "record_finding_feedback",
        ctx,
        case_id,
        finding_id=view["finding_id"],
        feedback_id=view["feedback_id"],
        verdict=view["verdict"],
        replayed=view["replayed"],
    )
    return view


def list_finding_feedback(
    *, tenant_id: str, case_id: str, finding_id: str | None = None
) -> list[dict[str, Any]]:
    ctx = _context(tenant_id, None)
    result: list[dict[str, Any]] = _guard(
        ctx,
        policy.FINDING_REVIEW,
        case_id,
        lambda: engine.list_feedback(
            ctx, FrappeRepository(), case_id=case_id, finding_id=finding_id
        ),
    )
    return result


def feedback_for_review(
    *, tenant_id: str, since: datetime.datetime | None = None
) -> dict[str, Any]:
    """The tenant's finding feedback for the TAX-070 loop (see ``engine.feedback_for_review``)."""
    ctx = _context(tenant_id, None)
    result: dict[str, Any] = _guard(
        ctx,
        policy.CASE_READ,
        None,
        lambda: engine.feedback_for_review(ctx, FrappeRepository(), since=since),
        subject_id="finding-feedback",
    )
    return result
