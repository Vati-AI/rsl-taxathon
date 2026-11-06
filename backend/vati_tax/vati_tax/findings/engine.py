"""Case findings and officer finding feedback (TAX-086), independent of Frappe.

``store`` binds ``Repository`` to the ``Vati Case Finding`` and
``Vati Finding Feedback`` DocTypes; tests bind it to ``MemoryRepository``, which
enforces the same unique keys.

- A finding is recorded once per idempotency key. The same key with the same body
  returns the stored finding (``replayed``); a changed body is
  ``idempotency_conflict``. Findings are never edited: a producer that reports
  something different about the same subject (same rule, or same playbook result
  item) appends the next *revision*, which supersedes the previous one. Every
  revision stays readable.
- Rule (``jube_rule``), check (``vati_check``) and agent (``langgraph_run``) findings
  are recorded by a service identity or staff (``draft.record``: output for human review);
  officer findings need ``finding.review``. Reading needs ``case.read``, which
  taxpayers never have; a case in another tenant is ``not_found``.
- Officer feedback (``finding.review``) is append-only. A newer feedback by the same
  officer links ``previous_feedback_id``; it snapshots the judged finding's producer
  and provenance; ``changes_finding`` is always false. The actor is the session user.
"""

from __future__ import annotations

import contextlib
import datetime
import uuid
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from vati_tax.findings import rules
from vati_tax.findings.rules import FindingNotFound, InvalidFinding
from vati_tax.identity import policy
from vati_tax.ids.canonical import CaseNotFound, IdempotencyConflict, MappingError, canonical_uuid


class Duplicate(Exception):
    """The repository refused a write because a unique key is already taken."""


class RevisionConflict(MappingError):
    """409: another writer recorded the same revision or feedback step first."""

    code = "version_conflict"
    http_status = 409


# --- records ------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseRef:
    tenant_id: str
    case_id: str
    taxpayer_id: str | None


@dataclass(frozen=True)
class FindingRecord:
    finding_id: str
    tenant_id: str
    case_id: str
    revision: int
    supersedes_finding_id: str | None
    lineage_key: str
    revision_key: str
    kind: str
    category: str
    code: str | None
    explanation: str
    evidence_refs: tuple[dict[str, Any], ...]
    unresolved_amount: dict[str, str] | None
    confidence: str
    alternative_explanations: tuple[str, ...]
    legal_route_status: str
    proposed_action: str
    producer: str
    producer_version: str
    subject: str | None
    rule_id: str | None
    rule_version: str | None
    ruleset_id: str | None
    ruleset_version: str | None
    input_digest: str | None
    playbook_id: str | None
    playbook_version: str | None
    playbook_content_digest: str | None
    registry_version: str | None
    run_id: str | None
    analysis_provenance: dict[str, Any] | None
    recorded_by: str
    recorder_role: str
    created_at: datetime.datetime
    correlation_id: str
    registration_key: str
    request_digest: str
    # TAX-121 (vati_check findings): RSL source records cited and the TAX-116 route.
    source_refs: tuple[dict[str, Any], ...] = ()
    candidate_route: dict[str, Any] | None = None


@dataclass(frozen=True)
class FeedbackRecord:
    feedback_id: str
    tenant_id: str
    case_id: str
    finding_id: str
    verdict: str
    reason_code: str
    reason: str | None
    modified_explanation: str | None
    recorded_by: str
    recorder_role: str
    recorded_at: datetime.datetime
    previous_feedback_id: str | None
    chain_key: str
    snapshot: dict[str, Any]
    correlation_id: str
    registration_key: str
    request_digest: str


# --- ports --------------------------------------------------------------------------


class Repository(Protocol):
    def atomic(self) -> AbstractContextManager[None]: ...

    def get_case(self, tenant_id: str, case_id: str) -> CaseRef | None: ...

    def evidence_hashes(
        self, tenant_id: str, taxpayer_id: str | None, evidence_ids: tuple[str, ...]
    ) -> dict[str, str]: ...

    def get_finding(self, finding_id: str) -> FindingRecord | None: ...

    def finding_by_key(self, registration_key: str) -> FindingRecord | None: ...

    def latest_in_lineage(self, lineage_key: str) -> FindingRecord | None: ...

    def insert_finding(self, record: FindingRecord) -> None: ...

    def case_findings(self, tenant_id: str, case_id: str) -> list[FindingRecord]: ...

    def feedback_by_key(self, registration_key: str) -> FeedbackRecord | None: ...

    def latest_feedback(self, finding_id: str, user: str) -> FeedbackRecord | None: ...

    def insert_feedback(self, record: FeedbackRecord) -> None: ...

    def case_feedback(self, tenant_id: str, case_id: str) -> list[FeedbackRecord]: ...

    def tenant_feedback(
        self, tenant_id: str, since: datetime.datetime | None
    ) -> list[FeedbackRecord]: ...


@dataclass(frozen=True)
class Context:
    scope: policy.TenantScope
    now: datetime.datetime
    correlation_id: str

    @classmethod
    def build(
        cls, scope: policy.TenantScope, correlation_id: str | None, now: datetime.datetime
    ) -> Context:
        corr = canonical_uuid(correlation_id, "correlation_id") if correlation_id else None
        return cls(scope=scope, now=now, correlation_id=corr or str(uuid.uuid4()))


# --- helpers ------------------------------------------------------------------------


def _authorize(ctx: Context, action: str) -> str:
    policy.authorize_action(ctx.scope, action)
    return sorted(ctx.scope.roles & policy.ACTION_ROLES[action])[0]


def _case(repo: Repository, tenant_id: str, case_id: object) -> CaseRef:
    case_id = canonical_uuid(case_id, "case_id")
    case = repo.get_case(tenant_id, case_id)
    if case is None or case.tenant_id != tenant_id:
        raise CaseNotFound("Case not found.", "case_id", "not_found")
    return case


def _finding(repo: Repository, case: CaseRef, finding_id: object) -> FindingRecord:
    record = repo.get_finding(canonical_uuid(finding_id, "finding_id"))
    if record is None or record.tenant_id != case.tenant_id or record.case_id != case.case_id:
        raise FindingNotFound("Finding not found.", "finding_id", "not_found")
    return record


def _evidence_refs(
    repo: Repository, case: CaseRef, finding: rules.FindingInput
) -> tuple[dict[str, Any], ...]:
    """Contract ``EvidenceRef``s with the stored document hash of each cited record."""
    ids = tuple(dict.fromkeys(c.evidence_id for c in finding.evidence))
    hashes = repo.evidence_hashes(case.tenant_id, case.taxpayer_id, ids) if ids else {}
    refs = []
    for index, citation in enumerate(finding.evidence):
        stored = hashes.get(citation.evidence_id)
        if stored is None:
            # Missing, another tenant's or another taxpayer's: reported alike.
            raise CaseNotFound("Evidence not found.", f"evidence_refs[{index}]", "not_found")
        if citation.document_sha256 is not None and citation.document_sha256 != stored:
            raise InvalidFinding(
                "The cited document hash differs from the stored evidence.",
                f"evidence_refs[{index}].document_sha256",
                "evidence_hash_mismatch",
            )
        refs.append(
            {
                "evidence_id": citation.evidence_id,
                "document_sha256": stored,
                "locator": {"page": citation.page, "record": citation.record},
            }
        )
    return tuple(refs)


def _replay(record: Any, request_digest: str) -> bool:
    if record is None:
        return False
    if record.request_digest != request_digest:
        raise IdempotencyConflict(
            "This idempotency key was used for a different request.",
            "idempotency_key",
            "idempotency_conflict",
        )
    return True


# --- findings -----------------------------------------------------------------------


def record_finding(
    ctx: Context,
    repo: Repository,
    *,
    case_id: str,
    body: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    """Record one finding (or its next revision); a same-key replay returns the stored one."""
    producer = body.get("producer") if isinstance(body, Mapping) else None
    action = policy.FINDING_REVIEW if producer == "officer" else policy.DRAFT_RECORD
    role = _authorize(ctx, action)
    finding = rules.FindingInput.build(body)
    tenant_id = ctx.scope.tenant_id
    case = _case(repo, tenant_id, case_id)
    key = rules.finding_key(tenant_id, case.case_id, idempotency_key)
    request_digest = rules.digest({"case_id": case.case_id, "finding": finding.digest()})
    existing = repo.finding_by_key(key)
    if _replay(existing, request_digest):
        assert existing is not None
        return finding_view(existing, replayed=True)
    refs = _evidence_refs(repo, case, finding)
    finding_id = str(uuid.uuid4())
    lineage = finding.lineage_key(case.tenant_id, case.case_id, finding_id)
    previous = repo.latest_in_lineage(lineage)
    if previous is not None and previous.request_digest == request_digest:
        # The producer reported the same thing again under a new key: nothing new.
        return finding_view(previous, replayed=True)
    revision = previous.revision + 1 if previous is not None else 1
    record = FindingRecord(
        finding_id=finding_id,
        tenant_id=case.tenant_id,
        case_id=case.case_id,
        revision=revision,
        supersedes_finding_id=previous.finding_id if previous is not None else None,
        lineage_key=lineage,
        revision_key=rules.revision_key(lineage, revision),
        kind=finding.kind,
        category=finding.category,
        code=finding.code,
        explanation=finding.explanation,
        evidence_refs=refs,
        unresolved_amount=finding.unresolved_amount,
        confidence=finding.confidence,
        alternative_explanations=finding.alternative_explanations,
        legal_route_status=finding.legal_route_status,
        proposed_action=finding.proposed_action,
        producer=finding.producer,
        producer_version=finding.producer_version,
        subject=finding.subject,
        rule_id=finding.rule_id,
        rule_version=finding.rule_version,
        ruleset_id=finding.ruleset_id,
        ruleset_version=finding.ruleset_version,
        input_digest=finding.input_digest,
        playbook_id=finding.playbook_id,
        playbook_version=finding.playbook_version,
        playbook_content_digest=finding.playbook_content_digest,
        registry_version=finding.registry_version,
        run_id=finding.run_id,
        analysis_provenance=finding.analysis_provenance,
        recorded_by=ctx.scope.user,
        recorder_role=role,
        created_at=ctx.now,
        correlation_id=ctx.correlation_id,
        registration_key=key,
        request_digest=request_digest,
        source_refs=finding.source_refs,
        candidate_route=finding.candidate_route,
    )
    try:
        with repo.atomic():
            repo.insert_finding(record)
    except Duplicate:
        existing = repo.finding_by_key(key)
        if _replay(existing, request_digest):
            assert existing is not None
            return finding_view(existing, replayed=True)
        raise RevisionConflict(
            "Another revision of this finding was recorded first; retry.",
            "finding",
            "finding_revision_conflict",
        ) from None
    return finding_view(record, replayed=False)


def record_rule_signal(
    ctx: Context,
    repo: Repository,
    *,
    case_id: str,
    signal: Mapping[str, Any],
    evidence: Mapping[str, str],
    currency: str = "LSL",
    idempotency_key: str | None = None,
) -> dict[str, Any] | None:
    """Record one TAX-035 rule result; ``no_signal``/``not_applicable`` return ``None``."""
    _authorize(ctx, policy.DRAFT_RECORD)
    translated = rules.from_rule_signal(signal, evidence=evidence, currency=currency)
    if translated is None:
        return None
    body, derived_key = translated
    return record_finding(
        ctx, repo, case_id=case_id, body=body, idempotency_key=idempotency_key or derived_key
    )


def record_playbook_result(
    ctx: Context,
    repo: Repository,
    *,
    case_id: str,
    result: Mapping[str, Any],
    stamp: Mapping[str, Any],
    run_id: str,
    evidence: Mapping[str, str],
    analysis_provenance: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Record a TAX-049 related-party result as findings, all or none."""
    _authorize(ctx, policy.DRAFT_RECORD)
    translated = rules.from_playbook_result(
        result,
        stamp=stamp,
        run_id=run_id,
        evidence=evidence,
        analysis_provenance=analysis_provenance,
    )
    with repo.atomic():
        return [
            record_finding(ctx, repo, case_id=case_id, body=body, idempotency_key=key)
            for body, key in translated
        ]


def _latest_per_officer(feedback: list[FeedbackRecord]) -> dict[tuple[str, str], FeedbackRecord]:
    latest: dict[tuple[str, str], FeedbackRecord] = {}
    for item in sorted(feedback, key=lambda f: f.recorded_at):
        latest[(item.finding_id, item.recorded_by.lower())] = item
    return latest


def _summary(
    finding: FindingRecord, latest: dict[tuple[str, str], FeedbackRecord], viewer: str
) -> dict[str, Any] | None:
    if finding.producer == "officer":
        return None
    counts = {verdict: 0 for verdict in rules.VERDICTS}
    viewer_verdict = None
    for (finding_id, user), item in latest.items():
        if finding_id != finding.finding_id:
            continue
        counts[item.verdict] += 1
        if user == viewer.lower():
            viewer_verdict = item.verdict
    return {**counts, "viewer_verdict": viewer_verdict}


def list_findings(
    ctx: Context, repo: Repository, *, case_id: str, include_superseded: bool = False
) -> list[dict[str, Any]]:
    """The case's findings, current revisions only unless ``include_superseded``."""
    _authorize(ctx, policy.CASE_READ)
    case = _case(repo, ctx.scope.tenant_id, case_id)
    records = repo.case_findings(case.tenant_id, case.case_id)
    superseded_by = {
        r.supersedes_finding_id: r.finding_id for r in records if r.supersedes_finding_id
    }
    latest = _latest_per_officer(repo.case_feedback(case.tenant_id, case.case_id))
    return [
        finding_view(
            record,
            superseded_by=superseded_by.get(record.finding_id),
            feedback_summary=_summary(record, latest, ctx.scope.user),
        )
        for record in sorted(records, key=lambda r: (r.created_at, r.revision, r.finding_id))
        if include_superseded or record.finding_id not in superseded_by
    ]


def get_finding(ctx: Context, repo: Repository, *, case_id: str, finding_id: str) -> dict[str, Any]:
    _authorize(ctx, policy.CASE_READ)
    case = _case(repo, ctx.scope.tenant_id, case_id)
    record = _finding(repo, case, finding_id)
    records = repo.case_findings(case.tenant_id, case.case_id)
    superseded_by = next(
        (r.finding_id for r in records if r.supersedes_finding_id == record.finding_id), None
    )
    latest = _latest_per_officer(repo.case_feedback(case.tenant_id, case.case_id))
    return finding_view(
        record,
        superseded_by=superseded_by,
        feedback_summary=_summary(record, latest, ctx.scope.user),
    )


# --- feedback -----------------------------------------------------------------------


def snapshot(record: FindingRecord) -> dict[str, Any]:
    """``FindingSnapshot`` plus the rule or playbook stamp TAX-070 attributes to."""
    return {
        "kind": record.kind,
        "category": record.category,
        "producer": record.producer,
        "producer_version": record.producer_version,
        "analysis_provenance": record.analysis_provenance,
        "code": record.code,
        "recorded_by": {"user": record.recorded_by, "role": record.recorder_role},
        "revision": record.revision,
        "rule_id": record.rule_id,
        "rule_version": record.rule_version,
        "ruleset_id": record.ruleset_id,
        "ruleset_version": record.ruleset_version,
        "playbook_id": record.playbook_id,
        "playbook_version": record.playbook_version,
        "playbook_content_digest": record.playbook_content_digest,
        "registry_version": record.registry_version,
        "run_id": record.run_id,
    }


def record_feedback(
    ctx: Context,
    repo: Repository,
    *,
    case_id: str,
    finding_id: str,
    body: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    """Record an officer's verdict on a rule or agent finding; it changes nothing else."""
    role = _authorize(ctx, policy.FINDING_REVIEW)
    feedback = rules.FeedbackInput.build(body)
    tenant_id = ctx.scope.tenant_id
    key = rules.feedback_key(tenant_id, ctx.scope.user, idempotency_key)
    case = _case(repo, tenant_id, case_id)
    finding_id = canonical_uuid(finding_id, "finding_id")
    request_digest = rules.digest(
        {"case_id": case.case_id, "feedback": feedback.digest(finding_id)}
    )
    existing = repo.feedback_by_key(key)
    if _replay(existing, request_digest):
        assert existing is not None
        return feedback_view(existing, replayed=True)
    finding = _finding(repo, case, finding_id)
    if finding.producer not in rules.REVIEWABLE_PRODUCERS:
        raise InvalidFinding(
            "Feedback is for rule and agent findings.", "finding_id", "finding_not_reviewable"
        )
    previous = repo.latest_feedback(finding.finding_id, ctx.scope.user)
    previous_id = previous.feedback_id if previous is not None else None
    record = FeedbackRecord(
        feedback_id=str(uuid.uuid4()),
        tenant_id=case.tenant_id,
        case_id=case.case_id,
        finding_id=finding.finding_id,
        verdict=feedback.verdict,
        reason_code=feedback.reason_code,
        reason=feedback.reason,
        modified_explanation=feedback.modified_explanation,
        recorded_by=ctx.scope.user,
        recorder_role=role,
        recorded_at=ctx.now,
        previous_feedback_id=previous_id,
        chain_key=rules.feedback_chain_key(finding.finding_id, ctx.scope.user, previous_id),
        snapshot=snapshot(finding),
        correlation_id=ctx.correlation_id,
        registration_key=key,
        request_digest=request_digest,
    )
    try:
        with repo.atomic():
            repo.insert_feedback(record)
    except Duplicate:
        existing = repo.feedback_by_key(key)
        if _replay(existing, request_digest):
            assert existing is not None
            return feedback_view(existing, replayed=True)
        raise RevisionConflict(
            "You recorded other feedback on this finding at the same time; reload and retry.",
            "finding_id",
            "feedback_conflict",
        ) from None
    return feedback_view(record, replayed=False)


def list_feedback(
    ctx: Context, repo: Repository, *, case_id: str, finding_id: str | None = None
) -> list[dict[str, Any]]:
    """Every feedback on the case (or one finding), oldest first. Staff only."""
    _authorize(ctx, policy.FINDING_REVIEW)
    case = _case(repo, ctx.scope.tenant_id, case_id)
    if finding_id is not None:
        finding_id = _finding(repo, case, finding_id).finding_id
    items = [
        f
        for f in repo.case_feedback(case.tenant_id, case.case_id)
        if finding_id is None or f.finding_id == finding_id
    ]
    return [feedback_view(f) for f in sorted(items, key=lambda f: (f.recorded_at, f.feedback_id))]


def feedback_for_review(
    ctx: Context, repo: Repository, *, since: datetime.datetime | None = None
) -> dict[str, Any]:
    """The tenant's finding feedback for the TAX-070 loop (ai/workflows/outcome_feedback.py).

    Each item carries the verdict, reason code and the judged component in the
    TAX-070 provenance shape (``rule``: rule_id + ruleset_version; ``playbook``:
    name, version, content_digest, registry_version). ``current`` is false when the
    same officer later recorded newer feedback on that finding. Staff or service
    (``case.read``); never taxpayers.
    """
    _authorize(ctx, policy.CASE_READ)
    items = sorted(
        repo.tenant_feedback(ctx.scope.tenant_id, since),
        key=lambda f: (f.recorded_at, f.feedback_id),
    )
    replaced = {f.previous_feedback_id for f in items if f.previous_feedback_id}
    return {
        "tenant_id": ctx.scope.tenant_id,
        "items": [
            {
                "tenant_id": f.tenant_id,
                "case_id": f.case_id,
                "finding_id": f.finding_id,
                "feedback_id": f.feedback_id,
                "previous_feedback_id": f.previous_feedback_id,
                "current": f.feedback_id not in replaced,
                "verdict": f.verdict,
                "reason_code": f.reason_code,
                "recorded_at": rules.iso(f.recorded_at),
                "finding": _contract_snapshot(f.snapshot),
                "provenance": [s] if (s := rules.component_stamp(f.snapshot)) else [],
            }
            for f in items
        ],
        "changes_findings": False,
    }


# --- views --------------------------------------------------------------------------

FINDING_CONTRACT_KEYS = (
    "finding_id",
    "case_id",
    "tenant_id",
    "kind",
    "category",
    "explanation",
    "evidence_refs",
    "unresolved_amount",
    "confidence",
    "alternative_explanations",
    "legal_route",
    "proposed_action",
    "producer",
    "producer_version",
    "created_at",
    "analysis_provenance",
    "feedback_summary",
    "source_refs",
    "candidate_route",
)
SNAPSHOT_KEYS = ("kind", "category", "producer", "producer_version", "analysis_provenance")


def _contract_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    return {k: value.get(k) for k in SNAPSHOT_KEYS}


def finding_view(
    record: FindingRecord,
    *,
    replayed: bool | None = None,
    superseded_by: str | None = None,
    feedback_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Contract ``Finding`` fields plus Vati revision and producer fields.

    ``contract_finding`` keeps only the contract fields (TAX-090 serves those).
    """
    view: dict[str, Any] = {
        "finding_id": record.finding_id,
        "case_id": record.case_id,
        "tenant_id": record.tenant_id,
        "kind": record.kind,
        "category": record.category,
        "explanation": record.explanation,
        "evidence_refs": [dict(r) for r in record.evidence_refs],
        "unresolved_amount": dict(record.unresolved_amount) if record.unresolved_amount else None,
        "confidence": record.confidence,
        "alternative_explanations": list(record.alternative_explanations),
        "legal_route": {"status": record.legal_route_status, "citation": None, "verified_by": None},
        "proposed_action": {"type": record.proposed_action, "requires_human_approval": True},
        "producer": record.producer,
        "producer_version": record.producer_version,
        "created_at": rules.iso(record.created_at),
        "analysis_provenance": record.analysis_provenance,
        "feedback_summary": feedback_summary,
        "source_refs": [dict(r) for r in record.source_refs],
        "candidate_route": dict(record.candidate_route) if record.candidate_route else None,
        "code": record.code,
        "subject": record.subject,
        "recorded_by": {"user": record.recorded_by, "role": record.recorder_role},
        "revision": record.revision,
        "supersedes_finding_id": record.supersedes_finding_id,
        "superseded_by": superseded_by,
        "rule": (
            {
                "rule_id": record.rule_id,
                "rule_version": record.rule_version,
                "ruleset_id": record.ruleset_id,
                "ruleset_version": record.ruleset_version,
                "input_digest": record.input_digest,
            }
            if record.rule_id
            else None
        ),
        "playbook": (
            {
                "name": record.playbook_id,
                "version": record.playbook_version,
                "content_digest": record.playbook_content_digest,
                "registry_version": record.registry_version,
                "run_id": record.run_id,
            }
            if record.playbook_id
            else None
        ),
    }
    if replayed is not None:
        view["replayed"] = replayed
    return view


def contract_finding(view: Mapping[str, Any]) -> dict[str, Any]:
    return {k: view[k] for k in FINDING_CONTRACT_KEYS if k in view}


def feedback_view(record: FeedbackRecord, *, replayed: bool | None = None) -> dict[str, Any]:
    """``FindingFeedback`` fields. ``recorded_by`` names the session user and role;
    TAX-090 maps the user to the contract ``officer_id``."""
    view: dict[str, Any] = {
        "feedback_id": record.feedback_id,
        "finding_id": record.finding_id,
        "case_id": record.case_id,
        "tenant_id": record.tenant_id,
        "verdict": record.verdict,
        "reason_code": record.reason_code,
        "reason": record.reason,
        "modified_explanation": record.modified_explanation,
        "recorded_by": {"user": record.recorded_by, "role": record.recorder_role},
        "recorded_at": rules.iso(record.recorded_at),
        "previous_feedback_id": record.previous_feedback_id,
        "finding": _contract_snapshot(record.snapshot),
        "changes_finding": False,
    }
    if replayed is not None:
        view["replayed"] = replayed
    return view


def contract_feedback(view: Mapping[str, Any], *, officer_id: str) -> dict[str, Any]:
    out = {k: v for k, v in view.items() if k != "replayed"}
    out["recorded_by"] = {"officer_id": officer_id, "role": view["recorded_by"]["role"]}
    return out


# --- memory repository --------------------------------------------------------------


@dataclass
class MemoryRepository:
    """Same unique keys as the DocTypes: registration and revision keys of findings,
    registration and chain keys of feedback."""

    cases: dict[str, CaseRef] = field(default_factory=dict)
    # evidence_id -> (tenant_id, taxpayer_id, sha256)
    evidence: dict[str, tuple[str, str | None, str]] = field(default_factory=dict)
    findings: dict[str, FindingRecord] = field(default_factory=dict)
    feedback: dict[str, FeedbackRecord] = field(default_factory=dict)

    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        saved = (dict(self.findings), dict(self.feedback))
        try:
            yield
        except BaseException:
            self.findings, self.feedback = saved
            raise

    def get_case(self, tenant_id: str, case_id: str) -> CaseRef | None:
        return self.cases.get(case_id)

    def evidence_hashes(
        self, tenant_id: str, taxpayer_id: str | None, evidence_ids: tuple[str, ...]
    ) -> dict[str, str]:
        out = {}
        for evidence_id in evidence_ids:
            row = self.evidence.get(evidence_id)
            if row and row[0] == tenant_id and (not taxpayer_id or row[1] == taxpayer_id):
                out[evidence_id] = row[2]
        return out

    def get_finding(self, finding_id: str) -> FindingRecord | None:
        return self.findings.get(finding_id)

    def finding_by_key(self, registration_key: str) -> FindingRecord | None:
        return next(
            (f for f in self.findings.values() if f.registration_key == registration_key), None
        )

    def latest_in_lineage(self, lineage_key: str) -> FindingRecord | None:
        rows = [f for f in self.findings.values() if f.lineage_key == lineage_key]
        return max(rows, key=lambda f: f.revision) if rows else None

    def insert_finding(self, record: FindingRecord) -> None:
        for other in self.findings.values():
            if other.registration_key == record.registration_key:
                raise Duplicate("registration_key")
            if other.revision_key == record.revision_key:
                raise Duplicate("revision_key")
        self.findings[record.finding_id] = replace(record)

    def case_findings(self, tenant_id: str, case_id: str) -> list[FindingRecord]:
        return [
            f for f in self.findings.values() if f.tenant_id == tenant_id and f.case_id == case_id
        ]

    def feedback_by_key(self, registration_key: str) -> FeedbackRecord | None:
        return next(
            (f for f in self.feedback.values() if f.registration_key == registration_key), None
        )

    def latest_feedback(self, finding_id: str, user: str) -> FeedbackRecord | None:
        rows = [
            f
            for f in self.feedback.values()
            if f.finding_id == finding_id and f.recorded_by.lower() == user.lower()
        ]
        return max(rows, key=lambda f: (f.recorded_at, f.feedback_id)) if rows else None

    def insert_feedback(self, record: FeedbackRecord) -> None:
        for other in self.feedback.values():
            if other.registration_key == record.registration_key:
                raise Duplicate("registration_key")
            if other.chain_key == record.chain_key:
                raise Duplicate("chain_key")
        self.feedback[record.feedback_id] = record

    def case_feedback(self, tenant_id: str, case_id: str) -> list[FeedbackRecord]:
        return [
            f for f in self.feedback.values() if f.tenant_id == tenant_id and f.case_id == case_id
        ]

    def tenant_feedback(
        self, tenant_id: str, since: datetime.datetime | None
    ) -> list[FeedbackRecord]:
        return [
            f
            for f in self.feedback.values()
            if f.tenant_id == tenant_id and (since is None or f.recorded_at >= since)
        ]
