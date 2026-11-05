"""Objection and appeal workflow with policy checks (TAX-069), independent of Frappe.

``store.py`` binds the ports below to Frappe; tests use the in-memory classes
at the end of this module together with the TAX-046 ``MemoryRepository``.

- ``lodge``: the taxpayer (or an auditor or supervisor recording it on the
  taxpayer's behalf) objects to an approved decision, or appeals the reviewed
  outcome of an objection. Cited evidence must already belong to the case's
  taxpayer in the tenant; new evidence is uploaded through TAX-032, never by
  changing what the decision cited.
- ``review``: a supervisor who did not propose or approve the decision, did not
  lodge the challenge and (for an appeal) did not review the objection records
  one outcome. An overturned outcome raises a review-only signal about the
  original decision and asks for a new human decision. It changes nothing else.
- ``history``: the full chain ``decision -> objection -> appeal -> final
  outcome`` with a check that the decision is unchanged since each challenge.

Every step, read and refusal is sealed into the TAX-059 ledger (subject
``challenge``).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, NoReturn, Protocol, TypeVar

from vati_tax.audit import ledger
from vati_tax.audit import service as audit
from vati_tax.audit.ledger import LedgerStore
from vati_tax.challenges import rules
from vati_tax.challenges.rules import ChallengeRecord
from vati_tax.decisions.engine import DecisionRecord
from vati_tax.identity import policy
from vati_tax.ids.canonical import IdempotencyConflict, MappingError, canonical_uuid

T = TypeVar("T")
SUBJECT = "challenge"


class ChallengeStore(Protocol):
    def atomic(self) -> contextlib.AbstractContextManager[None]: ...

    def get(self, tenant_id: str, challenge_id: str) -> ChallengeRecord | None: ...

    def for_decision(self, tenant_id: str, decision_id: str) -> list[ChallengeRecord]:
        """Every challenge of one decision, oldest first."""
        ...

    def insert(self, record: ChallengeRecord) -> None:
        """Store a new record; raise ``ledger.Duplicate`` on a clashing key."""
        ...

    def update(self, record: ChallengeRecord, expected_version: int) -> bool: ...


class DecisionSource(Protocol):
    """Read-only view of TAX-046 decisions and TAX-032 evidence ownership."""

    def get_decision(self, decision_id: str) -> DecisionRecord | None: ...

    def known_evidence(
        self, tenant_id: str, taxpayer_id: str | None, evidence_ids: tuple[str, ...]
    ) -> set[str]: ...


@dataclass(frozen=True)
class Ports:
    challenges: ChallengeStore
    decisions: DecisionSource
    ledger: LedgerStore


def _taxpayer_only(scope: policy.TenantScope) -> bool:
    return scope.roles == {policy.TAXPAYER}


def _append(
    ctx: audit.Context,
    ports: Ports,
    *,
    action: str,
    outcome: str,
    record: ChallengeRecord,
    payload: Any,
    privilege: str,
    reason: str | None = None,
    target_actor: str | None = None,
) -> None:
    ledger.append(
        ports.ledger,
        ledger.Unsealed(
            event_id=ledger.new_event_id(),
            tenant_id=ctx.scope.tenant_id,
            action=action,
            outcome=outcome,
            actor=ctx.scope.user,
            actor_role=audit.acting_role(ctx.scope, privilege),
            occurred_at=ctx.now,
            correlation_id=ctx.correlation_id,
            subject_type=SUBJECT,
            subject_id=record.challenge_id,
            payload_hash=ledger.digest(payload),
            case_id=record.case_id,
            taxpayer_id=record.taxpayer_id,
            reason=reason,
            target_actor=target_actor,
        ),
    )


def _refuse(
    ctx: audit.Context,
    ports: Ports,
    *,
    attempted: str,
    subject_id: str,
    case_id: str | None,
    exc: MappingError,
) -> NoReturn:
    audit.record_denied(
        ctx,
        ports.ledger,
        attempted_action=attempted,
        subject_type=SUBJECT,
        subject_id=subject_id,
        code=exc.field_errors[0]["code"] if exc.field_errors else exc.code,
        case_id=case_id,
    )
    raise exc


def _guard(
    ctx: audit.Context,
    ports: Ports,
    *,
    action: str,
    subject_id: str,
    case_id: str | None,
    fn: Callable[[], T],
) -> T:
    return audit.guarded(
        ctx,
        ports.ledger,
        attempted_action=action,
        subject_type=SUBJECT,
        subject_id=subject_id,
        case_id=case_id,
        fn=fn,
    )


def _visible_decision(ctx: audit.Context, ports: Ports, decision_id: str | None) -> DecisionRecord:
    """The decision if it is in the tenant and, for a taxpayer, its own."""
    decision = ports.decisions.get_decision(decision_id) if decision_id else None
    if (
        decision is None
        or decision.tenant_id != ctx.scope.tenant_id
        or (_taxpayer_only(ctx.scope) and decision.taxpayer_id not in ctx.scope.taxpayer_ids)
    ):
        raise rules.ChallengeNotFound("Decision not found.", "decision_id", "not_found")
    return decision


def _load(ctx: audit.Context, ports: Ports, challenge_id: object) -> ChallengeRecord:
    cid = canonical_uuid(challenge_id, "challenge_id")
    record = ports.challenges.get(ctx.scope.tenant_id, cid)
    if (
        record is None
        or record.tenant_id != ctx.scope.tenant_id
        or (_taxpayer_only(ctx.scope) and record.taxpayer_id not in ctx.scope.taxpayer_ids)
    ):
        raise rules.ChallengeNotFound("Challenge not found.", "challenge_id", "not_found")
    return record


# --- lodge --------------------------------------------------------------------------


def lodge(
    ctx: audit.Context, ports: Ports, *, body: Mapping[str, Any], idempotency_key: str
) -> dict[str, Any]:
    """Lodge an objection or appeal. Replaying the same key returns the stored one."""
    request = rules.LodgeRequest.build(body, idempotency_key)
    cid = rules.challenge_id(ctx.scope.tenant_id, request.idempotency_key)
    _guard(
        ctx,
        ports,
        action=policy.CHALLENGE_LODGE,
        subject_id=cid,
        case_id=None,
        fn=lambda: policy.authorize_action(
            ctx.scope,
            policy.CHALLENGE_LODGE,
            # Role check only here; the decision's taxpayer is checked below.
            taxpayer_id=next(iter(ctx.scope.taxpayer_ids), None),
        ),
    )
    existing = ports.challenges.get(ctx.scope.tenant_id, cid)
    if existing is not None:
        if (
            existing.request_digest != rules.digest(request.payload())
            or existing.lodged_by.lower() != ctx.scope.user.lower()
        ):
            raise IdempotencyConflict(
                "This idempotency key was used for a different request.",
                "idempotency_key",
                "idempotency_conflict",
            )
        return {
            **rules.challenge_view(existing, for_taxpayer=_taxpayer_only(ctx.scope)),
            "replayed": True,
        }
    case_id: str | None = None
    try:
        parent: ChallengeRecord | None = None
        if request.stage == "appeal":
            parent = _load(ctx, ports, request.parent_challenge_id)
            decision = _visible_decision(ctx, ports, parent.decision_id)
        else:
            decision = _visible_decision(ctx, ports, request.decision_id)
        case_id = decision.case_id
        if not decision.taxpayer_id:
            raise rules.ChallengeConflict(
                "The decision's case has no taxpayer to object.", "decision_id", "no_taxpayer"
            )
        if decision.status != "approved":
            raise rules.ChallengeConflict(
                "Only an approved decision can be challenged.",
                "decision_id",
                "decision_not_final",
            )
        others = ports.challenges.for_decision(ctx.scope.tenant_id, decision.decision_id)
        if any(c.status == "lodged" for c in others):
            raise rules.ChallengeConflict(
                "A challenge to this decision is still waiting for review.",
                "decision_id",
                "challenge_open",
            )
        if parent is None:
            if any(c.stage == "objection" for c in others):
                raise rules.ChallengeConflict(
                    "This decision already has an objection; appeal its outcome instead.",
                    "decision_id",
                    "objection_exists",
                )
        else:
            if parent.stage != "objection":
                raise rules.ChallengeConflict(
                    "An appeal follows an objection.", "parent_challenge_id", "not_objection"
                )
            if parent.outcome not in rules.APPEALABLE:
                raise rules.ChallengeConflict(
                    "Only an upheld or partly overturned objection can be appealed.",
                    "parent_challenge_id",
                    "not_appealable",
                )
            if any(c.parent_challenge_id == parent.challenge_id for c in others):
                raise rules.ChallengeConflict(
                    "This objection has already been appealed.",
                    "parent_challenge_id",
                    "appeal_exists",
                )
        known = ports.decisions.known_evidence(
            ctx.scope.tenant_id, decision.taxpayer_id, request.evidence_ids
        )
        if known != set(request.evidence_ids):
            raise rules.ChallengeNotFound("Evidence not found.", "evidence_ids", "not_found")
    except MappingError as exc:
        _refuse(
            ctx,
            ports,
            attempted=policy.CHALLENGE_LODGE,
            subject_id=cid,
            case_id=case_id,
            exc=exc,
        )
    record = ChallengeRecord(
        challenge_id=cid,
        tenant_id=ctx.scope.tenant_id,
        taxpayer_id=decision.taxpayer_id,
        case_id=decision.case_id,
        decision_id=decision.decision_id,
        stage=request.stage,
        parent_challenge_id=request.parent_challenge_id,
        grounds=request.grounds,
        evidence_ids=request.evidence_ids,
        route_reference=request.route_reference,
        decision_digest=rules.decision_digest(decision),
        request_digest=rules.digest(request.payload()),
        lodged_by=ctx.scope.user,
        lodger_role=audit.acting_role(ctx.scope, policy.CHALLENGE_LODGE),
        lodged_at=rules.utc(ctx.now),
    )
    with ports.challenges.atomic():
        ports.challenges.insert(record)
        _append(
            ctx,
            ports,
            action="challenge.lodged",
            outcome=record.stage,
            record=record,
            payload=request.payload(),
            privilege=policy.CHALLENGE_LODGE,
        )
    return {
        **rules.challenge_view(record, for_taxpayer=_taxpayer_only(ctx.scope)),
        "replayed": False,
    }


# --- review -------------------------------------------------------------------------


def review(
    ctx: audit.Context,
    ports: Ports,
    *,
    challenge_id: object,
    expected_version: object,
    outcome: object,
    reason: object,
) -> dict[str, Any]:
    """Record the reviewed outcome. The decision and its evidence stay as they were."""
    record = _load(ctx, ports, challenge_id)
    decision = ports.decisions.get_decision(record.decision_id)
    parent = (
        ports.challenges.get(ctx.scope.tenant_id, record.parent_challenge_id)
        if record.parent_challenge_id
        else None
    )

    def authorise() -> None:
        policy.authorize_action(ctx.scope, policy.CHALLENGE_REVIEW)
        user = ctx.scope.user.lower()
        involved = {
            (decision.proposed_by if decision else "") or "",
            (decision.reviewed_by if decision else "") or "",
            record.lodged_by,
            (parent.reviewed_by if parent else "") or "",
        }
        if user in {name.lower() for name in involved if name}:
            raise policy.ActionDenied(
                "You took part in this decision or challenge and cannot review it"
            )

    _guard(
        ctx,
        ports,
        action=policy.CHALLENGE_REVIEW,
        subject_id=record.challenge_id,
        case_id=record.case_id,
        fn=authorise,
    )
    try:
        rules.check_reviewable(record, expected_version)
        result = rules.check_outcome(outcome)
        text = rules.required_text({"reason": reason}, "reason", rules.REASON_MIN, rules.REASON_MAX)
        if decision is None or rules.decision_digest(decision) != record.decision_digest:
            raise rules.ChallengeConflict(
                "The original decision no longer matches the one challenged.",
                "decision_id",
                "decision_changed",
            )
    except MappingError as exc:
        _refuse(
            ctx,
            ports,
            attempted=policy.CHALLENGE_REVIEW,
            subject_id=record.challenge_id,
            case_id=record.case_id,
            exc=exc,
        )
    reviewed = replace(
        record,
        status="reviewed",
        version=record.version + 1,
        reviewed_by=ctx.scope.user,
        reviewed_at=rules.utc(ctx.now),
        outcome=result,
        review_reason=text,
    )
    with ports.challenges.atomic():
        if not ports.challenges.update(reviewed, record.version):
            raise rules.ChallengeConflict(
                "The challenge changed since you loaded it.", "expected_version", "stale_version"
            )
        _append(
            ctx,
            ports,
            action="challenge.reviewed",
            outcome=result,
            record=reviewed,
            payload={"outcome": result, "version": reviewed.version},
            privilege=policy.CHALLENGE_REVIEW,
            reason=text,
            # The signal (TAX-059) is about the decision's proposer, who never sees it.
            target_actor=decision.proposed_by,
        )
    return rules.challenge_view(reviewed)


# --- read ---------------------------------------------------------------------------


def history(ctx: audit.Context, ports: Ports, *, decision_id: object) -> dict[str, Any]:
    """The decision and every challenge to it, with the final reviewed outcome."""
    did = canonical_uuid(decision_id, "decision_id")
    decision = _visible_decision(ctx, ports, did)
    _guard(
        ctx,
        ports,
        action=policy.CHALLENGE_READ,
        subject_id=decision.decision_id,
        case_id=decision.case_id,
        fn=lambda: policy.authorize_action(
            ctx.scope, policy.CHALLENGE_READ, taxpayer_id=decision.taxpayer_id
        ),
    )
    for_taxpayer = _taxpayer_only(ctx.scope)
    chain = ports.challenges.for_decision(ctx.scope.tenant_id, decision.decision_id)
    current = rules.decision_digest(decision)
    reviewed = [c for c in chain if c.status == "reviewed"]
    final = reviewed[-1] if reviewed and not any(c.status == "lodged" for c in chain) else None
    audit.record_read(
        ctx,
        ports.ledger,
        action="challenge.read",
        subject_type=SUBJECT,
        subject_id=decision.decision_id,
        case_id=decision.case_id,
        taxpayer_id=decision.taxpayer_id,
        detail={"challenges": len(chain)},
    )
    return {
        "decision": {
            "decision_id": decision.decision_id,
            "case_id": decision.case_id,
            "status": decision.status,
            "outcome": decision.outcome,
            "proposed_at": rules.iso(decision.proposed_at),
            "reviewed_at": rules.iso(decision.reviewed_at),
            "decision_digest": current,
        },
        "challenges": [rules.challenge_view(c, for_taxpayer=for_taxpayer) for c in chain],
        "original_decision_unchanged": all(c.decision_digest == current for c in chain),
        "overturned": [c.challenge_id for c in reviewed if c.outcome in rules.OVERTURNED],
        "final_outcome": None
        if final is None
        else {"challenge_id": final.challenge_id, "stage": final.stage, "outcome": final.outcome},
        "legal_route_verified": False,
        "legal_note": rules.LEGAL_NOTE,
    }


# --- in-memory port -----------------------------------------------------------------


@dataclass
class MemoryChallenges:
    rows: dict[str, ChallengeRecord] = field(default_factory=dict)

    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        snapshot = dict(self.rows)
        try:
            yield
        except BaseException:
            self.rows = snapshot
            raise

    def get(self, tenant_id: str, challenge_id: str) -> ChallengeRecord | None:
        record = self.rows.get(challenge_id)
        return record if record and record.tenant_id == tenant_id else None

    def for_decision(self, tenant_id: str, decision_id: str) -> list[ChallengeRecord]:
        return sorted(
            (
                r
                for r in self.rows.values()
                if r.tenant_id == tenant_id and r.decision_id == decision_id
            ),
            key=lambda r: (r.lodged_at, r.stage != "objection"),
        )

    def insert(self, record: ChallengeRecord) -> None:
        if record.challenge_id in self.rows:
            raise ledger.Duplicate("challenge_id")
        for other in self.rows.values():
            if other.decision_id != record.decision_id:
                continue
            if record.stage == "objection" and other.stage == "objection":
                raise ledger.Duplicate("objection_key")
            if record.parent_challenge_id and other.parent_challenge_id == (
                record.parent_challenge_id
            ):
                raise ledger.Duplicate("appeal_key")
        self.rows[record.challenge_id] = record

    def update(self, record: ChallengeRecord, expected_version: int) -> bool:
        current = self.rows.get(record.challenge_id)
        if current is None or current.version != expected_version:
            return False
        self.rows[record.challenge_id] = record
        return True
