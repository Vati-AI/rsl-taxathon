"""Institutional knowledge index and permission-scoped retrieval (TAX-068).

Indexes only *reviewed* outcomes: TAX-046 decisions with status ``approved``, reviewed
by a supervisor who did not propose them. Each indexed record keeps the officer's
reasoning, the alternative explanations that were accepted, the cited evidence
(contract ``EvidenceRef``) and the legal authority versions recorded at decision time.

Retrieval is permission-scoped before anything is read:

- the caller must pass the TAX-053 ``check_read_access`` rule (authenticated, member of
  the tenant, an officer or service role, never a taxpayer);
- every candidate case must pass the caller-supplied ``can_read_case`` check (the
  Vati/Frappe case permission layer). It is required and denies by default;
- ``restricted`` records (for example serious-case escalations) are visible to
  supervisors only; the active case itself is never returned;
- other taxpayers' identifiers are not returned.

Every result is labelled institutional memory, ``binding: False``. Each authority
version cited in an old decision is re-checked against the current TAX-052 registry
(still in force, superseded, unverified, unknown), so a stale citation cannot be
mistaken for current law. Reasoning text is untrusted data: it is searched and
excerpted, never executed, and text that reads like an instruction is flagged.
"""

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Final

from ai.tools.evidence_search import (
    SearchDenied,
    check_read_access,
    looks_like_instruction,
)
from ai.workflows.legal_routes import LegalRouteRegistry

INDEX_VERSION: Final = "tax068-knowledge-v1"
MAX_RECORDS: Final = 10_000
MAX_RESULTS: Final = 10
MAX_TERMS: Final = 12
EXCERPT_CHARS: Final = 400
NATURE: Final = "institutional_memory"
NOTICE: Final = (
    "Prior reviewed reasoning offered for consistency. It is institutional memory, not "
    "binding precedent and not legal authority; the current case must be decided on its "
    "own evidence and on currently verified law."
)
OUTCOMES: Final = frozenset(
    {
        "close_no_further_action",
        "close_after_correction",
        "request_further_evidence",
        "continue_investigation",
        "escalate_to_supervisor",
    }
)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_WORD = re.compile(r"[a-z0-9]{3,}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
CaseReadCheck = Callable[[Mapping[str, object], str, str], bool]


class KnowledgeInputError(ValueError):
    """A record or query is malformed; carries a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class KnowledgeRecord:
    record_id: str
    tenant_id: str
    case_id: str
    decision_id: str
    decided_on: date
    outcome: str
    finding_codes: frozenset[str]
    reasoning: str
    accepted_alternatives: tuple[dict[str, object], ...]
    evidence_refs: tuple[dict[str, object], ...]
    authorities: tuple[tuple[str, str], ...]
    restricted: bool
    words: frozenset[str]
    instruction_like: bool


def _clean(text: object, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    return " ".join(_CONTROL.sub(" ", text).split())[:limit]


def _id(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise KnowledgeInputError(f"invalid_{key}")
    return value


def _evidence_ref(raw: object) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        raise KnowledgeInputError("invalid_evidence_ref")
    evidence_id = raw.get("evidence_id")
    sha = raw.get("document_sha256")
    locator = raw.get("locator")
    if not isinstance(evidence_id, str) or not _ID.fullmatch(evidence_id):
        raise KnowledgeInputError("invalid_evidence_ref")
    if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
        raise KnowledgeInputError("invalid_evidence_ref")
    if not isinstance(locator, Mapping) or (
        locator.get("page") is None and locator.get("record") is None
    ):
        raise KnowledgeInputError("invalid_evidence_ref")
    return {
        "evidence_id": evidence_id,
        "document_sha256": sha,
        "locator": {"page": locator.get("page"), "record": locator.get("record")},
    }


def _record(raw: object, tenant_id: str) -> KnowledgeRecord | None:
    """Validate one decision record; ``None`` if it is not reviewed knowledge."""
    if not isinstance(raw, Mapping):
        raise KnowledgeInputError("invalid_record")
    if raw.get("tenant_id") != tenant_id:
        raise KnowledgeInputError("tenant_mismatch")
    if raw.get("status") != "approved":
        return None  # drafts, proposals, rejected and withdrawn decisions are not knowledge
    reviewer, proposer = raw.get("reviewed_by"), raw.get("proposed_by")
    if (
        raw.get("reviewer_role") != "supervisor"
        or not isinstance(reviewer, str)
        or not reviewer
        or reviewer == proposer
    ):
        return None  # not an independently reviewed outcome
    outcome = raw.get("outcome")
    if outcome not in OUTCOMES:
        raise KnowledgeInputError("invalid_outcome")
    try:
        decided_on = date.fromisoformat(str(raw.get("decided_on")))
    except ValueError as error:
        raise KnowledgeInputError("invalid_decided_on") from error
    codes = raw.get("finding_codes", [])
    if not isinstance(codes, list) or not all(
        isinstance(c, str) and _CODE.fullmatch(c) for c in codes
    ):
        raise KnowledgeInputError("invalid_finding_codes")
    reasoning = _clean(raw.get("reasoning"), 4_000)
    if len(reasoning) < 20:
        raise KnowledgeInputError("reasoning_required")
    alternatives = []
    for alt in raw.get("accepted_alternatives", []) or []:
        if (
            not isinstance(alt, Mapping)
            or not isinstance(alt.get("code"), str)
            or not _CODE.fullmatch(str(alt["code"]))
        ):
            raise KnowledgeInputError("invalid_alternative")
        alternatives.append(
            {
                "code": alt["code"],
                "summary": _clean(alt.get("summary"), 400),
                "evidence_refs": [_evidence_ref(r) for r in alt.get("evidence_refs", []) or []],
            }
        )
    refs = tuple(_evidence_ref(r) for r in raw.get("evidence_refs", []) or [])
    if not refs:
        raise KnowledgeInputError("evidence_refs_required")
    authorities = []
    for item in raw.get("authorities", []) or []:
        if not isinstance(item, Mapping):
            raise KnowledgeInputError("invalid_authority")
        authorities.append((_id(item, "authority_id"), _id(item, "version")))
    words = frozenset(
        _WORD.findall(
            " ".join([reasoning.lower(), *(str(a["summary"]).lower() for a in alternatives)])
        )
    )
    return KnowledgeRecord(
        record_id=_id(raw, "record_id"),
        tenant_id=tenant_id,
        case_id=_id(raw, "case_id"),
        decision_id=_id(raw, "decision_id"),
        decided_on=decided_on,
        outcome=str(outcome),
        finding_codes=frozenset(codes),
        reasoning=reasoning,
        accepted_alternatives=tuple(alternatives),
        evidence_refs=refs,
        authorities=tuple(authorities),
        restricted=raw.get("restricted") is True,
        words=words,
        instruction_like=looks_like_instruction(reasoning),
    )


@dataclass(frozen=True)
class KnowledgeIndex:
    tenant_id: str
    records: tuple[KnowledgeRecord, ...]
    skipped_unreviewed: int
    version: str = INDEX_VERSION


def build_index(raw_records: Iterable[object], *, tenant_id: str) -> KnowledgeIndex:
    """Index one tenant's reviewed decisions; unreviewed ones are counted, not indexed."""
    rows = list(raw_records)
    if len(rows) > MAX_RECORDS:
        raise KnowledgeInputError("record_budget_exceeded")
    kept: dict[str, KnowledgeRecord] = {}
    skipped = 0
    for raw in rows:
        record = _record(raw, tenant_id)
        if record is None:
            skipped += 1
            continue
        if record.record_id in kept and kept[record.record_id] != record:
            raise KnowledgeInputError("conflicting_record")
        kept[record.record_id] = record
    return KnowledgeIndex(
        tenant_id, tuple(sorted(kept.values(), key=lambda r: r.record_id)), skipped
    )


def authority_status(
    registry: LegalRouteRegistry, authority_id: str, version: str, as_of: date
) -> dict[str, object]:
    """Current standing of an authority version cited in an older decision."""
    entry = registry.get(authority_id, version)
    if entry is None:
        status = "unknown_to_registry"
    elif not entry.is_effective(as_of):
        status = (
            "no_longer_in_force"
            if entry.effective_to and as_of > entry.effective_to
            else "not_yet_in_force"
        )
    elif not entry.is_verified:
        status = "unverified"
    else:
        status = "in_force_verified"
    newer = sorted(
        e.version
        for e in registry.entries
        if e.authority_id == authority_id and e.version != version and e.is_effective(as_of)
    )
    return {
        "authority_id": authority_id,
        "version_at_decision": version,
        "current_status": status,
        "versions_in_force_now": newer,
        "registry_version": registry.registry_version,
    }


def retrieve(
    index: KnowledgeIndex,
    *,
    actor: Mapping[str, object],
    can_read_case: CaseReadCheck,
    current_case_id: str,
    finding_codes: Iterable[str],
    terms: Iterable[str] = (),
    registry: LegalRouteRegistry,
    as_of: date,
    limit: int = 5,
) -> dict[str, object]:
    """Return authorised, cited prior cases relevant to the active case's findings."""
    check_read_access(actor, index.tenant_id)  # raises SearchDenied before any record is read
    if not callable(can_read_case):
        raise SearchDenied("case_permission_check_required")
    codes = {c for c in finding_codes if isinstance(c, str) and _CODE.fullmatch(c)}
    words = {w for t in terms if isinstance(t, str) for w in _WORD.findall(t.lower())}
    if not codes and not words:
        raise KnowledgeInputError("query_required")
    if len(words) > MAX_TERMS:
        raise KnowledgeInputError("too_many_terms")
    if not 1 <= limit <= MAX_RESULTS:
        raise KnowledgeInputError("invalid_limit")
    roles = actor.get("roles")
    is_supervisor = isinstance(roles, list) and "supervisor" in roles

    withheld = 0
    scored: list[tuple[int, KnowledgeRecord]] = []
    for record in index.records:
        if record.case_id == current_case_id:
            continue
        relevance = 3 * len(record.finding_codes & codes) + len(record.words & words)
        if relevance == 0:
            continue
        if record.restricted and not is_supervisor:
            withheld += 1
            continue
        try:
            allowed = can_read_case(actor, index.tenant_id, record.case_id) is True
        except Exception:
            allowed = False  # a failing permission check denies
        if not allowed:
            withheld += 1
            continue
        scored.append((relevance, record))
    scored.sort(key=lambda item: (-item[0], -item[1].decided_on.toordinal(), item[1].record_id))

    results = []
    for relevance, record in scored[:limit]:
        results.append(
            {
                "nature": NATURE,
                "binding": False,
                "record_id": record.record_id,
                "case_id": record.case_id,
                "decision_id": record.decision_id,
                "decided_on": record.decided_on.isoformat(),
                "outcome": record.outcome,
                "matched_finding_codes": sorted(record.finding_codes & codes),
                "relevance": relevance,
                "reasoning_excerpt": record.reasoning[:EXCERPT_CHARS],
                "instruction_like_text_flagged": record.instruction_like,
                "accepted_alternatives": list(record.accepted_alternatives),
                "citations": list(record.evidence_refs),
                "authorities": [
                    authority_status(registry, authority_id, version, as_of)
                    for authority_id, version in record.authorities
                ],
                "notice": NOTICE,
            }
        )
    return {
        "index_version": index.version,
        "tenant_id": index.tenant_id,
        "query": {"finding_codes": sorted(codes), "terms": sorted(words)},
        "results": results,
        "withheld_by_permission": withheld,
        "nature": NATURE,
        "binding": False,
        "notice": NOTICE,
    }
