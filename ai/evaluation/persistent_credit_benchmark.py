"""Sealed benchmark of the persistent VAT credit review (TAX-139, Challenge 2).

Same protocol as the TAX-118 benchmark (``assurance_benchmark``), on a new seed with the
``persistent_credit`` issue planted. The TAX-118 seal is untouched: without the
``persistent_credit`` parameter the generator produces exactly the sealed TAX-118
population, which ``tests/ai/test_persistent_credit.py`` checks.

``seal --seed N``  generate the population, keep world and answer key in
                   ``.benchmark/tax139/`` and commit only their hashes to
                   ``docs/evidence/TAX-139/manifest.json``.
``dev``            run the whole assurance system on the development split and score it.
``freeze``         fingerprint the rule, policy and pipeline files.
``test``           once, on the test split: detection of planted persistent credits, false
                   positives by look-alike group (exporters, capital-spending look-alikes,
                   other clean taxpayers, taxpayers with other issues), the naive
                   "credit in half the periods" rule, an ablation dropping each structural
                   test, the refund lanes, and the full TAX-118 scoring on this seed.
``--check``        fast self-test on a small population (``scripts/dev.py check``).

Everything is system output on synthetic data scored against a hidden answer key.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from ai.evaluation.assurance_benchmark import (
    RULE_FILES,
    _now,
    _pct,
    _rate,
    _write_json,
    canonical_sha256,
    error_analysis,
    file_sha256,
    run_system,
    score,
    split,
)
from ai.evaluation.assurance_population import PERSISTENT_CREDIT, generate
from ai.tools.persistent_credit import TESTS, load_policy, review_persistent_credit

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / ".benchmark" / "tax139"
EVIDENCE = ROOT / "docs/evidence/TAX-139"
MANIFEST = EVIDENCE / "manifest.json"
RESULTS = EVIDENCE / "results.json"
GENERATOR_FILE = "ai/evaluation/assurance_population.py"
TAX139_RULE_FILES = (
    *RULE_FILES,
    "ai/tools/persistent_credit.py",
    "ai/tools/persistent_credit_policy.json",
)
PARAMS: dict[str, Any] = {"persistent_credit": {"planted": 24, "capex_lookalikes": 24}}
GROUPS = ("exporter", "capex_lookalike", "other_clean", "other_issue", "planted")


def rules_fingerprint() -> dict[str, Any]:
    files = {f: file_sha256(ROOT / f) for f in TAX139_RULE_FILES}
    return {"fingerprint": canonical_sha256(files), "files": files}


def group(k: Mapping[str, Any]) -> str:
    counterparts = set(k.get("counterparts", []))
    if PERSISTENT_CREDIT in k["issues"]:
        return "planted"
    if k["issues"]:
        return "other_issue"
    if "capex_lookalike" in counterparts:
        return "capex_lookalike"
    if "structural_refund" in counterparts:
        return "exporter"
    return "other_clean"


def credit_score(
    world: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    key: Mapping[str, Any],
) -> dict[str, Any]:
    """Persistent-credit metrics by look-alike group, baseline and ablation."""
    tins = sorted(results)
    groups = {t: group(key["taxpayers"][t]) for t in tins}

    def by_group(flagged: Iterable[str]) -> dict[str, Any]:
        hit = set(flagged)
        return {
            g: _rate(
                sum(1 for t in tins if groups[t] == g and t in hit),
                sum(1 for t in tins if groups[t] == g),
            )
            for g in GROUPS
        }

    system = [t for t in tins if PERSISTENT_CREDIT in results[t]["flags"]]
    naive = [t for t in tins if results[t]["persistent_credit"]["persistent"]]
    rate = Decimal(world["rates_assumed"]["vat"])
    returns: dict[str, list[dict[str, Any]]] = {}
    for r in world["records"]["etax.vat_return"]:
        returns.setdefault(r["tin"], []).append(r)
    entries: dict[str, list[dict[str, Any]]] = {}
    for e in world["records"]["asycuda.entry"]:
        entries.setdefault(e["importer_tin"], []).append(e)
    policy = load_policy()
    ablation = {}
    for dropped in TESTS:
        pol = {
            **policy,
            "explanation_order": [x for x in policy["explanation_order"] if x != dropped],
        }
        flagged = [
            t
            for t in tins
            if review_persistent_credit(
                vat_returns=returns.get(t, []),
                vat_rate=rate,
                saft=world["submissions"][t]["saft"],
                asycuda_entries=entries.get(t, []),
                policy=pol,
            )["findings"]
        ]
        ablation[f"without_{dropped}"] = by_group(flagged)
    planted = [t for t in tins if groups[t] == "planted"]
    suppressed = sum(
        (Decimal(key["taxpayers"][t]["amount_at_risk"][PERSISTENT_CREDIT]) for t in planted),
        Decimal("0.00"),
    )
    unexplained = sum(
        (Decimal(results[t]["persistent_credit"]["totals"]["unexplained"]) for t in planted),
        Decimal("0.00"),
    )
    # Lanes of the exporters' valid claims (structural credit): must stay fast.
    exporter_fast = exporter_policy_fast = 0
    planted_claims = planted_not_fast = 0
    for t in tins:
        for c in results[t]["claims"]:
            truth = key["taxpayers"][t]["claims"][c["claim_id"]]
            lane = c["lane"]["lane"]
            if groups[t] == "exporter" and truth["policy_lane"] == "fast":
                exporter_policy_fast += 1
                exporter_fast += lane == "fast"
            if groups[t] == "planted":
                planted_claims += 1
                planted_not_fast += lane != "fast"
    explained_tests = {
        test: sum(
            1
            for t in tins
            if groups[t] != "planted"
            and results[t]["persistent_credit"]["persistent"]
            and Decimal(results[t]["persistent_credit"]["totals"][f"explained_by_{test}"]) > 0
        )
        for test in TESTS
    }
    return {
        "flagged_by_group": by_group(system),
        "naive_persistence_rule_by_group": by_group(naive),
        "ablation_flagged_by_group": ablation,
        "explanation_used_for_non_planted_persistent_credits": explained_tests,
        "every_credit_period_records_all_tests": all(
            len(p["tests"]) == len(TESTS)
            for t in tins
            for p in results[t]["persistent_credit"]["periods"]
            if Decimal(p["credit"]) > 0
        ),
        "amounts": {
            "planted_suppressed_output_vat": str(suppressed),
            "unexplained_credit_computed_for_planted": str(unexplained),
        },
        "lanes": {
            "exporter_policy_fast_claims_fast": _rate(exporter_fast, exporter_policy_fast),
            "planted_claims_not_fast": _rate(planted_not_fast, planted_claims),
        },
        "findings_eligible_for_assessment": any(
            f["route"]["eligible_for_assessment"]
            for r in results.values()
            for f in r["findings"]
            if f["issue"] == PERSISTENT_CREDIT
        ),
    }


# --------------------------------------------------------------------------- commands


def _load_sealed() -> tuple[dict, dict, dict]:
    manifest = json.loads(MANIFEST.read_text())
    world_path, key_path = WORK / "world.json", WORK / "answer_key.json"
    if not world_path.exists() or not key_path.exists():
        world, key = generate(int(manifest["seed"]), manifest["params_override"])
        _write_json(world_path, world)
        _write_json(key_path, key)
    world = json.loads(world_path.read_text())
    key = json.loads(key_path.read_text())
    if canonical_sha256(world) != manifest["dataset_sha256"]:
        raise SystemExit("dataset does not match the sealed hash")
    if canonical_sha256(key) != manifest["answer_key_sha256"]:
        raise SystemExit("answer key does not match the sealed hash")
    return manifest, world, key


def cmd_seal(seed: int) -> None:
    if MANIFEST.exists():
        raise SystemExit("already sealed; a new seal needs a new evidence directory")
    world, key = generate(seed, PARAMS)
    _write_json(WORK / "world.json", world)
    _write_json(WORK / "answer_key.json", key)
    groups = [group(v) for v in key["taxpayers"].values()]
    manifest = {
        "synthetic": True,
        "task": "TAX-139",
        "sealed_at": _now(),
        "seed": seed,
        "params_override": PARAMS,
        "generator_version": key["generator_version"],
        "generator_sha256": file_sha256(ROOT / GENERATOR_FILE),
        "dataset_sha256": canonical_sha256(world),
        "answer_key_sha256": canonical_sha256(key),
        "taxpayers": len(key["taxpayers"]),
        "split": {"dev": len(split(key, "dev")), "test": len(split(key, "test"))},
        "groups": {g: groups.count(g) for g in GROUPS},
        "claims_total": len(world["records"]["online_refunds.claim"]),
        "rules_at_seal": rules_fingerprint()["fingerprint"],
        "frozen": None,
        "test_run": None,
        "disclosure": (
            "The generator change, the answer key and the review were written by the same "
            "AI coding agent. Thresholds were set on debugging seeds 777 and 778 before "
            "sealing; only the dataset and answer-key hashes were committed before the "
            "development run; the rules were frozen before the single test run."
        ),
    }
    _write_json(MANIFEST, manifest)
    print(json.dumps({k: manifest[k] for k in ("seed", "split", "groups")}, indent=2))


def cmd_dev() -> None:
    manifest, world, key = _load_sealed()
    if manifest.get("test_run"):
        raise SystemExit("test split already run; development scoring is closed")
    results = run_system(world, split(key, "dev"))
    summary = {
        "run_at": _now(),
        "rules": rules_fingerprint()["fingerprint"],
        "persistent_credit": credit_score(world, results, key),
        "detection_by_issue": score(results, key)["detection_by_issue"],
        "errors": [e for e in error_analysis(results, key) if e["issue"] == PERSISTENT_CREDIT],
    }
    _write_json(EVIDENCE / "dev_summary.json", summary)
    print(json.dumps(summary, indent=2))


def cmd_freeze() -> None:
    manifest = json.loads(MANIFEST.read_text())
    if manifest.get("test_run"):
        raise SystemExit("test split already run")
    fp = rules_fingerprint()
    manifest["frozen"] = {"at": _now(), **fp}
    _write_json(MANIFEST, manifest)
    print(fp["fingerprint"])


def cmd_test() -> None:
    manifest, world, key = _load_sealed()
    if not manifest.get("frozen"):
        raise SystemExit("rules are not frozen")
    if manifest.get("test_run") or RESULTS.exists():
        raise SystemExit("the test split has already been run once")
    if rules_fingerprint()["fingerprint"] != manifest["frozen"]["fingerprint"]:
        raise SystemExit("rules changed after freezing")
    regenerated, regenerated_key = generate(int(manifest["seed"]), manifest["params_override"])
    reproducible = (
        canonical_sha256(regenerated) == manifest["dataset_sha256"]
        and canonical_sha256(regenerated_key) == manifest["answer_key_sha256"]
    )
    tins = split(key, "test")
    results = run_system(world, tins)
    full = score(results, key)
    errors = error_analysis(results, key)
    report = {
        "synthetic": True,
        "task": "TAX-139",
        "run_at": _now(),
        "seed": manifest["seed"],
        "rules_fingerprint": manifest["frozen"]["fingerprint"],
        "dataset_sha256": manifest["dataset_sha256"],
        "regenerated_from_seed_matches_seal": reproducible,
        "persistent_credit": credit_score(world, results, key),
        "assurance_system": {
            k: full[k]
            for k in (
                "detection_by_issue",
                "detection_overall",
                "false_positive_overall",
                "lanes",
                "legal_routes",
            )
        },
        "errors": errors,
    }
    _write_json(RESULTS, report)
    with (EVIDENCE / "errors.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["tin", "type", "issue", "reason"])
        writer.writeheader()
        writer.writerows(errors)
    manifest["test_run"] = {"at": report["run_at"], "results_sha256": file_sha256(RESULTS)}
    _write_json(MANIFEST, manifest)
    (EVIDENCE / "RESULTS.md").write_text(render_results(manifest, report), encoding="utf-8")
    print((EVIDENCE / "RESULTS.md").read_text())


LABELS = {
    "exporter": "Exporters (zero-rated share at or above 30%)",
    "capex_lookalike": "Capital-spending look-alikes",
    "other_clean": "Other clean taxpayers",
    "other_issue": "Taxpayers with another planted issue",
    "planted": "**Planted persistent credit**",
}


def render_results(manifest: Mapping[str, Any], report: Mapping[str, Any]) -> str:
    pc = report["persistent_credit"]
    sysm = report["assurance_system"]
    lines = [
        "# TAX-139 results: persistent VAT credit positions",
        "",
        "**Everything here is system output on synthetic data**, scored automatically "
        "against a hidden answer key. Thresholds are prototype assumptions, not RSL policy.",
        "",
        f"- Population: {manifest['taxpayers']} synthetic taxpayers, seed {manifest['seed']}, "
        f"with {manifest['groups']['planted']} planted persistent-credit taxpayers and "
        f"{manifest['groups']['capex_lookalike']} capital-spending look-alikes "
        f"({manifest['params_override']}).",
        f"- Development split {manifest['split']['dev']}, test split "
        f"{manifest['split']['test']} (run once, reported here).",
        f"- Sealed {manifest['sealed_at']}: dataset `{manifest['dataset_sha256'][:16]}...`, "
        f"answer key `{manifest['answer_key_sha256'][:16]}...`.",
        f"- Rules frozen {manifest['frozen']['at']}: fingerprint "
        f"`{manifest['frozen']['fingerprint'][:16]}...`; test run {report['run_at']}.",
        f"- Regenerating from the seed reproduces the sealed data: "
        f"{report['regenerated_from_seed_matches_seal']}.",
        "",
        "## Flagged for an unexplained persistent credit (test split)",
        "",
        "Rows other than the planted group are false positives.",
        "",
        "| Group | System (explanations first) | Naive rule: credit in half the periods |",
        "|---|---|---|",
    ]
    for g in GROUPS:
        lines.append(
            f"| {LABELS[g]} | {_pct(pc['flagged_by_group'][g])} | "
            f"{_pct(pc['naive_persistence_rule_by_group'][g])} |"
        )
    lines += [
        "",
        "## Ablation: drop one structural test (test split, false positives)",
        "",
        "| Test dropped | Exporters | Capital look-alikes | Other clean | Other issue | "
        "Planted found |",
        "|---|---|---|---|---|---|",
    ]
    for name, row in pc["ablation_flagged_by_group"].items():
        lines.append(
            f"| {name.removeprefix('without_')} | {_pct(row['exporter'])} | "
            f"{_pct(row['capex_lookalike'])} | {_pct(row['other_clean'])} | "
            f"{_pct(row['other_issue'])} | {_pct(row['planted'])} |"
        )
    used = pc["explanation_used_for_non_planted_persistent_credits"]
    lines += [
        "",
        "Non-planted taxpayers with a persistent credit whose credit each test helped explain: "
        + ", ".join(f"{k} {v}" for k, v in used.items())
        + ".",
        f"Every credit period records all three tests: "
        f"{pc['every_credit_period_records_all_tests']}.",
        "",
        "## Lanes and amounts (test split)",
        "",
        f"- Exporters' claims whose policy-correct lane is fast that got fast: "
        f"{_pct(pc['lanes']['exporter_policy_fast_claims_fast'])}",
        f"- Planted taxpayers' claims kept out of the fast lane: "
        f"{_pct(pc['lanes']['planted_claims_not_fast'])}",
        f"- Output VAT suppressed in planted returns: "
        f"M{Decimal(pc['amounts']['planted_suppressed_output_vat']):,.0f}; unexplained credit "
        f"the review computed for them: "
        f"M{Decimal(pc['amounts']['unexplained_credit_computed_for_planted']):,.0f} (the "
        "remainder after the structural tests, not an assessment).",
        f"- Any persistent-credit finding eligible for assessment: "
        f"{pc['findings_eligible_for_assessment']} (by design).",
        "",
        "## Whole assurance system on this seed (test split)",
        "",
        "| Issue | Detected | False positives |",
        "|---|---|---|",
    ]
    for issue, v in sysm["detection_by_issue"].items():
        lines.append(f"| {issue} | {_pct(v['detected'])} | {_pct(v['false_positive'])} |")
    lines += [
        "",
        f"- Clean taxpayers flagged for anything: "
        f"{_pct(sysm['false_positive_overall']['clean_taxpayers_flagged'])}",
        f"- Lane matches the policy-correct lane: {_pct(sysm['lanes']['accuracy'])}",
        f"- Correct findings routed to an expected candidate route: "
        f"{_pct(sysm['legal_routes']['correct_candidate_route_or_policy_gap'])}",
        "",
        "## Errors (test split)",
        "",
    ]
    errs = [e for e in report["errors"] if e["issue"] == PERSISTENT_CREDIT]
    if not errs:
        lines.append("No persistent-credit misses or false positives.")
    for e in errs:
        lines.append(f"- {e['type']} {e['tin']}: {e['reason']}")
    other = len(report["errors"]) - len(errs)
    lines += [
        "",
        f"{other} errors on the other five issues are listed in `errors.csv`; their causes "
        "are the ones reported for TAX-118.",
        "",
        "## Limits",
        "",
        "- The planted mechanism is one pattern (standard-rated sales under-declared in both "
        "the return and the books). A taxpayer whose structural explanation is itself false "
        "(fake exports, fictitious assets) is not detected here: capital spending comes from "
        "the taxpayer's books, so the review asks the officer to verify the assets.",
        "- Thresholds were set on debugging seeds by the same agent that wrote the generator.",
        "",
    ]
    return "\n".join(lines)


def cmd_check() -> None:
    params = {
        "n_taxpayers": 40,
        "n_dev": 10,
        "issue_counts": {"fake_invoices": 2, "untaxed_imported_services": 2, "mixed": 1},
        "persistent_credit": {"planted": 4, "capex_lookalikes": 4},
    }
    world, key = generate(13, params)
    again, again_key = generate(13, params)
    assert canonical_sha256(world) == canonical_sha256(again), "generator not reproducible"
    assert canonical_sha256(key) == canonical_sha256(again_key), "key not reproducible"
    visible = json.dumps(world)
    for secret in ("capex_lookalike", "suppression", "suppressed_output_vat", "capex_months"):
        assert secret not in visible, f"answer-key field {secret} leaked into the world"
    results = run_system(world, sorted(key["taxpayers"]))
    pc = credit_score(world, results, key)
    assert pc["every_credit_period_records_all_tests"]
    assert not pc["findings_eligible_for_assessment"]
    print(
        "persistent credit benchmark check passed: planted flagged "
        f"{pc['flagged_by_group']['planted']['rate']}, exporters flagged "
        f"{pc['flagged_by_group']['exporter']['rate']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("seal")
    p.add_argument("--seed", type=int, required=True)
    sub.add_parser("dev")
    sub.add_parser("freeze")
    sub.add_parser("test")
    args = parser.parse_args(argv)
    if args.check:
        cmd_check()
    elif args.command == "seal":
        cmd_seal(args.seed)
    elif args.command == "dev":
        cmd_dev()
    elif args.command == "freeze":
        cmd_freeze()
    elif args.command == "test":
        cmd_test()
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
