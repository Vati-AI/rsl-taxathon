"""Risk calibration and drift evaluation on reviewed outcomes (TAX-066, ADR-030).

The approved risk model is the reviewed TAX-035 ruleset
(``integration/jube/rules/tax_risk_rules.v1.json``), the same file Jube's activation
rules are rendered from. This module measures it against *reviewed outcomes* and
proposes a calibrated candidate. It never replaces the approved ruleset:

* **Reviewed outcomes** come only from TAX-059 audit-ledger entries in the
  ``vati_tax.audit.ledger.entry_view`` shape (as returned by the supervisor-only
  ``get_ledger``) together with a ``verify_ledger()`` report. A case's label is its
  latest decision that was proposed by one officer (``decision.proposed``), approved
  by a *different* supervisor (``decision.approved``; the approved TAX-046 outcome is
  the joined ``detail`` field, as in TAX-064) and executed (``decision.executed``, no
  ``decision.execution_failed``). A decision overturned on challenge (TAX-069) is not
  a reliable label. Everything that does not qualify is excluded and listed by reason.
* **Labels.** ``close_no_further_action`` is *legitimate* (no review was warranted);
  every other allowlisted outcome is *review warranted*. A flagged legitimate case is
  a false positive; an unflagged review-warranted case is a miss.
* **Temporal holdout.** The candidate is fitted only on cases from the calibration
  periods whose label was approved before ``label_cutoff``; it is evaluated on later
  holdout periods it never saw. Calibration-period labels approved at or after the
  cutoff are excluded (no leakage), and periods may not overlap.
* **Calibration.** A small reviewed grid of integral thresholds per rule. The fit is
  lexicographic on the calibration set: fewest misses, then fewest false positives,
  then fewest changed rules, then the smallest move from the approved values.
* **Legitimate-case regressions.** Holdout cases that the candidate flags and the
  approved ruleset did not, where the officer closed with no further action. These
  and newly missed review-warranted cases block promotion above their thresholds.
* **Drift.** Rule fire rates, the label mix and Population Stability Index per
  feature (fixed reviewed bins), calibration window against holdout window, plus a
  per-period table. Alerts are listed plainly and must be acknowledged on approval.
* **Promotion.** ``promote`` needs an approval record from a ``SYN-SUPERVISOR-NN``
  who is not the proposer, naming the candidate, parent and report digests and every
  drift alert. Even an approved candidate is refused when the metric gate is blocked.
  A successful result is an approval record only: activation is a separate reviewed
  PR that replaces the ruleset file and runs ``scripts/jube_rules.py apply``. This
  module has no code path that writes the approved ruleset, and ``write_candidate``
  refuses the rules directory.

Everything is deterministic and runs on synthetic data only. Rates are exact
fractions; failures and false positives are listed by case, never averaged away.
"""

from __future__ import annotations

import argparse
import copy
import datetime
import functools
import hashlib
import importlib.util
import itertools
import json
import math
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[2]
FIXTURES_PATH: Final = ROOT / "data" / "synthetic" / "tax066_reviewed_outcomes.json"
TAX_RISK_PATH: Final = ROOT / "integration" / "jube" / "tax_risk.py"
APPROVED_RULESET_PATH: Final = ROOT / "integration" / "jube" / "rules" / "tax_risk_rules.v1.json"
PROTECTED_DIR: Final = APPROVED_RULESET_PATH.parent
CALIBRATION_VERSION: Final = "tax066-risk-calibration-v1"

LEGITIMATE_OUTCOMES: Final = frozenset({"close_no_further_action"})
REVIEW_WARRANTED_OUTCOMES: Final = frozenset(
    {
        "close_after_correction",
        "request_further_evidence",
        "continue_investigation",
        "escalate_to_supervisor",
        "refer_to_specialist",
    }
)
REVIEWED_OUTCOMES: Final = LEGITIMATE_OUTCOMES | REVIEW_WARRANTED_OUTCOMES
OVERTURNED: Final = frozenset({"overturned", "partially_overturned"})
APPROVER_ID: Final = re.compile(r"SYN-SUPERVISOR-\d{2}")
MAX_ENTRIES: Final = 50_000
_SAFE_TEXT: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:@_\-]{0,127}")


class CalibrationError(ValueError):
    """Input is unsafe to calibrate from; carries a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PromotionRefused(ValueError):
    """A candidate may not be promoted; carries a stable code and detail."""

    def __init__(self, code: str, message: str, detail: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.detail = list(detail)


@functools.lru_cache(maxsize=1)
def tax_risk() -> Any:
    """The TAX-035 evaluator, loaded from its file (``integration/jube`` is not a package)."""
    spec = importlib.util.spec_from_file_location("vati_tax066_tax_risk", TAX_RISK_PATH)
    if spec is None or spec.loader is None:
        raise CalibrationError("evaluator_missing", f"cannot load {TAX_RISK_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


def load_fixtures(path: Path = FIXTURES_PATH) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("synthetic") is not True:
        raise CalibrationError("not_synthetic", "TAX-066 fixtures must be marked synthetic")
    return doc


def load_approved_ruleset(path: Path = APPROVED_RULESET_PATH) -> dict[str, Any]:
    return dict(tax_risk().load_ruleset(path))


def _ts(value: object, what: str) -> datetime.datetime:
    if not isinstance(value, str):
        raise CalibrationError("invalid_timestamp", f"{what} must be an ISO timestamp")
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalibrationError("invalid_timestamp", f"{what} is not ISO: {value!r}") from exc
    if parsed.tzinfo is None:
        raise CalibrationError("invalid_timestamp", f"{what} needs a timezone")
    return parsed


def _iso(value: datetime.datetime) -> str:
    return value.astimezone(datetime.UTC).isoformat().replace("+00:00", "Z")


# --- synthetic stand-ins for records and ledger reads ----------------------------------


def case_uuid(n: int) -> str:
    return f"66000000-0000-4000-8000-{n:012d}"


def display_id(n: int) -> str:
    return f"TAX066-{n:02d}"


def _periods_ending(period: str, count: int) -> list[str]:
    index = tax_risk().quarter_index(period)
    return [f"{i // 4}-Q{i % 4 + 1}" for i in range(index - count + 1, index + 1)]


def build_record(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Expand a compact case spec into a TAX-024-shaped synthetic record."""
    n = int(spec["n"])
    stem = f"SYN066-{n:02d}"
    contracts: list[dict[str, Any]] = [
        {
            "id": f"CTR-{stem}-01",
            "counterparty_id": f"SYN-SUPPLIER-066-{n:02d}",
            "description": "General operating supplies",
            "related_party": False,
        }
    ]
    documented = Decimal(str(spec["documented"]))
    invoices: list[dict[str, Any]] = [
        {
            "id": f"INV-{stem}-01",
            "contract_id": f"CTR-{stem}-01",
            "counterparty_id": f"SYN-SUPPLIER-066-{n:02d}",
            "direction": "purchase",
            "net_amount": f"{documented * 10:.2f}",
            "vat_amount": f"{documented:.2f}",
            "gross_amount": f"{documented * 11:.2f}",
        }
    ]
    related = Decimal(str(spec["related_net"]))
    if related > 0:
        contracts.append(
            {
                "id": f"CTR-{stem}-RP",
                "counterparty_id": f"SYN-GROUP-066-{n:02d}",
                "description": "Group management services",
                "related_party": True,
            }
        )
        invoices.append(
            {
                "id": f"INV-{stem}-RP",
                "contract_id": f"CTR-{stem}-RP",
                "counterparty_id": f"SYN-GROUP-066-{n:02d}",
                "direction": "purchase",
                "net_amount": f"{related:.2f}",
                "vat_amount": "0.00",
                "gross_amount": f"{related:.2f}",
            }
        )
    imported = int(spec["imported_unverified"])
    if imported:
        contracts.append(
            {
                "id": f"CTR-{stem}-IMP",
                "counterparty_id": f"SYN-FOREIGN-066-{n:02d}",
                "description": "Imported digital services",
                "related_party": False,
            }
        )
        for k in range(1, imported + 1):
            invoices.append(
                {
                    "id": f"INV-{stem}-IMP-{k}",
                    "contract_id": f"CTR-{stem}-IMP",
                    "counterparty_id": f"SYN-FOREIGN-066-{n:02d}",
                    "direction": "purchase",
                    "net_amount": "1000.00",
                    "vat_amount": "0.00",
                    "gross_amount": "1000.00",
                    "vat_status": "treatment_unverified",
                }
            )
    return {
        "case_id": case_uuid(n),
        "display_id": display_id(n),
        "synthetic": True,
        "taxpayer_id": f"SYN-TAXPAYER-066-{n:02d}",
        "period": spec["period"],
        "return": {
            "id": f"RET-{stem}",
            "output_vat": "0.00",
            "input_vat_claimed": spec["claimed"],
            "refund_claim": spec["refund"],
        },
        "contracts": contracts,
        "invoices": invoices,
        "payments": [],
        "loss_periods": _periods_ending(spec["period"], int(spec["loss_run"])),
    }


def synthetic_ledger(
    fixtures: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Stand-in for ``get_ledger`` (joined with approved outcomes) plus ``verify_ledger``.

    Rows use the TAX-059 ``entry_view`` field names. Real use passes the supervisor's
    ledger read and verification report instead.
    """
    tenant = fixtures["tenant_id"]
    specs = sorted(fixtures["cases"], key=lambda s: (s["review"]["approved_at"], s["n"]))
    entries: list[dict[str, Any]] = []

    def add(spec: Mapping[str, Any], suffix: str, **row: Any) -> None:
        n = int(spec["n"])
        entries.append(
            {
                "tenant_id": tenant,
                "event_id": f"evt-066-{n:02d}-{suffix}",
                "chain_seq": len(entries) + 1,
                "subject_type": "decision",
                "subject_id": f"dec-066-{n:02d}",
                "case_id": case_uuid(n),
                "correlation_id": f"corr-066-{n:02d}",
                **row,
            }
        )

    for spec in specs:
        review = spec["review"]
        approved_at = _ts(review["approved_at"], "approved_at")
        add(
            spec,
            "p",
            category="write",
            action="decision.proposed",
            outcome="proposed",
            actor=review["proposed_by"],
            actor_role=review.get("proposer_role", "auditor"),
            occurred_at=_iso(approved_at - datetime.timedelta(days=2)),
        )
        add(
            spec,
            "a",
            category="approval",
            action="decision.approved",
            outcome="approved",
            actor=review["approved_by"],
            actor_role=review.get("approver_role", "supervisor"),
            occurred_at=_iso(approved_at),
            detail=review["outcome"],
        )
        failed = review.get("execution") == "failed"
        add(
            spec,
            "x",
            category="write",
            action="decision.execution_failed" if failed else "decision.executed",
            outcome="approval_expired" if failed else "changed",
            actor="vati-service",
            actor_role="service",
            occurred_at=_iso(approved_at + datetime.timedelta(minutes=1)),
        )
        if review.get("challenge") in OVERTURNED:
            add(
                spec,
                "c",
                category="approval",
                action="challenge.reviewed",
                outcome=review["challenge"],
                actor="supervisor-e@synthetic.test",
                actor_role="supervisor",
                occurred_at=_iso(approved_at + datetime.timedelta(days=30)),
                subject_type="challenge",
                subject_id=f"chl-066-{int(spec['n']):02d}",
            )
    head = len(entries)
    report = {
        "intact": True,
        "entries_checked": head,
        "head_seq": head,
        "head_hash": digest([e["event_id"] for e in entries]),
        "problems": [],
    }
    return entries, report


# --- reviewed outcomes from ledger entries ---------------------------------------------


@dataclass(frozen=True)
class ReviewedOutcome:
    case_id: str
    decision_id: str
    outcome: str
    proposed_by: str
    approved_by: str
    approved_at: datetime.datetime
    approved_seq: int

    @property
    def legitimate(self) -> bool:
        return self.outcome in LEGITIMATE_OUTCOMES


def _text(entry: Mapping[str, Any], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not _SAFE_TEXT.fullmatch(value):
        raise CalibrationError("invalid_entry", f"ledger entry {key} is missing or malformed")
    return value


def reviewed_outcomes(
    entries: Iterable[Mapping[str, Any]],
    verify_report: Mapping[str, Any],
    *,
    tenant_id: str,
) -> tuple[dict[str, ReviewedOutcome], list[dict[str, str]]]:
    """Final reviewed outcome per case, plus every excluded decision with its reason."""
    if not isinstance(verify_report, Mapping) or verify_report.get("intact") is not True:
        raise CalibrationError("ledger_not_intact", "the ledger verification did not pass")
    rows = list(entries)
    if len(rows) > MAX_ENTRIES:
        raise CalibrationError("entry_budget_exceeded", "too many ledger entries for one run")
    seen: set[str] = set()
    ordered: list[Mapping[str, Any]] = []
    positions: set[int] = set()
    for entry in rows:
        if not isinstance(entry, Mapping):
            raise CalibrationError("invalid_entry", "ledger entries must be objects")
        if entry.get("tenant_id", tenant_id) != tenant_id:
            raise CalibrationError("tenant_mismatch", "entries from another tenant were supplied")
        seq = entry.get("chain_seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise CalibrationError("invalid_entry", "chain_seq must be a positive integer")
        event_id = _text(entry, "event_id")
        if event_id in seen:
            continue  # duplicate delivery of the same sealed entry
        if seq in positions:
            raise CalibrationError("chain_conflict", "two entries claim the same chain position")
        seen.add(event_id)
        positions.add(seq)
        ordered.append(entry)
    ordered.sort(key=lambda e: int(e["chain_seq"]))
    verified = verify_report.get("head_seq")
    checked = verify_report.get("entries_checked")
    top = max(positions, default=0)
    if (
        not isinstance(verified, int)
        or not isinstance(checked, int)
        or top > min(verified, checked)
    ):
        raise CalibrationError(
            "ledger_not_verified", "entries beyond the verified chain head were supplied"
        )

    by_decision: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    overturned_cases: set[str] = set()
    for entry in ordered:
        action = entry.get("action")
        if action == "challenge.reviewed" and entry.get("outcome") in OVERTURNED:
            if isinstance(entry.get("case_id"), str):
                overturned_cases.add(str(entry["case_id"]))
            continue
        if entry.get("subject_type") != "decision" or not isinstance(action, str):
            continue
        if not action.startswith("decision.") or action.endswith(".denied"):
            continue
        decision_id = _text(entry, "subject_id")
        by_decision[decision_id].setdefault(action, entry)

    excluded: list[dict[str, str]] = []
    finals: dict[str, ReviewedOutcome] = {}
    for decision_id, events in sorted(by_decision.items()):
        approved = events.get("decision.approved")
        if approved is None:
            continue  # not reviewed (draft, proposal awaiting review, rejected or withdrawn)
        case_id = approved.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            excluded.append({"decision": decision_id, "case_id": "", "reason": "missing_case"})
            continue

        def exclude(reason: str, case_id: str = case_id, decision_id: str = decision_id) -> None:
            excluded.append({"decision": decision_id, "case_id": case_id, "reason": reason})

        outcome = approved.get("detail")
        proposed = events.get("decision.proposed")
        if not isinstance(outcome, str) or outcome not in REVIEWED_OUTCOMES:
            exclude("unknown_outcome")
        elif proposed is None:
            exclude("missing_proposal")
        elif approved.get("actor_role") != "supervisor":
            exclude("approver_not_supervisor")
        elif _text(proposed, "actor").lower() == _text(approved, "actor").lower():
            exclude("sod_violation")
        elif "decision.execution_failed" in events or "decision.executed" not in events:
            exclude("not_executed")
        elif case_id in overturned_cases:
            exclude("overturned_on_challenge")
        else:
            candidate = ReviewedOutcome(
                case_id=case_id,
                decision_id=decision_id,
                outcome=outcome,
                proposed_by=_text(proposed, "actor"),
                approved_by=_text(approved, "actor"),
                approved_at=_ts(approved.get("occurred_at"), "occurred_at"),
                approved_seq=int(approved["chain_seq"]),
            )
            current = finals.get(case_id)
            if current is None or candidate.approved_seq > current.approved_seq:
                finals[case_id] = candidate
    return finals, excluded


# --- evaluation --------------------------------------------------------------------------


@dataclass(frozen=True)
class Example:
    case_id: str
    display_id: str
    period: str
    record: Mapping[str, Any]
    label: ReviewedOutcome

    @property
    def legitimate(self) -> bool:
        return self.label.legitimate


def signals(ruleset: Mapping[str, Any], record: Mapping[str, Any]) -> list[str]:
    """Rule IDs that signal for one record, using the TAX-035 evaluator itself."""
    results = tax_risk().evaluate_record(ruleset, dict(record))
    return [r["rule_id"] for r in results if r["outcome"] == "signal"]


@dataclass(frozen=True)
class Confusion:
    tp: tuple[str, ...]
    fp: tuple[str, ...]
    fn: tuple[str, ...]
    tn: tuple[str, ...]
    rule_fires: Mapping[str, int]
    flagged: frozenset[str]

    def counts(self) -> dict[str, int]:
        return {"tp": len(self.tp), "fp": len(self.fp), "fn": len(self.fn), "tn": len(self.tn)}

    def view(self) -> dict[str, Any]:
        legit = len(self.fp) + len(self.tn)
        warranted = len(self.tp) + len(self.fn)
        flagged = len(self.tp) + len(self.fp)
        return {
            **self.counts(),
            "false_positive_rate": _rate(len(self.fp), legit),
            "recall": _rate(len(self.tp), warranted),
            "precision": _rate(len(self.tp), flagged),
            "false_positive_cases": list(self.fp),
            "missed_cases": list(self.fn),
            "rule_fires": dict(self.rule_fires),
        }


def _rate(num: int, den: int) -> dict[str, Any]:
    if den == 0:
        return {"value": None, "fraction": f"{num}/0"}
    return {"value": float(round(Fraction(num, den), 4)), "fraction": f"{num}/{den}"}


def confusion(ruleset: Mapping[str, Any], examples: Sequence[Example]) -> Confusion:
    tp: list[str] = []
    fp: list[str] = []
    fn: list[str] = []
    tn: list[str] = []
    fires: Counter[str] = Counter({r["rule_id"]: 0 for r in ruleset["rules"]})
    flagged: set[str] = set()
    for example in examples:
        fired = signals(ruleset, example.record)
        fires.update(fired)
        if fired:
            flagged.add(example.case_id)
        bucket = (fp if fired else tn) if example.legitimate else (tp if fired else fn)
        bucket.append(example.display_id)
    return Confusion(tuple(tp), tuple(fp), tuple(fn), tuple(tn), dict(fires), frozenset(flagged))


def rule_threshold(ruleset: Mapping[str, Any], rule_id: str) -> str:
    for rule in ruleset["rules"]:
        if rule["rule_id"] == rule_id:
            return str(rule["condition"]["value"])
    raise CalibrationError("unknown_rule", f"no rule {rule_id}")


def _bump_minor(version: str) -> str:
    major, minor, _ = (int(p) for p in version.split("."))
    return f"{major}.{minor + 1}.0"


def with_thresholds(
    approved: Mapping[str, Any],
    thresholds: Mapping[str, str],
    *,
    proposer: str,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A proposed ruleset: changed rule conditions, versions bumped, never ``approved``."""
    candidate = copy.deepcopy(dict(approved))
    changed = False
    for rule in candidate["rules"]:
        new = thresholds.get(rule["rule_id"])
        if new is None or Decimal(new) == Decimal(str(rule["condition"]["value"])):
            continue
        tax_risk().jube_literal(new)  # Jube thresholds must stay integral
        rule["condition"]["value"] = new
        rule["version"] = _bump_minor(rule["version"])
        changed = True
    if changed:
        candidate["ruleset_version"] = _bump_minor(str(approved["ruleset_version"]))
        candidate["status"] = "proposed_calibration"
        candidate["calibration"] = {
            "calibration_version": CALIBRATION_VERSION,
            "parent_ruleset_version": approved["ruleset_version"],
            "parent_digest": digest(approved),
            "proposer": proposer,
            "auto_activate": False,
            **(meta or {}),
        }
    tax_risk().validate_ruleset(candidate)
    return candidate


def fit_candidate(
    approved: Mapping[str, Any],
    calibration: Sequence[Example],
    grid: Mapping[str, Sequence[str]],
    *,
    proposer: str,
    meta: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Grid search on the calibration examples only; returns (candidate, chosen thresholds)."""
    order = [r["rule_id"] for r in approved["rules"] if r["rule_id"] in grid]
    if set(grid) - set(order):
        raise CalibrationError("unknown_rule", f"grid names unknown rules {set(grid) - set(order)}")
    baseline: dict[str, int] = {}
    for rule_id in order:
        values = [Decimal(v) for v in grid[rule_id]]
        current = Decimal(rule_threshold(approved, rule_id))
        if current not in values:
            raise CalibrationError("grid_missing_approved", f"{rule_id} grid lacks {current}")
        baseline[rule_id] = values.index(current)
    best: tuple[tuple[int, int, int, int], dict[str, str]] | None = None
    for combo in itertools.product(*(range(len(grid[r])) for r in order)):
        chosen = {rule_id: grid[rule_id][i] for rule_id, i in zip(order, combo, strict=True)}
        ruleset = with_thresholds(approved, chosen, proposer=proposer)
        result = confusion(ruleset, calibration)
        moves = [abs(i - baseline[r]) for r, i in zip(order, combo, strict=True)]
        key = (len(result.fn), len(result.fp), sum(1 for m in moves if m), sum(moves))
        if best is None or key < best[0]:
            best = (key, chosen)
    assert best is not None
    chosen = best[1]
    return with_thresholds(approved, chosen, proposer=proposer, meta=meta), chosen


def _bin_index(value: Decimal, edges: Sequence[Decimal]) -> int:
    for i, edge in enumerate(edges):
        if value <= edge:
            return i
    return len(edges)


def psi(expected: Sequence[int], actual: Sequence[int]) -> float:
    """Population Stability Index with +0.5 smoothing per bin (deterministic)."""
    e_total = sum(expected) + 0.5 * len(expected)
    a_total = sum(actual) + 0.5 * len(actual)
    total = 0.0
    for e, a in zip(expected, actual, strict=True):
        p = (e + 0.5) / e_total
        q = (a + 0.5) / a_total
        total += (q - p) * math.log(q / p)
    return total


def drift_report(
    approved: Mapping[str, Any],
    calibration: Sequence[Example],
    holdout: Sequence[Example],
    *,
    bins: Mapping[str, Sequence[str]],
    rate_alert: Fraction,
    psi_alert: float,
) -> dict[str, Any]:
    """Calibration window against holdout window, plus per-period rows (approved model)."""
    alerts: list[dict[str, Any]] = []

    def features(example: Example) -> Mapping[str, Decimal]:
        return dict(tax_risk().derive_features(dict(example.record)))

    feature_psi: dict[str, Any] = {}
    for name in sorted(bins):
        edges = [Decimal(e) for e in bins[name]]
        counts = []
        for window in (calibration, holdout):
            row = [0] * (len(edges) + 1)
            for example in window:
                row[_bin_index(features(example)[name], edges)] += 1
            counts.append(row)
        value = psi(counts[0], counts[1])
        feature_psi[name] = {
            "bins_upper_inclusive": [str(e) for e in edges],
            "calibration": counts[0],
            "holdout": counts[1],
            "psi": round(value, 4),
        }
        if value > psi_alert:
            alerts.append(
                {
                    "id": f"feature_psi:{name}",
                    "value": round(value, 4),
                    "threshold": psi_alert,
                    "detail": f"bins {counts[0]} -> {counts[1]}",
                }
            )

    def fire_rates(window: Sequence[Example]) -> dict[str, Fraction]:
        fires = confusion(approved, window).rule_fires
        return {rule: Fraction(count, len(window) or 1) for rule, count in fires.items()}

    before, after = fire_rates(calibration), fire_rates(holdout)
    rule_rates: dict[str, Any] = {}
    for rule in sorted(before):
        change = abs(after[rule] - before[rule])
        rule_rates[rule] = {
            "calibration": f"{before[rule].numerator}/{before[rule].denominator}",
            "holdout": f"{after[rule].numerator}/{after[rule].denominator}",
            "change": float(round(change, 4)),
        }
        if change > rate_alert:
            alerts.append(
                {
                    "id": f"rule_rate:{rule}",
                    "value": float(round(change, 4)),
                    "threshold": float(rate_alert),
                    "detail": f"fire rate {float(before[rule]):.4f} -> {float(after[rule]):.4f}",
                }
            )
    legit_before = Fraction(sum(e.legitimate for e in calibration), len(calibration) or 1)
    legit_after = Fraction(sum(e.legitimate for e in holdout), len(holdout) or 1)
    if abs(legit_after - legit_before) > rate_alert:
        alerts.append(
            {
                "id": "label_mix:legitimate_share",
                "value": float(round(abs(legit_after - legit_before), 4)),
                "threshold": float(rate_alert),
                "detail": f"{float(legit_before):.4f} -> {float(legit_after):.4f}",
            }
        )
    periods: dict[str, list[Example]] = defaultdict(list)
    for example in (*calibration, *holdout):
        periods[example.period].append(example)
    per_period = []
    for period in sorted(periods, key=tax_risk().quarter_index):
        window = periods[period]
        result = confusion(approved, window)
        per_period.append(
            {
                "period": period,
                "cases": len(window),
                "legitimate": sum(e.legitimate for e in window),
                **result.counts(),
                "rule_fires": dict(result.rule_fires),
            }
        )
    alerts.sort(key=lambda a: a["id"])
    return {
        "alerts": alerts,
        "feature_psi": feature_psi,
        "rule_fire_rates": rule_rates,
        "legitimate_share": {
            "calibration": float(round(legit_before, 4)),
            "holdout": float(round(legit_after, 4)),
        },
        "per_period": per_period,
    }


@dataclass(frozen=True)
class CalibrationResult:
    report: dict[str, Any]
    candidate: dict[str, Any]
    approved: dict[str, Any]


def _split(
    records: Mapping[str, Mapping[str, Any]],
    labels: Mapping[str, ReviewedOutcome],
    config: Mapping[str, Any],
) -> tuple[list[Example], list[Example], list[dict[str, str]], list[str]]:
    calib_periods = set(config["calibration_periods"])
    holdout_periods = set(config["holdout_periods"])
    if calib_periods & holdout_periods:
        raise CalibrationError("periods_overlap", "calibration and holdout periods overlap")
    q = tax_risk().quarter_index
    if not calib_periods or not holdout_periods:
        raise CalibrationError("periods_missing", "both windows need at least one period")
    if max(map(q, calib_periods)) >= min(map(q, holdout_periods)):
        raise CalibrationError("not_temporal", "holdout periods must all follow calibration")
    cutoff = _ts(config["label_cutoff"], "label_cutoff")
    calibration: list[Example] = []
    holdout: list[Example] = []
    excluded: list[dict[str, str]] = []
    unreviewed: list[str] = []
    for case_id in sorted(records, key=lambda c: str(records[c].get("display_id", c))):
        record = records[case_id]
        shown = str(record.get("display_id", case_id))
        label = labels.get(case_id)
        if label is None:
            unreviewed.append(shown)
            continue
        example = Example(case_id, shown, str(record["period"]), record, label)
        if example.period in calib_periods:
            if label.approved_at >= cutoff:
                excluded.append({"case": shown, "reason": "label_after_cutoff"})
                continue
            calibration.append(example)
        elif example.period in holdout_periods:
            holdout.append(example)
        else:
            excluded.append({"case": shown, "reason": "period_outside_windows"})
    return calibration, holdout, excluded, unreviewed


def evaluate(
    fixtures: Mapping[str, Any],
    records: Mapping[str, Mapping[str, Any]],
    entries: Iterable[Mapping[str, Any]],
    verify_report: Mapping[str, Any],
    approved: Mapping[str, Any] | None = None,
) -> CalibrationResult:
    """Fit a candidate on the calibration window and evaluate both on the holdout."""
    approved_ruleset = dict(approved) if approved is not None else load_approved_ruleset()
    if approved_ruleset.get("status") == "proposed_calibration":
        raise CalibrationError("parent_not_approved", "calibrate only from the approved ruleset")
    for record in records.values():
        if record.get("synthetic") is not True:
            raise CalibrationError("not_synthetic", "only synthetic records may be calibrated")
    config = fixtures["calibration"]
    thresholds = fixtures["thresholds"]
    labels, ledger_excluded = reviewed_outcomes(
        entries, verify_report, tenant_id=str(fixtures["tenant_id"])
    )
    shown = {cid: str(rec.get("display_id", cid)) for cid, rec in records.items()}
    calibration, holdout, excluded, unreviewed = _split(records, labels, config)
    excluded = sorted(
        [
            *excluded,
            *(
                {"case": shown.get(e["case_id"], e["case_id"]), "reason": e["reason"]}
                for e in ledger_excluded
            ),
        ],
        key=lambda e: (e["case"], e["reason"]),
    )
    meta = {
        "calibration_periods": sorted(config["calibration_periods"]),
        "holdout_periods": sorted(config["holdout_periods"]),
        "label_cutoff": config["label_cutoff"],
        "calibration_cases": len(calibration),
    }
    candidate, chosen = fit_candidate(
        approved_ruleset,
        calibration,
        config["grid"],
        proposer=str(config["proposer"]),
        meta=meta,
    )
    changes = [
        {
            "rule_id": rule["rule_id"],
            "from": rule_threshold(approved_ruleset, rule["rule_id"]),
            "to": chosen[rule["rule_id"]],
            "version_from": old["version"],
            "version_to": rule["version"],
        }
        for rule, old in zip(candidate["rules"], approved_ruleset["rules"], strict=True)
        if rule["version"] != old["version"]
    ]
    approved_cal = confusion(approved_ruleset, calibration)
    candidate_cal = confusion(candidate, calibration)
    approved_hold = confusion(approved_ruleset, holdout)
    candidate_hold = confusion(candidate, holdout)
    by_case = {e.case_id: e for e in holdout}
    regressions = sorted(
        by_case[c].display_id
        for c in candidate_hold.flagged - approved_hold.flagged
        if by_case[c].legitimate
    )
    new_missed = sorted(
        by_case[c].display_id
        for c in approved_hold.flagged - candidate_hold.flagged
        if not by_case[c].legitimate
    )
    improvements = sorted(
        by_case[c].display_id
        for c in approved_hold.flagged - candidate_hold.flagged
        if by_case[c].legitimate
    )
    drift = drift_report(
        approved_ruleset,
        calibration,
        holdout,
        bins=fixtures["drift_bins"],
        rate_alert=Fraction(str(thresholds["drift_rate_change_alert"])),
        psi_alert=float(thresholds["drift_psi_alert"]),
    )
    calib_ids = {e.case_id for e in calibration}
    hold_ids = {e.case_id for e in holdout}
    cutoff = _ts(config["label_cutoff"], "label_cutoff")
    checks = [
        _check("ledger_verified", True, f"head_seq={verify_report.get('head_seq')}"),
        _check(
            "no_leakage",
            not (calib_ids & hold_ids) and all(e.label.approved_at < cutoff for e in calibration),
            "calibration labels approved before the cutoff; no case in both windows",
        ),
        _check(
            "min_calibration_cases",
            len(calibration) >= int(thresholds["min_calibration_cases"]),
            f"{len(calibration)} >= {thresholds['min_calibration_cases']}",
        ),
        _check(
            "min_holdout_cases",
            len(holdout) >= int(thresholds["min_holdout_cases"]),
            f"{len(holdout)} >= {thresholds['min_holdout_cases']}",
        ),
        _check(
            "min_holdout_legitimate",
            sum(e.legitimate for e in holdout) >= int(thresholds["min_holdout_legitimate"]),
            f"{sum(e.legitimate for e in holdout)} >= {thresholds['min_holdout_legitimate']}",
        ),
        _check(
            "legitimate_case_regressions",
            len(regressions) <= int(thresholds["max_legitimate_regressions"]),
            f"{len(regressions)} <= {thresholds['max_legitimate_regressions']}: {regressions}",
        ),
        _check(
            "new_missed_review_warranted",
            len(new_missed) <= int(thresholds["max_new_missed_review_warranted"]),
            f"{len(new_missed)} <= {thresholds['max_new_missed_review_warranted']}: {new_missed}",
        ),
        _check(
            "holdout_false_positives_not_worse",
            len(candidate_hold.fp) <= len(approved_hold.fp),
            f"{len(approved_hold.fp)} -> {len(candidate_hold.fp)}",
        ),
        _check("candidate_changes_something", bool(changes), f"{len(changes)} rule(s) changed"),
    ]
    blocked = [c["id"] for c in checks if not c["passed"]]
    report: dict[str, Any] = {
        "calibration_version": CALIBRATION_VERSION,
        "synthetic": True,
        "fixture_version": fixtures.get("fixture_version"),
        "approved": {
            "ruleset_version": approved_ruleset["ruleset_version"],
            "digest": digest(approved_ruleset),
        },
        "candidate": {
            "ruleset_version": candidate["ruleset_version"],
            "status": candidate.get("status"),
            "digest": digest(candidate),
            "proposer": config["proposer"],
            "auto_activate": False,
            "changes": changes,
        },
        "windows": {
            "calibration_periods": meta["calibration_periods"],
            "holdout_periods": meta["holdout_periods"],
            "label_cutoff": config["label_cutoff"],
            "calibration_cases": len(calibration),
            "holdout_cases": len(holdout),
            "unreviewed": unreviewed,
            "excluded": excluded,
        },
        "calibration": {"approved": approved_cal.view(), "candidate": candidate_cal.view()},
        "holdout": {"approved": approved_hold.view(), "candidate": candidate_hold.view()},
        "legitimate_case_regressions": regressions,
        "new_missed_review_warranted": new_missed,
        "false_positives_removed": improvements,
        "drift": drift,
        "checks": checks,
        "metric_gate": "blocked" if blocked else "pass",
        "blocked_by": blocked,
        "promotion": "requires a supervisor approval record (see promote); never automatic",
    }
    report["report_digest"] = digest(report)
    return CalibrationResult(report, candidate, approved_ruleset)


def _check(check_id: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "passed": bool(passed), "detail": detail}


def evaluate_fixtures(
    fixtures: Mapping[str, Any] | None = None, approved: Mapping[str, Any] | None = None
) -> CalibrationResult:
    doc = dict(fixtures) if fixtures is not None else load_fixtures()
    records = {r["case_id"]: r for r in (build_record(spec) for spec in doc["cases"])}
    entries, verify_report = synthetic_ledger(doc)
    return evaluate(doc, records, entries, verify_report, approved)


# --- promotion -------------------------------------------------------------------------


def promote(
    result: CalibrationResult,
    approval: Mapping[str, Any] | None,
    *,
    current_approved: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Check a supervisor approval of the candidate. Returns an approval record only."""
    report = result.report
    if not approval:
        raise PromotionRefused("approval_required", "a calibration needs a supervisor approval")
    if approval.get("decision") != "approve":
        raise PromotionRefused("not_approved", "the review did not approve this candidate")
    approver = approval.get("approver")
    if not isinstance(approver, str) or not APPROVER_ID.fullmatch(approver):
        raise PromotionRefused("invalid_approver", "approver must be a SYN-SUPERVISOR-NN")
    proposer = str(report["candidate"]["proposer"])
    if approver.lower() == proposer.lower():
        raise PromotionRefused("self_approval", "the proposer cannot approve their own candidate")
    rationale = approval.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise PromotionRefused("rationale_required", "the approval needs a written rationale")
    if approval.get("candidate_digest") != digest(result.candidate):
        raise PromotionRefused("digest_mismatch", "the approval names a different candidate")
    if report["candidate"]["digest"] != digest(result.candidate):
        raise PromotionRefused("digest_mismatch", "the report and candidate disagree")
    body = {k: v for k, v in report.items() if k != "report_digest"}
    if (
        approval.get("report_digest") != report["report_digest"]
        or digest(body) != report["report_digest"]
    ):
        raise PromotionRefused("report_mismatch", "the approval names a different report")
    live = dict(current_approved) if current_approved is not None else load_approved_ruleset()
    parent = digest(live)
    if approval.get("parent_digest") != parent or report["approved"]["digest"] != parent:
        raise PromotionRefused("stale_parent", "the approved ruleset changed since calibration")
    alerts = {a["id"] for a in report["drift"]["alerts"]}
    acknowledged = set(approval.get("acknowledged_drift_alerts") or [])
    if alerts - acknowledged:
        raise PromotionRefused(
            "drift_not_acknowledged",
            "every drift alert must be acknowledged by the approver",
            sorted(alerts - acknowledged),
        )
    if report["metric_gate"] != "pass":
        raise PromotionRefused(
            "metric_gate_blocked",
            "the candidate fails the holdout gate; approval cannot override it",
            report["blocked_by"],
        )
    approved_at = _ts(approval.get("approved_at"), "approved_at")
    return {
        "status": "approved_for_activation",
        "synthetic": True,
        "ruleset_id": result.candidate["ruleset_id"],
        "candidate_ruleset_version": result.candidate["ruleset_version"],
        "candidate_digest": digest(result.candidate),
        "parent_ruleset_version": report["approved"]["ruleset_version"],
        "parent_digest": parent,
        "report_digest": report["report_digest"],
        "proposer": proposer,
        "approver": approver,
        "approved_at": _iso(approved_at),
        "rationale": rationale.strip(),
        "acknowledged_drift_alerts": sorted(acknowledged & alerts),
        "activation": (
            "manual: a reviewed PR replaces the approved ruleset file and runs "
            "scripts/jube_rules.py apply; this module never writes the approved ruleset"
        ),
    }


def write_candidate(result: CalibrationResult, path: Path) -> Path:
    """Write the proposed ruleset for review. Refuses the approved rules directory."""
    target = path.resolve()
    protected = PROTECTED_DIR.resolve()
    if target == protected or protected in target.parents:
        raise PromotionRefused("protected_path", "candidates are never written over approved rules")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result.candidate, indent=2) + "\n", encoding="utf-8")
    return target


# --- CLI -------------------------------------------------------------------------------


def check_expected(report: Mapping[str, Any], expected: Mapping[str, Any]) -> list[str]:
    """Differences between a report and the reviewed expected baseline in the fixtures."""
    problems = []
    actual = {
        "included_calibration": report["windows"]["calibration_cases"],
        "included_holdout": report["windows"]["holdout_cases"],
        "excluded": report["windows"]["excluded"],
        "candidate_changes": {c["rule_id"]: c["to"] for c in report["candidate"]["changes"]},
        "calibration": {
            "approved": {k: report["calibration"]["approved"][k] for k in ("tp", "fp", "fn", "tn")},
            "candidate": {
                k: report["calibration"]["candidate"][k] for k in ("tp", "fp", "fn", "tn")
            },
        },
        "holdout": {
            "approved": {k: report["holdout"]["approved"][k] for k in ("tp", "fp", "fn", "tn")},
            "candidate": {k: report["holdout"]["candidate"][k] for k in ("tp", "fp", "fn", "tn")},
        },
        "legitimate_regressions": report["legitimate_case_regressions"],
        "new_missed_review_warranted": report["new_missed_review_warranted"],
        "metric_gate": report["metric_gate"],
        "drift_alerts": [a["id"] for a in report["drift"]["alerts"]],
    }
    for key, want in expected.items():
        if actual.get(key) != want:
            problems.append(f"{key}: expected {want!r}, got {actual.get(key)!r}")
    return problems


def render(report: Mapping[str, Any]) -> str:
    lines = [
        f"TAX-066 risk calibration ({report['calibration_version']}, synthetic)",
        f"approved ruleset {report['approved']['ruleset_version']} {report['approved']['digest']}",
        f"candidate {report['candidate']['ruleset_version']} "
        f"({report['candidate']['status']}, auto_activate=False) {report['candidate']['digest']}",
    ]
    for change in report["candidate"]["changes"]:
        lines.append(
            f"  {change['rule_id']}: {change['from']} -> {change['to']} "
            f"(v{change['version_from']} -> v{change['version_to']})"
        )
    w = report["windows"]
    lines.append(
        f"calibration {','.join(w['calibration_periods'])}: {w['calibration_cases']} cases; "
        f"holdout {','.join(w['holdout_periods'])}: {w['holdout_cases']} cases; "
        f"label cutoff {w['label_cutoff']}"
    )
    for item in w["excluded"]:
        lines.append(f"  excluded {item['case']}: {item['reason']}")
    for window in ("calibration", "holdout"):
        for model in ("approved", "candidate"):
            v = report[window][model]
            lines.append(
                f"{window:<11} {model:<9} TP {v['tp']:>2} FP {v['fp']:>2} FN {v['fn']:>2} "
                f"TN {v['tn']:>2}  FPR {v['false_positive_rate']['fraction']:>5}  "
                f"recall {v['recall']['fraction']:>5}  FP cases {v['false_positive_cases']}"
            )
    lines.append(f"legitimate-case regressions: {report['legitimate_case_regressions']}")
    lines.append(f"newly missed review-warranted: {report['new_missed_review_warranted']}")
    lines.append(f"false positives removed: {report['false_positives_removed']}")
    for alert in report["drift"]["alerts"]:
        lines.append(
            f"DRIFT {alert['id']}: {alert['value']} > {alert['threshold']} ({alert['detail']})"
        )
    for check in report["checks"]:
        lines.append(f"{'PASS' if check['passed'] else 'FAIL'} {check['id']}: {check['detail']}")
    lines.append(f"metric gate: {report['metric_gate'].upper()} {report['blocked_by']}")
    lines.append(f"report digest {report['report_digest']}")
    lines.append("promotion: supervisor approval record required; never automatic")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--check", action="store_true", help="fail if the report drifts from the reviewed baseline"
    )
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument(
        "--write-candidate", type=Path, help="write the proposed ruleset for review"
    )
    args = parser.parse_args(argv)
    fixtures = load_fixtures()
    result = evaluate_fixtures(fixtures)
    print(json.dumps(result.report, indent=2) if args.json else render(result.report))
    if args.write_candidate:
        print(f"candidate written to {write_candidate(result, args.write_candidate)}")
    if args.check:
        problems = check_expected(result.report, fixtures["expected"])
        for problem in problems:
            print(f"BASELINE MISMATCH {problem}", file=sys.stderr)
        if problems:
            return 1
        print("baseline: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
