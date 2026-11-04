"""Auditor action observation and supervisor-reviewed workflow proposals (TAX-064).

Reads one tenant's TAX-059 audit-ledger entries (``vati_tax.audit.ledger.entry_view``
shape, oldest first), keeps only *approved* work, and turns each case into an
ordered trace of steps:

- ``evidence_request.recorded`` -> ``request_evidence`` (an officer-authorised request);
- ``decision.approved`` -> the approved outcome from the optional ``detail`` field
  that the Vati layer joins from the approved decision (``decision_approved`` if absent).
  An approved decision whose execution later failed is dropped from the trace.

Reads, refusals, overrides, drafts, proposals, rejections and withdrawals never become
steps: the observer learns only from actions a supervisor approved or authorised.

Cases are split deterministically (SHA-256 of tenant and case) into a training set and
a held-out set. Contiguous step sequences that recur in enough training cases, across
enough distinct approvers, become versioned workflow proposals. Each proposal is then
compared against held-out traces it never saw. The comparison is review evidence for a
supervisor, not an accuracy claim, and thresholds are prototype choices documented here.

Proposals are always ``status: proposed`` with ``auto_activate: False``. Nothing in this
module can approve, activate or run a workflow; activation needs a supervisor
(``playbook.approve``, TAX-046), a reviewed TAX-061 registry entry and a rollback plan.
"""

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

OBSERVER_VERSION: Final = "tax064-observer-v1"
PROPOSAL_STATUS: Final = "proposed"
MIN_SEQUENCE_LENGTH: Final = 2
MAX_SEQUENCE_LENGTH: Final = 6
MIN_SUPPORT_CASES: Final = 3  # training cases containing the sequence
MIN_DISTINCT_APPROVERS: Final = 2  # not one officer's habit
HOLDOUT_BUCKETS: Final = 3  # one bucket of three is held out
MIN_HOLDOUT_CASES: Final = 2
MAX_ENTRIES: Final = 50_000
MAX_PROPOSALS: Final = 25

STEP_ACTIONS: Final = frozenset({"evidence_request.recorded", "decision.approved"})
EXECUTION_FAILED: Final = "decision.execution_failed"
# TAX-046 allowlisted outcomes; anything else in ``detail`` is not trusted as a step name.
APPROVED_OUTCOMES: Final = frozenset(
    {
        "close_no_further_action",
        "close_after_correction",
        "request_further_evidence",
        "continue_investigation",
        "escalate_to_supervisor",
    }
)
_UUIDISH = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:_\-]{0,127}")


class ObservationError(ValueError):
    """Ledger input is unsafe to learn from; carries a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Step:
    name: str
    actor: str
    event_id: str


@dataclass(frozen=True)
class CaseTrace:
    case_id: str
    steps: tuple[Step, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(step.name for step in self.steps)


def _text(entry: Mapping[str, object], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not _UUIDISH.fullmatch(value):
        raise ObservationError("invalid_entry", f"ledger entry {key} is missing or malformed")
    return value


def build_traces(entries: Iterable[Mapping[str, object]], *, tenant_id: str) -> list[CaseTrace]:
    """Turn one tenant's ledger entries into approved-step traces per case."""
    rows = list(entries)
    if len(rows) > MAX_ENTRIES:
        raise ObservationError("entry_budget_exceeded", "too many ledger entries for one run")
    seen: set[str] = set()
    ordered: list[tuple[int, Mapping[str, object]]] = []
    for entry in rows:
        if not isinstance(entry, Mapping):
            raise ObservationError("invalid_entry", "ledger entries must be objects")
        if entry.get("tenant_id", tenant_id) != tenant_id:
            raise ObservationError("tenant_mismatch", "entries from another tenant were supplied")
        seq = entry.get("chain_seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise ObservationError("invalid_entry", "chain_seq must be a positive integer")
        event_id = _text(entry, "event_id")
        if event_id in seen:
            continue  # duplicate delivery of the same sealed entry
        seen.add(event_id)
        ordered.append((seq, entry))
    ordered.sort(key=lambda item: item[0])
    if len({seq for seq, _ in ordered}) != len(ordered):
        raise ObservationError("chain_conflict", "two entries claim the same chain position")

    failed_subjects = {
        str(entry.get("subject_id"))
        for _, entry in ordered
        if entry.get("action") == EXECUTION_FAILED
    }
    by_case: dict[str, list[Step]] = defaultdict(list)
    for _, entry in ordered:
        action = entry.get("action")
        if action not in STEP_ACTIONS or entry.get("outcome") in ("denied", "refused"):
            continue
        case_id = entry.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            continue
        if action == "evidence_request.recorded":
            name = "request_evidence"
        else:
            if str(entry.get("subject_id")) in failed_subjects:
                continue  # approved but never applied: not an observed approved action
            detail = entry.get("detail")
            name = (
                detail
                if isinstance(detail, str) and detail in APPROVED_OUTCOMES
                else "decision_approved"
            )
        by_case[case_id].append(Step(name, _text(entry, "actor"), _text(entry, "event_id")))
    return [CaseTrace(case_id, tuple(steps)) for case_id, steps in sorted(by_case.items())]


def is_holdout(tenant_id: str, case_id: str) -> bool:
    """Deterministic split: the same case is always in the same set."""
    digest = hashlib.sha256(f"{tenant_id}\x1f{case_id}".encode()).digest()
    return digest[0] % HOLDOUT_BUCKETS == 0


def _contains(names: tuple[str, ...], pattern: tuple[str, ...]) -> int | None:
    width = len(pattern)
    for start in range(len(names) - width + 1):
        if names[start : start + width] == pattern:
            return start
    return None


def _mine(traces: list[CaseTrace]) -> dict[tuple[str, ...], list[tuple[CaseTrace, int]]]:
    """Contiguous sequences -> the training cases (and offsets) that contain them."""
    found: dict[tuple[str, ...], list[tuple[CaseTrace, int]]] = defaultdict(list)
    for trace in traces:
        names = trace.names
        counted: set[tuple[str, ...]] = set()
        for width in range(MIN_SEQUENCE_LENGTH, min(MAX_SEQUENCE_LENGTH, len(names)) + 1):
            for start in range(len(names) - width + 1):
                pattern = names[start : start + width]
                if pattern not in counted:
                    counted.add(pattern)
                    found[pattern].append((trace, start))
    return found


def evaluate_on_holdout(pattern: tuple[str, ...], holdout: list[CaseTrace]) -> dict[str, object]:
    """Compare a proposal with held-out traces; descriptive review evidence only."""
    if len(holdout) < MIN_HOLDOUT_CASES:
        return {"status": "insufficient_holdout", "holdout_cases": len(holdout)}
    full = [t for t in holdout if _contains(t.names, pattern) is not None]
    started = [t for t in holdout if pattern[0] in t.names]
    diverged: dict[str, int] = defaultdict(int)
    for trace in started:
        if trace in full:
            continue
        names = trace.names
        index = names.index(pattern[0])
        matched = 1
        while (
            matched < len(pattern)
            and index + matched < len(names)
            and names[index + matched] == pattern[matched]
        ):
            matched += 1
        actual = names[index + matched] if index + matched < len(names) else "end_of_trace"
        diverged[f"after {pattern[matched - 1]}: {actual} instead of {pattern[matched]}"] += 1
    return {
        "status": "evaluated",
        "holdout_cases": len(holdout),
        "cases_containing_sequence": len(full),
        "coverage": f"{len(full)}/{len(holdout)}",
        "cases_starting_sequence": len(started),
        "followed_when_started": f"{len(full)}/{len(started)}" if started else "0/0",
        "divergences": dict(sorted(diverged.items())),
        "notice": "Descriptive comparison with held-out cases for supervisor review; "
        "not an accuracy measure and not evidence that the workflow is correct.",
    }


def propose_workflows(
    entries: Iterable[Mapping[str, object]], *, tenant_id: str
) -> dict[str, object]:
    """Mine repeated approved sequences and return supervisor-review proposals."""
    traces = build_traces(entries, tenant_id=tenant_id)
    training = [t for t in traces if not is_holdout(tenant_id, t.case_id)]
    holdout = [t for t in traces if is_holdout(tenant_id, t.case_id)]
    mined = _mine(training)
    frequent = {
        pattern: hits
        for pattern, hits in mined.items()
        if len(hits) >= MIN_SUPPORT_CASES
        and len({t.steps[start + i].actor for t, start in hits for i in range(len(pattern))})
        >= MIN_DISTINCT_APPROVERS
    }

    def is_contained_in_longer(pattern: tuple[str, ...]) -> bool:
        support = len(frequent[pattern])
        return any(
            len(other) > len(pattern)
            and len(frequent[other]) == support
            and _contains(other, pattern) is not None
            for other in frequent
        )

    maximal = sorted(
        (p for p in frequent if not is_contained_in_longer(p)),
        key=lambda p: (-len(frequent[p]), -len(p), p),
    )[:MAX_PROPOSALS]
    proposals = []
    for pattern in maximal:
        hits = frequent[pattern]
        actors = sorted(
            {t.steps[start + i].actor for t, start in hits for i in range(len(pattern))}
        )
        evidence = [t.steps[start + i].event_id for t, start in hits for i in range(len(pattern))]
        body = {"tenant_id": tenant_id, "steps": list(pattern), "observer": OBSERVER_VERSION}
        proposal_id = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
        proposals.append(
            {
                "kind": "workflow_proposal",
                "proposal_id": f"wfp-{proposal_id}",
                "version": "0.1.0",
                "observer_version": OBSERVER_VERSION,
                "status": PROPOSAL_STATUS,
                "auto_activate": False,
                "approved_by": None,
                "activation_requires": [
                    "supervisor playbook.approve (TAX-046)",
                    "reviewed TAX-061 registry entry",
                    "holdout review and rollback plan",
                ],
                "steps": list(pattern),
                "support": {
                    "training_cases": len(hits),
                    "distinct_approvers": len(actors),
                    "case_ids": sorted({t.case_id for t, _ in hits}),
                },
                "evidence_event_ids": evidence[:200],
                "holdout_evaluation": evaluate_on_holdout(pattern, holdout),
                "thresholds": {
                    "min_support_cases": MIN_SUPPORT_CASES,
                    "min_distinct_approvers": MIN_DISTINCT_APPROVERS,
                    "sequence_length": [MIN_SEQUENCE_LENGTH, MAX_SEQUENCE_LENGTH],
                    "holdout_fraction": f"1/{HOLDOUT_BUCKETS}",
                },
                "notice": "Observed pattern of approved actions, proposed for supervisor "
                "review only. It is not active, not a rule and not a judgement about any "
                "officer or taxpayer.",
            }
        )
    return {
        "tenant_id": tenant_id,
        "observer_version": OBSERVER_VERSION,
        "cases_observed": len(traces),
        "training_cases": len(training),
        "holdout_cases": len(holdout),
        "proposals": proposals,
        "activated": [],
    }


def activate(proposal: Mapping[str, object]) -> None:
    """Explicitly refuse self-activation; activation is a supervisor action elsewhere."""
    raise ObservationError(
        "activation_not_permitted",
        "Workflow proposals cannot be activated by the observer; a supervisor must approve "
        "a reviewed registry version (TAX-046 playbook.approve, TAX-061).",
    )
