"""Outcome and appeal feedback loop (TAX-070): review proposals, never automatic changes.

Input is one tenant's TAX-069 challenge histories (``get_challenge_history`` staff view:
decision -> objection -> appeal -> ``final_outcome``), each joined by the Vati layer
with the provenance of what informed the decision:

- ``playbook``/``tool`` stamps from the TAX-061 registry (``name``, ``version``,
  ``content_digest``, ``registry_version``);
- ``rule`` references (``rule_id``, ``ruleset_version``) from the TAX-035/066 risk ruleset;
- ``authority`` references (``authority_id``, ``version``) from the TAX-052 registry.

Only reviewed final outcomes count. ``withdrawn`` challenges and unresolved chains are
excluded with reasons, and a history whose original decision changed after the
challenge was lodged (``original_decision_unchanged`` false) is excluded as a lineage
problem rather than counted. ``upheld`` outcomes are kept as counter-evidence.

A component version with at least ``MIN_FINAL_OUTCOMES`` final outcomes and an
overturn share of at least ``REVIEW_SHARE`` (partially overturned counts as overturned)
becomes a *review proposal* for supervisors. Legal authorities are only ever referred
for legal review with ``legal_interpretation_change: None``. Nothing here edits a rule,
playbook, registry or legal interpretation: ``apply`` always refuses. Every proposal
records the lineage it was evaluated on (content digest, registry/ruleset versions,
decision digests and challenge IDs) and a lineage digest so the evaluation can be
reproduced. Thresholds are prototype choices documented here.
"""

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from fractions import Fraction
from typing import Final

FEEDBACK_VERSION: Final = "tax070-feedback-v1"
MIN_FINAL_OUTCOMES: Final = 3
REVIEW_SHARE: Final = Fraction(1, 2)
MAX_HISTORIES: Final = 20_000
FINAL_OUTCOMES: Final = frozenset({"upheld", "partially_overturned", "overturned"})
OVERTURNED: Final = frozenset({"overturned", "partially_overturned"})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@\-]{0,127}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
NOTICE: Final = (
    "Proposal for supervisor review only. Objection and appeal outcomes are recorded for "
    "review and have no verified legal effect; this proposal changes no rule, playbook, "
    "registry or legal interpretation and is not a finding about any officer."
)


class FeedbackInputError(ValueError):
    """A history or provenance entry is malformed; carries a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _ident(value: object, code: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise FeedbackInputError(code)
    return value


def _component(raw: object) -> tuple[tuple[str, str, str], dict[str, object]]:
    """Normalise one provenance entry to a (kind, name, version) key plus lineage."""
    if not isinstance(raw, Mapping):
        raise FeedbackInputError("invalid_provenance")
    kind = raw.get("kind")
    if kind in ("playbook", "tool"):
        name = _ident(raw.get("name"), "invalid_provenance")
        version = _ident(raw.get("version"), "invalid_provenance")
        digest = raw.get("content_digest")
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise FeedbackInputError("invalid_provenance")
        lineage = {"content_digest": digest, "registry_version": raw.get("registry_version")}
    elif kind == "rule":
        name = _ident(raw.get("rule_id"), "invalid_provenance")
        version = _ident(raw.get("ruleset_version"), "invalid_provenance")
        lineage = {"ruleset_version": version}
    elif kind == "authority":
        name = _ident(raw.get("authority_id"), "invalid_provenance")
        version = _ident(raw.get("version"), "invalid_provenance")
        lineage = {"authority_version": version}
    else:
        raise FeedbackInputError("invalid_provenance_kind")
    return (str(kind), name, version), lineage


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def evaluate_feedback(
    items: Iterable[Mapping[str, object]], *, tenant_id: str
) -> dict[str, object]:
    """Attribute reviewed final outcomes to component versions and propose reviews."""
    rows = list(items)
    if len(rows) > MAX_HISTORIES:
        raise FeedbackInputError("history_budget_exceeded")
    excluded: dict[str, int] = defaultdict(int)
    lineage_problems: list[str] = []
    seen_decisions: set[str] = set()
    tally: dict[tuple[str, str, str], dict[str, list[dict[str, object]]]] = defaultdict(
        lambda: {"overturned": [], "upheld": []}
    )
    lineage: dict[tuple[str, str, str], dict[str, object]] = {}
    for item in rows:
        if not isinstance(item, Mapping):
            raise FeedbackInputError("invalid_item")
        if item.get("tenant_id") != tenant_id:
            raise FeedbackInputError("tenant_mismatch")
        history = item.get("history")
        if not isinstance(history, Mapping) or not isinstance(history.get("decision"), Mapping):
            raise FeedbackInputError("invalid_history")
        decision = history["decision"]
        decision_id = _ident(decision.get("decision_id"), "invalid_history")
        if decision_id in seen_decisions:
            continue  # duplicate delivery of the same history
        seen_decisions.add(decision_id)
        if history.get("original_decision_unchanged") is not True:
            lineage_problems.append(decision_id)
            continue
        final = history.get("final_outcome")
        if not isinstance(final, Mapping) or final.get("outcome") is None:
            excluded["no_final_outcome"] += 1
            continue
        outcome = final.get("outcome")
        if outcome == "withdrawn":
            excluded["withdrawn"] += 1
            continue
        if outcome not in FINAL_OUTCOMES:
            raise FeedbackInputError("invalid_outcome")
        provenance = item.get("provenance")
        if not isinstance(provenance, list) or not provenance:
            excluded["no_provenance"] += 1
            continue
        evidence = {
            "decision_id": decision_id,
            "decision_digest": decision.get("decision_digest"),
            "challenge_id": final.get("challenge_id"),
            "stage": final.get("stage"),
            "outcome": outcome,
        }
        for raw in provenance:
            key, component_lineage = _component(raw)
            known = lineage.setdefault(key, component_lineage)
            if known != component_lineage:
                raise FeedbackInputError("conflicting_component_lineage")
            bucket = "overturned" if outcome in OVERTURNED else "upheld"
            tally[key][bucket].append(evidence)

    proposals = []
    components = []
    for key in sorted(tally):
        kind, name, version = key
        # Sorted so the same evidence in any delivery order gives the same proposal.
        overturned = sorted(tally[key]["overturned"], key=lambda e: str(e["decision_id"]))
        upheld = sorted(tally[key]["upheld"], key=lambda e: str(e["decision_id"]))
        total = len(overturned) + len(upheld)
        share = Fraction(len(overturned), total)
        summary = {
            "kind": kind,
            "name": name,
            "version": version,
            "final_outcomes": total,
            "overturned": len(overturned),
            "upheld": len(upheld),
            "overturn_share": f"{len(overturned)}/{total}",
        }
        components.append(summary)
        if total < MIN_FINAL_OUTCOMES or share < REVIEW_SHARE:
            continue
        evaluated_on = {
            "component": {"kind": kind, "name": name, "version": version, **lineage[key]},
            "decisions": sorted(
                (str(e["decision_id"]), str(e["decision_digest"]), str(e["challenge_id"]))
                for e in overturned + upheld
            ),
            "feedback_version": FEEDBACK_VERSION,
            "thresholds": [MIN_FINAL_OUTCOMES, str(REVIEW_SHARE)],
        }
        lineage_digest = _digest(evaluated_on)
        proposal: dict[str, object] = {
            "kind": "legal_review_referral" if kind == "authority" else "component_review_proposal",
            "proposal_id": f"fbp-{lineage_digest[:16]}",
            "status": "proposed",
            "auto_apply": False,
            "approved_by": None,
            "component": evaluated_on["component"],
            **{k: summary[k] for k in ("final_outcomes", "overturned", "upheld", "overturn_share")},
            "overturned_cases": overturned,
            "counter_evidence_upheld": upheld,
            "requested_action": "supervisor review of this version; any change goes through a "
            "new reviewed version (TAX-061/TAX-065), never an in-place edit",
            "lineage": evaluated_on,
            "lineage_digest": lineage_digest,
            "notice": NOTICE,
        }
        if kind == "authority":
            proposal["legal_interpretation_change"] = None
            proposal["automatic_change_permitted"] = False
            proposal["requested_action"] = (
                "legal reviewers to consider whether this authority version was applied "
                "appropriately; no interpretation is changed by this system"
            )
        proposals.append(proposal)

    return {
        "feedback_version": FEEDBACK_VERSION,
        "tenant_id": tenant_id,
        "histories_received": len(rows),
        "excluded": dict(sorted(excluded.items())),
        "lineage_problems": sorted(lineage_problems),
        "components": components,
        "proposals": proposals,
        "applied_changes": [],
        "notice": NOTICE,
    }


def apply(proposal: Mapping[str, object]) -> None:
    """Refuse: feedback never changes rules, playbooks, registries or legal interpretation."""
    raise FeedbackInputError("automatic_change_not_permitted")
