"""Re-measure imported-services detection after TAX-141 (synthetic, not a blind run).

TAX-118's sealed test missed imported services whose fees were booked as general
operating expenses: the three-way match only tested service accounts. TAX-141 adds a
question when the bank narration or the Central Bank cross-border report describes a
payment as services and none of it is booked or declared as services.

This script scores the current rules for ``untaxed_imported_services`` only:

* the TAX-118 sealed seed's test split (the same taxpayers as the sealed run, but the rules
  were changed after that run was read, so this is **not** a blind result);
* a fresh seed's whole population, for false positives on taxpayers the rules were not
  written against;
* each with and without the Central Bank ``purpose`` field (generator opt-in), and with
  the bank narration withheld, to show what each description contributes on its own.

``python -m ai.evaluation.imported_services_remeasure`` writes
``docs/evidence/TAX-141/remeasure.json``. Figures are system output on synthetic data.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from ai.evaluation.assurance_benchmark import error_analysis, run_system, score, split
from ai.evaluation.assurance_population import generate

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/evidence/TAX-141/remeasure.json"
SEALED = ROOT / "docs/evidence/TAX-118/results.json"
SEALED_SEED = 20261106
FRESH_SEED = 20261112
ISSUE = "untaxed_imported_services"
CODE = "foreign_service_payments_outside_service_accounts"


def withhold_narration(world: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of the world whose bank exports carry no narration."""
    out = json.loads(json.dumps(world))
    for sub in out["submissions"].values():
        text = sub.get("foreign_payments_csv")
        if not text:
            continue
        rows = list(csv.reader(io.StringIO(text)))
        col = rows[0].index("narration")
        for row in rows[1:]:
            row[col] = "Payment"
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows)
        sub["foreign_payments_csv"] = buf.getvalue()
    return out


def measure(seed: int, *, test_split: bool, cb_purpose: bool, narration: bool) -> dict[str, Any]:
    world, key = generate(seed, {"central_bank_purpose": True} if cb_purpose else None)
    if not narration:
        world = withhold_narration(world)
    tins = split(key, "test") if test_split else sorted(key["taxpayers"])
    results = run_system(world, tins)
    report = score(results, key)
    tk = key["taxpayers"]
    disguised = [t for t in tins if tk[t].get("imported_services_disguise")]
    raised_on = sorted(t for t in tins if any(f["code"] == CODE for f in results[t]["findings"]))
    return {
        "seed": seed,
        "population": "test split" if test_split else "whole population",
        "taxpayers": len(tins),
        "central_bank_purpose": cb_purpose,
        "bank_narration": narration,
        "detection": report["detection_by_issue"][ISSUE],
        "fees_booked_as_expenses": {
            "planted": len(disguised),
            "detected": sum(ISSUE in results[t]["flags"] for t in disguised),
        },
        "new_question_raised": {
            "taxpayers": len(raised_on),
            "with_planted_issue": sum(ISSUE in tk[t]["issues"] for t in raised_on),
            "without_planted_issue": sum(ISSUE not in tk[t]["issues"] for t in raised_on),
        },
        "clean_taxpayers_flagged": report["false_positive_overall"]["clean_taxpayers_flagged"],
        "amount_at_risk": {
            k: report["amount_at_risk"][k]
            for k in (
                "imported_services_planted_gap",
                "imported_services_gap_computed_for_planted_taxpayers",
            )
        },
        "errors": [e for e in error_analysis(results, key) if e["issue"] == ISSUE],
    }


def before() -> dict[str, Any]:
    sealed = json.loads(SEALED.read_text())
    main = sealed["main"]
    return {
        "source": "docs/evidence/TAX-118/results.json (sealed blind test run)",
        "seed": sealed["seed"],
        "run_at": sealed["run_at"],
        "detection": main["detection_by_issue"][ISSUE],
        "fees_booked_as_expenses_missed": sum(
            1 for e in sealed["errors"] if e["issue"] == ISSUE and e["type"] == "miss"
        ),
        "clean_taxpayers_flagged": main["false_positive_overall"]["clean_taxpayers_flagged"],
        "amount_at_risk": {
            k: main["amount_at_risk"][k]
            for k in (
                "imported_services_planted_gap",
                "imported_services_gap_computed_for_planted_taxpayers",
            )
        },
    }


def run() -> dict[str, Any]:
    runs = [
        measure(seed, test_split=seed == SEALED_SEED, cb_purpose=cb, narration=nar)
        for seed in (SEALED_SEED, FRESH_SEED)
        for cb, nar in ((False, True), (True, True), (False, False), (True, False))
    ]
    return {
        "synthetic": True,
        "task": "TAX-141",
        "blind": False,
        "disclosure": (
            "The sealed TAX-118 test split was read before these rules were written, so the "
            "after figures on that split are not a blind result. The fresh seed was generated "
            "after the rules were fixed and is the better guide to false positives."
        ),
        "before": before(),
        "after": runs,
    }


def main() -> int:
    report = run()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    for r in report["after"]:
        d = r["detection"]
        print(
            f"seed {r['seed']} {r['population']:<16} cb_purpose={r['central_bank_purpose']!s:<5} "
            f"narration={r['bank_narration']!s:<5} detected {d['detected']['count']}/"
            f"{d['detected']['of']} fp {d['false_positive']['count']}/{d['false_positive']['of']}"
            f" gap {Decimal(r['amount_at_risk']['imported_services_gap_computed_for_planted_taxpayers']):,.2f}"
            f" of {Decimal(r['amount_at_risk']['imported_services_planted_gap']):,.2f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
