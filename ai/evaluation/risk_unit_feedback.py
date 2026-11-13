"""Risk Unit feedback: Vati's corroboration evidence for RSL (TAX-131, ADR-043 §6, ADR-053).

RSL selects cases; its Risk Unit owns the risk criteria and the scores. Vati does not
compete with them. What Vati learns from cases RSL referred is offered back as feedback
the Risk Unit may use to review its own criteria:

- from the TAX-066 calibration (``risk_calibration``): how often Vati's rule signals and
  the officers' reviewed outcomes agreed on the hold-out window, how often each rule
  signalled, the drift alerts, and which Vati rule changes are proposed (never active);
- from TAX-067 peer baselines (``peer_baselines.compare`` results): per metric, how many
  comparisons were made and how many fell outside the typical range or lacked evidence.

The pack is aggregate only: no case, taxpayer or TIN identifiers and no amounts. It has
no score, selection, status or decision field (``validate_pack`` refuses them anywhere).
Its shape is an assumption until RSL specifies one: the contract ID ends in
``-assumed`` and the stated route is RSL's WSO2 API Manager; nothing here sends it.

CLI::

    python -m ai.evaluation.risk_unit_feedback                 # print the pack (JSON)
    python -m ai.evaluation.risk_unit_feedback --baselines comparisons.json --out pack.json
    python -m ai.evaluation.risk_unit_feedback --check         # build and validate
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from ai.evaluation import risk_calibration

SCHEMA: Final = "vati.risk-unit-feedback/1"
CONTRACT_ID: Final = "rsl.audit_system.risk_unit_feedback.v0-assumed"
AUTHORITY: Final = "feedback_only"
AUDIENCE: Final = "RSL Risk Unit"
ROUTE: Final = "wso2_api_manager"
ASSUMED_SHAPE_NOTICE: Final = (
    "Assumed message shape pending an RSL-supplied specification; not an RSL interface."
)
STATEMENT: Final = (
    "Feedback from Vati's investigation of cases RSL selected. It corroborates or questions "
    "RSL's selection in aggregate. It is not a risk score, a selection, a finding about any "
    "taxpayer or a decision; RSL's Risk Unit decides whether to change its criteria."
)
BASELINE_VERSION: Final = "tax067-baselines-v1"
# Field names that would turn feedback into a score, a selection or an action on RSL's
# records. None is produced; ``validate_pack`` refuses them at any depth.
FORBIDDEN_KEYS: Final = frozenset(
    {
        "score",
        "risk_score",
        "fraud_score",
        "fraud_conclusion",
        "risk_level",
        "risk_rating",
        "selected",
        "selection",
        "select_taxpayers",
        "status",
        "rsl_status",
        "decision",
        "assessment",
        "payment",
        "offset",
    }
)
# Identifiers that would make the pack about individual cases or taxpayers.
IDENTIFYING_KEYS: Final = frozenset(
    {"case_id", "taxpayer_id", "tin", "tenant_id", "false_positive_cases", "missed_cases"}
)
POSITIONS: Final = ("within_typical_range", "outside_typical_range")


class FeedbackError(ValueError):
    """A pack or its inputs break the feedback contract; ``code`` is stable."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def _digest(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _agreement(view: Mapping[str, Any]) -> dict[str, Any]:
    """Confusion counts in the Risk Unit's terms; the case lists are left out."""
    return {
        "signal_confirmed_by_review": int(view["tp"]),
        "signal_not_confirmed_by_review": int(view["fp"]),
        "review_found_issue_without_signal": int(view["fn"]),
        "no_signal_and_no_issue": int(view["tn"]),
        "signals_not_confirmed_rate": view["false_positive_rate"],
        "issues_signalled_rate": view["recall"],
        "signals_confirmed_rate": view["precision"],
    }


def calibration_section(report: Mapping[str, Any]) -> dict[str, Any]:
    """The TAX-066 report as aggregate feedback (approved ruleset, hold-out window)."""
    holdout = report["holdout"]["approved"]
    windows = report["windows"]
    drift = report.get("drift") or {}
    return {
        "source": report["calibration_version"],
        "report_digest": report["report_digest"],
        "fixture_version": report.get("fixture_version"),
        "vati_ruleset": dict(report["approved"]),
        "window": {
            "calibration_periods": list(windows["calibration_periods"]),
            "holdout_periods": list(windows["holdout_periods"]),
            "reviewed_cases": int(windows["calibration_cases"]) + int(windows["holdout_cases"]),
        },
        "holdout_agreement": _agreement(holdout),
        "rule_signals_on_holdout": dict(sorted(holdout["rule_fires"].items())),
        "drift_alerts": [
            {k: alert[k] for k in ("id", "value", "threshold", "detail")}
            for alert in drift.get("alerts", [])
        ],
        "proposed_vati_rule_changes": sorted(
            {
                str(change.get("rule_id") or change.get("rule"))
                for change in report["candidate"].get("changes", [])
            }  # fmt: skip
        ),
        "proposed_changes_active": False,
    }


def baselines_section(comparisons: Iterable[Mapping[str, Any]] | None) -> dict[str, Any]:
    """TAX-067 comparisons, counted per metric and kind; no taxpayer or values."""
    if comparisons is None:
        return {"source": BASELINE_VERSION, "included": False,
                "reason": "no peer-baseline comparisons supplied"}  # fmt: skip
    metrics: dict[str, dict[str, dict[str, int]]] = {}
    total = 0
    for item in comparisons:
        if not isinstance(item, Mapping) or item.get("baseline_version") != BASELINE_VERSION:
            raise FeedbackError("unsupported_comparison", "not a TAX-067 comparison")
        if item.get("risk_score") is not None or item.get("fraud_conclusion") is not None:
            raise FeedbackError("comparison_carries_a_score")
        total += 1
        for metric, entry in sorted((item.get("comparisons") or {}).items()):
            for kind in ("baseline", "peer_group"):
                part = entry.get(kind) or {}
                if part.get("state") == "insufficient_evidence":
                    outcome = "insufficient_evidence"
                else:
                    outcome = part.get("position")
                    if outcome not in POSITIONS:
                        raise FeedbackError("unknown_position", f"{metric}.{kind}")
                counts = metrics.setdefault(metric, {}).setdefault(
                    kind, dict.fromkeys((*POSITIONS, "insufficient_evidence"), 0)
                )
                counts[outcome] += 1
    return {
        "source": BASELINE_VERSION,
        "included": True,
        "comparisons": total,
        "by_metric": metrics,
        "note": "Counts of explainable comparisons only; outside the typical range is a "
        "question for review, not a risk indicator.",
    }


def build_pack(
    report: Mapping[str, Any],
    comparisons: Iterable[Mapping[str, Any]] | None = None,
    *,
    issued_at: str,
) -> dict[str, Any]:
    """The Risk Unit feedback pack from a TAX-066 report and optional TAX-067 results."""
    if report.get("calibration_version") != risk_calibration.CALIBRATION_VERSION:
        raise FeedbackError("unsupported_report", str(report.get("calibration_version")))
    body: dict[str, Any] = {
        "schema": SCHEMA,
        "contract_id": CONTRACT_ID,
        "authority": AUTHORITY,
        "audience": AUDIENCE,
        "route": ROUTE,
        "notice": ASSUMED_SHAPE_NOTICE,
        "statement": STATEMENT,
        "synthetic": bool(report.get("synthetic")),
        "issued_at": issued_at,
        "calibration": calibration_section(report),
        "peer_baselines": baselines_section(comparisons),
    }
    body["pack_sha256"] = _digest(body)
    validate_pack(body)
    return body


def _walk(value: Any, path: str) -> Iterable[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield f"{path}.{key}", str(key)
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")


def validate_pack(pack: Mapping[str, Any]) -> None:
    """Refuse a pack that is not feedback-only, aggregate and intact."""
    for key in ("schema", "contract_id", "authority", "route", "pack_sha256"):
        if key not in pack:
            raise FeedbackError("missing_field", key)
    if pack["schema"] != SCHEMA or pack["contract_id"] != CONTRACT_ID:
        raise FeedbackError("wrong_contract")
    if pack["authority"] != AUTHORITY:
        raise FeedbackError("wrong_authority", str(pack["authority"]))
    for path, key in _walk(pack, "pack"):
        if key in FORBIDDEN_KEYS:
            raise FeedbackError("forbidden_field", path)
        if key in IDENTIFYING_KEYS:
            raise FeedbackError("identifying_field", path)
    body = {k: v for k, v in pack.items() if k != "pack_sha256"}
    if _digest(body) != pack["pack_sha256"]:
        raise FeedbackError("digest_mismatch")


def write_pack(pack: Mapping[str, Any], path: Path) -> Path:
    """Write a validated pack. Never into the Jube rules directory or over a ruleset."""
    validate_pack(pack)
    target = path.resolve()
    rules_dir = risk_calibration.APPROVED_RULESET_PATH.resolve().parent
    if target == rules_dir or rules_dir in target.parents:
        raise FeedbackError("refused_path", "the approved rules directory")
    target.write_text(json.dumps(pack, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baselines", type=Path, help="JSON list of TAX-067 compare() results")
    parser.add_argument("--out", type=Path, help="write the pack here instead of printing it")
    parser.add_argument("--issued-at", default="2026-11-13T00:00:00Z")
    parser.add_argument("--check", action="store_true", help="build and validate only")
    args = parser.parse_args(argv)
    comparisons = None
    if args.baselines:
        comparisons = json.loads(args.baselines.read_text(encoding="utf-8"))
        if not isinstance(comparisons, list):
            parser.error("--baselines must hold a JSON list")
    try:
        result = risk_calibration.evaluate_fixtures()
        pack = build_pack(result.report, comparisons, issued_at=args.issued_at)
        if args.check:
            print(f"Risk Unit feedback pack valid ({pack['pack_sha256'][:12]})")
        elif args.out:
            print(write_pack(pack, args.out))
        else:
            print(json.dumps(pack, indent=2, sort_keys=True))
    except FeedbackError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
