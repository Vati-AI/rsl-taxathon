"""Blind benchmark of the assurance system on the synthetic population (TAX-118).

Protocol (each step is a command; ``.benchmark/`` is git-ignored working space):

``seal``    generate the population from a seed, write the world and answer key to
            ``.benchmark/`` and commit only their SHA-256 hashes to
            ``docs/evidence/TAX-118/manifest.json``.
``dev``     run the system on the 100-taxpayer development split and score it. Rules may
            be tuned on this split only.
``freeze``  record a fingerprint of every rule, policy and pipeline file in the manifest.
``test``    refuse unless the regenerated dataset matches the sealed hashes, the rule
            fingerprint matches the frozen one and the test split has not been run; then run
            the 200-taxpayer test split once, with ablation (no RSL score), Lekuka coverage
            10/50/90% and the simple refund-size baselines, and write the results.
``human``   write case files and a timing sheet for the manual baseline, and system case
            packs with a scoring form for an outside reviewer.
``--check`` fast self-test on a small population (used by ``scripts/dev.py check``).

The system (``assurance_pipeline.assess_taxpayer``) never receives the answer key; the
key is read only by the scorer here. Metrics are system output on synthetic data; manual
baseline times are stated assumptions until a person times real cases.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import statistics
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ai.evaluation.assurance_pipeline import assess_taxpayer, build_sources
from ai.evaluation.assurance_population import ISSUES, generate

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / ".benchmark"
EVIDENCE = ROOT / "docs/evidence/TAX-118"
MANIFEST = EVIDENCE / "manifest.json"
RESULTS = EVIDENCE / "results.json"
RULE_FILES = (
    "ai/evaluation/assurance_pipeline.py",
    "ai/tools/idms_match.py",
    "ai/tools/source_recompute.py",
    "ai/tools/refund_triage.py",
    "ai/tools/refund_triage_policy.json",
    "ai/tools/foreign_payments.py",
    "ai/tools/related_party_signals.py",
    "ai/tools/work_arrangement.py",
    "ai/tools/refund_reconciliation.py",
    "ai/workflows/rule_register.py",
    "ai/workflows/rule_register.candidates.json",
    "ai/workflows/policy_gap_register.json",
    "integration/rsl_sources/contracts.py",
    "integration/rsl_sources/fixture_source.py",
)
GENERATOR_FILE = "ai/evaluation/assurance_population.py"
SIMPLE_RULE_THRESHOLDS = ("100000.00", "500000.00", "1000000.00")
COVERAGES = ("0.10", "0.50", "0.90")
LANES = ("fast", "standard", "investigation")
# Manual baseline time model: stated assumptions, replaced by a timed human run.
MANUAL_MINUTES = {
    "per_claim": 20,
    "per_claim_line": 3,
    "per_foreign_counterparty": 15,
    "cit_review": 30,
    "per_agreement": 20,
}


def canonical_sha256(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rules_fingerprint() -> dict[str, Any]:
    files = {f: file_sha256(ROOT / f) for f in RULE_FILES}
    return {"fingerprint": canonical_sha256(files), "files": files}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- running


def run_system(
    world: Mapping[str, Any],
    tins: Iterable[str],
    *,
    use_score: bool = True,
    unavailable: Iterable[str] = (),
) -> dict[str, dict[str, Any]]:
    sources = build_sources(world, unavailable)
    return {tin: assess_taxpayer(world, sources, tin, use_score=use_score) for tin in tins}


def split(key: Mapping[str, Any], name: str) -> list[str]:
    return sorted(t for t, v in key["taxpayers"].items() if v["split"] == name)


# --------------------------------------------------------------------------- scoring


def _rate(n: int, d: int) -> dict[str, Any]:
    return {"count": n, "of": d, "rate": None if d == 0 else round(n / d, 4)}


def score(results: Mapping[str, Mapping[str, Any]], key: Mapping[str, Any]) -> dict[str, Any]:
    """Score system results for the given taxpayers against the answer key."""
    tk = {t: key["taxpayers"][t] for t in results}
    out: dict[str, Any] = {"taxpayers": len(results)}
    # Taxpayer-level detection.
    per_issue = {}
    for issue in ISSUES:
        planted = [t for t in results if issue in tk[t]["issues"]]
        found = [t for t in planted if issue in results[t]["flags"]]
        others = [t for t in results if issue not in tk[t]["issues"]]
        wrong = [t for t in others if issue in results[t]["flags"]]
        per_issue[issue] = {
            "detected": _rate(len(found), len(planted)),
            "false_positive": _rate(len(wrong), len(others)),
        }
    out["detection_by_issue"] = per_issue
    with_issue = [t for t in results if tk[t]["issues"]]
    instances = sum(len(tk[t]["issues"]) for t in with_issue)
    found_instances = sum(len(set(tk[t]["issues"]) & set(results[t]["flags"])) for t in with_issue)
    any_found = [t for t in with_issue if set(tk[t]["issues"]) & set(results[t]["flags"])]
    clean = [t for t in results if not tk[t]["issues"]]
    clean_flagged = [t for t in clean if results[t]["flags"]]
    mixed = [t for t in with_issue if len(tk[t]["issues"]) == 2]
    mixed_both = [t for t in mixed if set(tk[t]["issues"]) <= set(results[t]["flags"])]
    out["detection_overall"] = {
        "planted_issue_instances": _rate(found_instances, instances),
        "taxpayers_with_any_issue_flagged": _rate(len(any_found), len(with_issue)),
        "mixed_taxpayers_both_found": _rate(len(mixed_both), len(mixed)),
    }
    out["false_positive_overall"] = {
        "clean_taxpayers_flagged": _rate(len(clean_flagged), len(clean))
    }
    # Claim-level lanes and amounts.
    confusion: Counter[tuple[str, str]] = Counter()
    valid_total = valid_fast = policy_fast = policy_fast_got_fast = 0
    fake_claims = fake_not_fast = fake_investigation = 0
    released_valid = leaked = valid_amount = Decimal("0.00")
    abs_err: list[Decimal] = []
    for t in results:
        for c in results[t]["claims"]:
            truth = tk[t]["claims"][c["claim_id"]]
            lane = c["lane"]["lane"]
            confusion[(truth["policy_lane"], lane)] += 1
            fake = Decimal(truth["fake_vat"])
            valid = Decimal(truth["valid_refund"])
            undisputed = Decimal(c["lane"]["undisputed_amount"]["amount"])
            valid_amount += valid
            released_valid += min(undisputed, valid)
            if fake > 0:
                fake_claims += 1
                fake_not_fast += lane != "fast"
                fake_investigation += lane == "investigation"
                leaked += max(Decimal("0.00"), undisputed - valid)
            else:
                valid_total += 1
                valid_fast += lane == "fast"
                abs_err.append(abs(undisputed - valid))
            if truth["policy_lane"] == "fast":
                policy_fast += 1
                policy_fast_got_fast += lane == "fast"
    total_claims = sum(confusion.values())
    correct = sum(v for (a, b), v in confusion.items() if a == b)
    out["lanes"] = {
        "accuracy": _rate(correct, total_claims),
        "confusion_truth_vs_system": {f"{a}->{b}": v for (a, b), v in sorted(confusion.items())},
        "valid_claims_fast": _rate(valid_fast, valid_total),
        "policy_fast_claims_fast": _rate(policy_fast_got_fast, policy_fast),
        "claims_with_fake_invoices_not_fast": _rate(fake_not_fast, fake_claims),
        "claims_with_fake_invoices_investigation": _rate(fake_investigation, fake_claims),
    }
    out["refund_amounts"] = {
        "valid_refund_total": str(valid_amount),
        "valid_refund_in_undisputed_portion": str(released_valid),
        "share_of_valid_refund_released_early": None
        if not valid_amount
        else round(float(released_valid / valid_amount), 4),
        "fake_vat_leaked_into_undisputed_portion": str(leaked),
        "valid_claims_undisputed_within_m1_of_valid_refund": _rate(
            sum(e <= 1 for e in abs_err), len(abs_err)
        ),
        "valid_claims_undisputed_mean_abs_error": str(
            (sum(abs_err, Decimal("0")) / len(abs_err)).quantize(Decimal("0.01"))
            if abs_err
            else Decimal("0")
        ),
    }
    # Amount at risk.
    fake_planted = sum(
        (Decimal(tk[t]["amount_at_risk"].get("fake_invoices", "0")) for t in results), Decimal("0")
    )
    fake_in_found = sum(
        (
            Decimal(tk[t]["amount_at_risk"].get("fake_invoices", "0"))
            for t in results
            if "fake_invoices" in results[t]["flags"]
        ),
        Decimal("0"),
    )
    fake_flagged = sum(
        (
            Decimal(results[t]["flagged_amounts"]["fake_invoices"])
            for t in results
            if "fake_invoices" in tk[t]["issues"]
        ),
        Decimal("0"),
    )
    imp_planted = sum(
        (Decimal(tk[t]["amount_at_risk"].get("untaxed_imported_services", "0")) for t in results),
        Decimal("0"),
    )
    imp_flagged = sum(
        (
            Decimal(results[t]["flagged_amounts"]["untaxed_imported_services"])
            for t in results
            if "untaxed_imported_services" in tk[t]["issues"]
        ),
        Decimal("0"),
    )
    out["amount_at_risk"] = {
        "fake_invoices_planted_vat": str(fake_planted),
        "fake_invoices_planted_vat_in_detected_taxpayers": str(fake_in_found),
        "fake_invoices_vat_disputed_by_system_for_planted_taxpayers": str(fake_flagged),
        "imported_services_planted_gap": str(imp_planted),
        "imported_services_gap_computed_for_planted_taxpayers": str(imp_flagged),
    }
    # Recompute: claims whose fakes were left out of the books.
    unbooked = caught = 0
    clean_claims_not_in_books = clean_claims = 0
    for t in results:
        for c in results[t]["claims"]:
            truth = tk[t]["claims"][c["claim_id"]]
            missing = bool(c["lane"]["inputs"].get("recompute", {}).get("lines_not_in_books"))
            if any(f and not f["booked"] for f in truth["fake_types"]):
                unbooked += 1
                caught += missing
            elif Decimal(truth["fake_vat"]) == 0:
                clean_claims += 1
                clean_claims_not_in_books += missing
    clean_periods = sum(len(results[t]["recompute"]["comparison"]) // 3 for t in clean)
    clean_excess = sum(len([f for f in results[t]["recompute"]["findings"]]) for t in clean)
    out["recompute"] = {
        "claims_with_unbooked_fakes_caught_by_recompute": _rate(caught, unbooked),
        "clean_taxpayer_periods_with_a_recompute_finding": _rate(clean_excess, clean_periods),
        "valid_claims_with_a_line_not_in_books": _rate(clean_claims_not_in_books, clean_claims),
    }
    # Legal routes.
    routed = route_ok = 0
    for t in results:
        for f in results[t]["findings"]:
            issue = f["issue"]
            if issue not in tk[t]["issues"]:
                continue
            expected = set(tk[t]["expected_routes"].get(issue, []))
            got = {c["rule_id"] for c in f["route"]["candidates"]} | set(f["route"]["policy_gaps"])
            routed += 1
            route_ok += bool(expected & got) and not f["route"]["eligible_for_assessment"]
    out["legal_routes"] = {
        "correct_candidate_route_or_policy_gap": _rate(route_ok, routed),
        "any_finding_eligible_for_assessment": any(
            f["route"]["eligible_for_assessment"] for r in results.values() for f in r["findings"]
        ),
    }
    # Time.
    seconds = [r["seconds"] for r in results.values()]
    manual = [manual_minutes(results[t]) for t in results]
    out["time"] = {
        "system_seconds_per_taxpayer_mean": round(statistics.mean(seconds), 4),
        "system_seconds_per_taxpayer_p95": round(
            sorted(seconds)[int(0.95 * (len(seconds) - 1))], 4
        ),
        "manual_minutes_per_taxpayer_assumed_mean": round(statistics.mean(manual), 1),
        "manual_model": MANUAL_MINUTES,
        "manual_status": "assumption_until_timed_by_a_person",
    }
    return out


def manual_minutes(result: Mapping[str, Any]) -> int:
    m = MANUAL_MINUTES
    minutes = m["cit_review"]
    for c in result["claims"]:
        minutes += m["per_claim"] + m["per_claim_line"] * len(c["match"]["lines"])
    minutes += m["per_foreign_counterparty"] * len(result["three_way"]["rows"])
    minutes += m["per_agreement"] * len(result["work_arrangement"].get("referrals", []))
    return minutes


def simple_rule(
    results: Mapping[str, Mapping[str, Any]], key: Mapping[str, Any], threshold: str
) -> dict[str, Any]:
    x = Decimal(threshold)
    tk = {t: key["taxpayers"][t] for t in results}
    flagged = {
        t
        for t in results
        if any(Decimal(c["lane"]["claimed_amount"]["amount"]) > x for c in results[t]["claims"])
    }
    with_issue = [t for t in results if tk[t]["issues"]]
    clean = [t for t in results if not tk[t]["issues"]]
    valid = fake = valid_pay = fake_hold = 0
    for t in results:
        for c in results[t]["claims"]:
            over = Decimal(c["lane"]["claimed_amount"]["amount"]) > x
            if Decimal(tk[t]["claims"][c["claim_id"]]["fake_vat"]) > 0:
                fake += 1
                fake_hold += over
            else:
                valid += 1
                valid_pay += not over
    return {
        "threshold": threshold,
        "taxpayers_with_any_issue_flagged": _rate(
            len([t for t in with_issue if t in flagged]), len(with_issue)
        ),
        "by_issue": {
            i: _rate(
                len([t for t in results if i in tk[t]["issues"] and t in flagged]),
                len([t for t in results if i in tk[t]["issues"]]),
            )
            for i in ISSUES
        },
        "clean_taxpayers_flagged": _rate(len([t for t in clean if t in flagged]), len(clean)),
        "valid_claims_paid_without_audit": _rate(valid_pay, valid),
        "claims_with_fake_invoices_held": _rate(fake_hold, fake),
    }


def error_analysis(results: Mapping[str, Mapping[str, Any]], key: Mapping[str, Any]):
    rows = []
    for t in sorted(results):
        k = key["taxpayers"][t]
        flags = set(results[t]["flags"])
        for issue in sorted(set(k["issues"]) - flags):
            rows.append(
                {
                    "tin": t,
                    "type": "miss",
                    "issue": issue,
                    "reason": _miss_reason(issue, k, results[t]),
                }
            )
        for issue in sorted(flags - set(k["issues"])):
            rows.append(
                {
                    "tin": t,
                    "type": "false_positive",
                    "issue": issue,
                    "reason": _fp_reason(issue, k, results[t]),
                }
            )
    return rows


def _miss_reason(issue: str, k: Mapping[str, Any], r: Mapping[str, Any]) -> str:
    if issue == "fake_invoices":
        strategy = k.get("fake_strategy")
        if strategy == "registered_booked":
            return (
                "Fakes from a registered supplier not integrated with Lekuka, booked in "
                "the ledger and backed by forged copies: no independent record contradicts "
                "them at this coverage."
            )
        return f"Fake strategy {strategy}: no line, recompute or score indicator fired."
    if issue == "untaxed_imported_services":
        if k.get("imported_services_disguise"):
            return (
                "Foreign fees were booked as general operating expenses, so the books "
                "do not show them as services and the three-way match did not test them."
            )
        return "Three-way gap stayed within tolerance."
    if issue == "disguised_employment":
        if "permit_record_missing" in k.get("notes", []):
            return (
                "No permit record for the individual, so presence could not be compared "
                "with the consultancy terms; no conflicting fact, no referral."
            )
        return "Facts did not conflict, so no specialist referral."
    if issue == "related_party_loss":
        return "Loss or fee-growth trigger did not fire."
    return "Interest-free loan trigger did not fire."


def _fp_reason(issue: str, k: Mapping[str, Any], r: Mapping[str, Any]) -> str:
    counterparts = set(k.get("counterparts", []))
    if issue == "untaxed_imported_services":
        if "wht_payee_reference_variant" in k.get("noise_taxpayer", []):
            return (
                "Withholding was declared under a different payee reference, so it did not "
                "join to the counterparty; an officer would resolve it with one question."
            )
        return "Imported-services gap above tolerance for a taxpayer with no planted issue."
    if issue == "related_party_loss":
        if "genuine_losses_undocumented" in counterparts:
            return (
                "Genuine losses with rising related-party fees but no explanation document "
                "supplied: the trigger fires and a human review is the intended outcome."
            )
        return "Loss trigger fired for a taxpayer with no planted issue."
    if issue == "disguised_employment":
        return (
            "A genuine consultant stayed 183 days or more, so permit presence conflicts "
            "with the fixed-term contract and the case is referred to a specialist."
        )
    if issue == "fake_invoices":
        lanes = [c for c in r["claims"] if c["lane"]["lane"] == "investigation"]
        if lanes:
            return (
                "RSL score at or above the high level together with an unsupported line "
                "(missing copy or supplier gap) moved a valid claim to investigation."
            )
        return "An authenticity indicator fired on a valid claim."
    return "Policy-gap trigger fired without a planted interest-free loan."


# --------------------------------------------------------------------------- commands


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_sealed() -> tuple[dict, dict, dict]:
    manifest = json.loads(MANIFEST.read_text())
    world_path, key_path = WORK / "world.json", WORK / "answer_key.json"
    if not world_path.exists() or not key_path.exists():
        world, key = generate(int(manifest["seed"]))
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
    world, key = generate(seed)
    _write_json(WORK / "world.json", world)
    _write_json(WORK / "answer_key.json", key)
    counts = Counter(i for v in key["taxpayers"].values() for i in v["issues"])
    manifest = {
        "synthetic": True,
        "task": "TAX-118",
        "sealed_at": _now(),
        "seed": seed,
        "generator_version": key["generator_version"],
        "generator_sha256": file_sha256(ROOT / GENERATOR_FILE),
        "params": key["params"],
        "dataset_sha256": canonical_sha256(world),
        "answer_key_sha256": canonical_sha256(key),
        "taxpayers": len(key["taxpayers"]),
        "split": {"dev": len(split(key, "dev")), "test": len(split(key, "test"))},
        "issue_instances_total": dict(sorted(counts.items())),
        "claims_total": len(world["records"]["online_refunds.claim"]),
        "rules_at_seal": rules_fingerprint()["fingerprint"],
        "frozen": None,
        "test_run": None,
        "disclosure": (
            "The generator, the answer key and the rules were all written by the same AI "
            "coding agent. Only the dataset and answer-key hashes were committed before "
            "tuning; the rules were tuned on the development split and frozen before the "
            "single test run. A small debugging population (seed 777) was used before "
            "sealing. A team member can rerun with a private seed for a blind check."
        ),
    }
    _write_json(MANIFEST, manifest)
    print(
        json.dumps(
            {
                k: manifest[k]
                for k in ("seed", "dataset_sha256", "answer_key_sha256", "split", "claims_total")
            },
            indent=2,
        )
    )


def cmd_dev() -> None:
    manifest, world, key = _load_sealed()
    if manifest.get("test_run"):
        raise SystemExit("test split already run; development scoring is closed")
    results = run_system(world, split(key, "dev"))
    report = score(results, key)
    report["errors"] = error_analysis(results, key)
    _write_json(WORK / "dev_report.json", report)
    _write_json(
        EVIDENCE / "dev_summary.json",
        {
            "run_at": _now(),
            "rules": rules_fingerprint()["fingerprint"],
            **{k: v for k, v in report.items() if k != "errors"},
            "error_counts": Counter(f"{e['type']}:{e['issue']}" for e in report["errors"]),
        },
    )
    print(
        json.dumps(
            {k: report[k] for k in ("detection_by_issue", "false_positive_overall", "lanes")},
            indent=2,
        )
    )
    for e in report["errors"]:
        print(f"{e['type']:15} {e['issue']:28} {e['reason']}")


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
    regenerated, regenerated_key = generate(int(manifest["seed"]))
    reproducible = (
        canonical_sha256(regenerated) == manifest["dataset_sha256"]
        and canonical_sha256(regenerated_key) == manifest["answer_key_sha256"]
    )
    tins = split(key, "test")
    main = run_system(world, tins)
    report: dict[str, Any] = {
        "synthetic": True,
        "task": "TAX-118",
        "run_at": _now(),
        "seed": manifest["seed"],
        "rules_fingerprint": manifest["frozen"]["fingerprint"],
        "dataset_sha256": manifest["dataset_sha256"],
        "regenerated_from_seed_matches_seal": reproducible,
        "main": score(main, key),
        "errors": error_analysis(main, key),
    }
    no_score = run_system(world, tins, use_score=False)
    report["ablation_without_rsl_score"] = score(no_score, key)
    degradation = {}
    for coverage in COVERAGES:
        w, k = generate(int(manifest["seed"]), {"lekuka_coverage": coverage})
        res = run_system(w, tins)
        s = score(res, k)
        degradation[coverage] = {
            "fake_invoices_detected": s["detection_by_issue"]["fake_invoices"]["detected"],
            "clean_taxpayers_flagged": s["false_positive_overall"]["clean_taxpayers_flagged"],
            "lane_accuracy": s["lanes"]["accuracy"],
            "valid_claims_fast": s["lanes"]["valid_claims_fast"],
            "claims_with_fake_invoices_not_fast": s["lanes"]["claims_with_fake_invoices_not_fast"],
        }
    report["lekuka_coverage_degradation"] = degradation
    report["simple_rule_baselines"] = [simple_rule(main, key, x) for x in SIMPLE_RULE_THRESHOLDS]
    _write_json(RESULTS, report)
    with (EVIDENCE / "errors.csv").open("w", newline="", encoding="utf-8") as fh:
        err_writer = csv.DictWriter(fh, fieldnames=["tin", "type", "issue", "reason"])
        err_writer.writeheader()
        err_writer.writerows(report["errors"])
    manifest["test_run"] = {"at": report["run_at"], "results_sha256": file_sha256(RESULTS)}
    _write_json(MANIFEST, manifest)
    (EVIDENCE / "RESULTS.md").write_text(render_results(manifest, report), encoding="utf-8")
    print((EVIDENCE / "RESULTS.md").read_text())


def _pct(r: Mapping[str, Any]) -> str:
    if r["rate"] is None:
        return "n/a"
    return f"{r['rate'] * 100:.1f}% ({r['count']}/{r['of']})"


def _m(value: str) -> str:
    return f"M{Decimal(value):,.0f}"


def render_results(manifest: Mapping[str, Any], report: Mapping[str, Any]) -> str:
    m = report["main"]
    ab = report["ablation_without_rsl_score"]
    lines = [
        "# TAX-118 results: synthetic benchmark of the refund and MNE assurance checks",
        "",
        "**Everything here is system output on synthetic data**, scored automatically "
        "against a hidden answer key. Manual-baseline times are stated assumptions until a "
        "person times real cases. Nothing here is real RSL performance.",
        "",
        f"- Population: {manifest['taxpayers']} synthetic FMCG taxpayers x "
        f"{manifest['params']['months']} months, {manifest['claims_total']} refund claims; "
        f"seed {manifest['seed']}.",
        f"- Development split {manifest['split']['dev']} taxpayers (tuning), test split "
        f"{manifest['split']['test']} taxpayers (run once, reported here).",
        f"- Sealed {manifest['sealed_at']}: dataset `{manifest['dataset_sha256'][:16]}...`, "
        f"answer key `{manifest['answer_key_sha256'][:16]}...`.",
        f"- Rules frozen {manifest['frozen']['at']}: fingerprint "
        f"`{manifest['frozen']['fingerprint'][:16]}...`; test run {report['run_at']}.",
        f"- Regenerating from the seed reproduces the sealed data: "
        f"{report['regenerated_from_seed_matches_seal']}.",
        f"- Lekuka coverage {manifest['params']['lekuka_coverage']}; synthetic rates VAT "
        f"{manifest['params']['vat_rate']}, WHT {manifest['params']['wht_rate']}, treaty WHT "
        f"{manifest['params']['wht_treaty_rate']} (assumptions, not Lesotho law).",
        "",
        "## Detection by planted issue (test split)",
        "",
        "| Issue | Detected | False positives (taxpayers without that issue) |",
        "|---|---|---|",
    ]
    for issue, v in m["detection_by_issue"].items():
        lines.append(f"| {issue} | {_pct(v['detected'])} | {_pct(v['false_positive'])} |")
    d = m["detection_overall"]
    lines += [
        "",
        f"- Planted issue instances found: {_pct(d['planted_issue_instances'])}",
        f"- Taxpayers with an issue that got at least one correct flag: "
        f"{_pct(d['taxpayers_with_any_issue_flagged'])}",
        f"- Mixed taxpayers with both issues found: {_pct(d['mixed_taxpayers_both_found'])}",
        f"- **Clean taxpayers flagged (false-positive rate): "
        f"{_pct(m['false_positive_overall']['clean_taxpayers_flagged'])}**",
        "",
        "## Refund lanes (test split)",
        "",
        f"- Lane matches the policy-correct lane: {_pct(m['lanes']['accuracy'])}",
        f"- Valid claims proposed for the fast lane: {_pct(m['lanes']['valid_claims_fast'])}",
        f"- Claims whose policy-correct lane is fast that got fast: "
        f"{_pct(m['lanes']['policy_fast_claims_fast'])}",
        f"- Claims containing fake invoices kept out of the fast lane: "
        f"{_pct(m['lanes']['claims_with_fake_invoices_not_fast'])}",
        f"- Claims containing fake invoices sent to investigation: "
        f"{_pct(m['lanes']['claims_with_fake_invoices_investigation'])}",
        "",
        "Confusion (policy-correct -> system): "
        + ", ".join(f"{k}: {v}" for k, v in m["lanes"]["confusion_truth_vs_system"].items()),
        "",
        "## Amounts (test split)",
        "",
        f"- Valid refund value recommended as undisputed (releasable while the rest is "
        f"reviewed): {_m(m['refund_amounts']['valid_refund_in_undisputed_portion'])} of "
        f"{_m(m['refund_amounts']['valid_refund_total'])} "
        f"({m['refund_amounts']['share_of_valid_refund_released_early'] * 100:.1f}%)",
        f"- Fake VAT that leaked into an undisputed portion: "
        f"{_m(m['refund_amounts']['fake_vat_leaked_into_undisputed_portion'])}",
        f"- Fake-invoice VAT planted: {_m(m['amount_at_risk']['fake_invoices_planted_vat'])}; "
        f"in taxpayers the system flagged: "
        f"{_m(m['amount_at_risk']['fake_invoices_planted_vat_in_detected_taxpayers'])}",
        f"- Imported-services gap planted: "
        f"{_m(m['amount_at_risk']['imported_services_planted_gap'])}; computed by the three-way "
        f"match for those taxpayers: "
        f"{_m(m['amount_at_risk']['imported_services_gap_computed_for_planted_taxpayers'])}",
        f"- Valid claims whose undisputed amount is within M1 of the valid refund: "
        f"{_pct(m['refund_amounts']['valid_claims_undisputed_within_m1_of_valid_refund'])}",
        "",
        "## Reconciliation and legal routes",
        "",
        f"- Claims with fakes left out of the books, caught by declared-vs-recomputed: "
        f"{_pct(m['recompute']['claims_with_unbooked_fakes_caught_by_recompute'])}",
        f"- Valid claims with an invoice not found in the taxpayer's books: "
        f"{_pct(m['recompute']['valid_claims_with_a_line_not_in_books'])}",
        f"- Clean taxpayer periods with a recompute finding: "
        f"{_pct(m['recompute']['clean_taxpayer_periods_with_a_recompute_finding'])}",
        f"- Correct findings routed to the expected candidate provision or policy gap: "
        f"{_pct(m['legal_routes']['correct_candidate_route_or_policy_gap'])} (routes are "
        "unverified candidates; this measures routing, not legal correctness)",
        f"- Any finding eligible for assessment: "
        f"{m['legal_routes']['any_finding_eligible_for_assessment']} (by design)",
        "",
        "## Time",
        "",
        f"- System processing: {m['time']['system_seconds_per_taxpayer_mean']} s per taxpayer "
        f"(mean), {m['time']['system_seconds_per_taxpayer_p95']} s (p95), for 24 months of "
        "records. This is machine time to assemble the case, not officer review time.",
        f"- Manual assembly, **assumed**: {m['time']['manual_minutes_per_taxpayer_assumed_mean']}"
        f" minutes per taxpayer from the model {m['time']['manual_model']}. Replace with the "
        "timed manual baseline (human/manual_baseline).",
        "",
        "## Baselines (test split)",
        "",
        "| Approach | Taxpayers with an issue flagged | Clean taxpayers flagged | "
        "Valid claims paid without audit / fast | Claims with fakes held |",
        "|---|---|---|---|---|",
        f"| Vati checks | {_pct(d['taxpayers_with_any_issue_flagged'])} | "
        f"{_pct(m['false_positive_overall']['clean_taxpayers_flagged'])} | "
        f"{_pct(m['lanes']['valid_claims_fast'])} | "
        f"{_pct(m['lanes']['claims_with_fake_invoices_not_fast'])} |",
        f"| Vati checks without RSL score | "
        f"{_pct(ab['detection_overall']['taxpayers_with_any_issue_flagged'])} | "
        f"{_pct(ab['false_positive_overall']['clean_taxpayers_flagged'])} | "
        f"{_pct(ab['lanes']['valid_claims_fast'])} | "
        f"{_pct(ab['lanes']['claims_with_fake_invoices_not_fast'])} |",
    ]
    for b in report["simple_rule_baselines"]:
        lines.append(
            f"| Flag every refund above {_m(b['threshold'])} | "
            f"{_pct(b['taxpayers_with_any_issue_flagged'])} | "
            f"{_pct(b['clean_taxpayers_flagged'])} | "
            f"{_pct(b['valid_claims_paid_without_audit'])} | "
            f"{_pct(b['claims_with_fake_invoices_held'])} |"
        )
    lines += [
        "| Manual (every refund audited) | not measured | not measured | 0% (all audited) | "
        "not measured |",
        "",
        "The simple rule cannot say *which* issue it found or why; refund size says nothing "
        "about imported services, employment or related-party issues.",
        "",
        "## Lekuka coverage (test split, same population)",
        "",
        "| Coverage | Fake invoices detected | Clean flagged | Lane accuracy | Valid claims fast "
        "| Claims with fakes not fast |",
        "|---|---|---|---|---|---|",
    ]
    lines.append(
        f"| {manifest['params']['lekuka_coverage']} (main) | "
        f"{_pct(m['detection_by_issue']['fake_invoices']['detected'])} | "
        f"{_pct(m['false_positive_overall']['clean_taxpayers_flagged'])} | "
        f"{_pct(m['lanes']['accuracy'])} | {_pct(m['lanes']['valid_claims_fast'])} | "
        f"{_pct(m['lanes']['claims_with_fake_invoices_not_fast'])} |"
    )
    for coverage, v in report["lekuka_coverage_degradation"].items():
        lines.append(
            f"| {coverage} | {_pct(v['fake_invoices_detected'])} | "
            f"{_pct(v['clean_taxpayers_flagged'])} | {_pct(v['lane_accuracy'])} | "
            f"{_pct(v['valid_claims_fast'])} | {_pct(v['claims_with_fake_invoices_not_fast'])} |"
        )
    counts = Counter(f"{e['type']}: {e['issue']}" for e in report["errors"])
    lines += [
        "",
        "## Errors (every miss and false positive has a reason in errors.csv)",
        "",
    ]
    for k, v in sorted(counts.items()):
        reasons = Counter(
            e["reason"] for e in report["errors"] if f"{e['type']}: {e['issue']}" == k
        )
        lines.append(
            f"- **{k}** ({v}): " + "; ".join(f"{r} [{n}]" for r, n in reasons.most_common())
        )
    lines += [
        "",
        "## How to read this honestly",
        "",
        "- The generator, the answer key and the rules were written by the same AI coding "
        "agent. The dataset and key hashes were committed before tuning, rules were tuned on "
        "the development split only and frozen before one test run. This limits, but does "
        "not remove, the advantage of knowing how issues were planted.",
        "- The data is synthetic and the noise levels are our assumptions. There are no real "
        "RSL outcomes. Legal routes are unverified candidates. Real Lekuka, bank and RSL "
        "fraud-model access has not been tested; the RSL score is a stand-in whose quality we "
        "set.",
        "- Not tested: Docling extraction accuracy on real documents (copies are supplied as "
        "already-extracted fields), the manual baseline (time model is assumed), officer "
        "usability, and the outside reviewer's scoring of case packs.",
        "- For a blind check, a team member can choose a private seed and run "
        "`python3 -m ai.evaluation.assurance_benchmark rerun --seed <private>`.",
        "",
    ]
    return "\n".join(lines)


def cmd_rerun(seed: int) -> None:
    """Score the frozen rules on a fresh population from a private seed (prints only)."""
    world, key = generate(seed)
    tins = sorted(key["taxpayers"])
    report = score(run_system(world, tins), key)
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "detection_by_issue",
                    "detection_overall",
                    "false_positive_overall",
                    "lanes",
                )
            },
            indent=2,
        )
    )


def cmd_human() -> None:
    manifest, world, key = _load_sealed()
    tins = split(key, "test")
    issue_tins = [t for t in tins if key["taxpayers"][t]["issues"]]
    clean_tins = [t for t in tins if not key["taxpayers"][t]["issues"]]
    chosen = sorted(issue_tins[:10] + clean_tins[:10])
    results = run_system(world, chosen)
    base = EVIDENCE / "human"
    manual_dir = base / "manual_baseline"
    packs_dir = base / "reviewer_packs"
    manual_dir.mkdir(parents=True, exist_ok=True)
    packs_dir.mkdir(parents=True, exist_ok=True)
    timing = io.StringIO()
    tw = csv.writer(timing, lineterminator="\n")
    tw.writerow(
        [
            "case_id",
            "officer",
            "start_time",
            "end_time",
            "minutes",
            "issues_found",
            "lane_recommended_per_claim",
            "amount_questioned",
            "notes",
        ]
    )
    form = io.StringIO()
    fw = csv.writer(form, lineterminator="\n")
    fw.writerow(
        [
            "pack_id",
            "reviewer",
            "clarity_1_to_5",
            "correctness_1_to_5",
            "would_act_on_it_yes_no",
            "missing_or_wrong",
            "comments",
        ]
    )
    for i, tin in enumerate(chosen, 1):
        case = f"CASE-{i:02d}"
        _write_case_files(manual_dir / case, world, tin)
        tw.writerow([case, "", "", "", "", "", "", "", ""])
        pack = f"PACK-{i:02d}"
        (packs_dir / f"{pack}.md").write_text(_pack(pack, results[tin]), encoding="utf-8")
        fw.writerow([pack, "", "", "", "", "", ""])
    (manual_dir / "timing_sheet.csv").write_text(timing.getvalue(), encoding="utf-8")
    (packs_dir / "scoring_form.csv").write_text(form.getvalue(), encoding="utf-8")
    (base / "README.md").write_text(HUMAN_README, encoding="utf-8")
    print(f"wrote {len(chosen)} manual cases and reviewer packs under {base}")


HUMAN_README = """# TAX-118 human checks (synthetic data only)

## Manual baseline (`manual_baseline/`)

Twenty test-split taxpayers (ten with planted issues, ten clean, in mixed order). Each
`CASE-NN` folder holds the raw records an officer would assemble by hand: refund claims and
their invoice schedules, IDMS invoices, ASYCUDA entries, e-Tax returns, withholding lines,
the taxpayer's ledger and the foreign bank file. The answer key is **not** included.

Ask an accountant to work each case in a spreadsheet as RSL would today, and fill in
`timing_sheet.csv`: start and end time, issues found, the lane they would give each claim,
and the amount they would question. Do not show them the system output first.

Each case covers 24 months. If time is short, work the three most recent refund claims
plus the foreign payments and withholding lines, and write in `notes` exactly what was
covered, so the comparison with the system is like for like.

## Outside reviewer (`reviewer_packs/`)

Twenty system case packs for the same taxpayers. Ask an outside accountant or tax
practitioner to score each pack in `scoring_form.csv` on clarity (1-5), correctness (1-5)
and whether they would act on it. They should not see the answer key.

Send both sheets back; TAX-118 results will add them as human-measured numbers.
"""


def _write_case_files(folder: Path, world: Mapping[str, Any], tin: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    recs = world["records"]

    def dump(name: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        flat = [
            {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in r.items()}
            for r in rows
        ]
        fields = sorted({k for r in flat for k in r})
        with (folder / name).open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(flat)

    claims = [c for c in recs["online_refunds.claim"] if c["tin"] == tin]
    dump("refund_claims.csv", [{k: v for k, v in c.items() if k != "lines"} for c in claims])
    dump(
        "claim_invoice_lines.csv",
        [dict(line, claim_id=c["claim_id"]) for c in claims for line in c["lines"]],
    )
    dump("idms_invoices.csv", [r for r in recs["idms.invoice"] if r["buyer_tin"] == tin])
    dump("asycuda_entries.csv", [r for r in recs["asycuda.entry"] if r["importer_tin"] == tin])
    dump("vat_returns.csv", [r for r in recs["etax.vat_return"] if r["tin"] == tin])
    dump(
        "wht_lines.csv",
        [
            dict(line, period=r["period"])
            for r in recs["etax.wht_return"]
            if r["tin"] == tin
            for line in r["lines"]
        ],
    )
    dump("cit_returns.csv", [r for r in recs["etax.cit_return"] if r["tin"] == tin])
    sub = world["submissions"][tin]
    dump("ledger_supplier_invoices.csv", sub["saft"]["supplier_invoices"])
    dump("ledger_sales_summary.csv", sub["saft"]["sales_summary"])
    dump("agreements.csv", sub.get("agreements", []))
    dump("permits.csv", sub.get("permits", []))
    if sub.get("foreign_payments_csv"):
        (folder / "bank_foreign_payments.csv").write_text(
            sub["foreign_payments_csv"], encoding="utf-8"
        )


def _pack(pack: str, r: Mapping[str, Any]) -> str:
    lines = [
        f"# {pack}: system case pack (synthetic)",
        "",
        f"Taxpayer `{r['tin']}`. Proposals only: an officer decides.",
        "",
        "## Findings",
        "",
    ]
    if not r["findings"]:
        lines.append("No findings.")
    for f in r["findings"]:
        route = f["route"]
        target = (
            ", ".join(c["instrument"] for c in route["candidates"])
            or ", ".join(route["policy_gaps"])
            or "none"
        )
        lines.append(
            f"- **{f['code']}**: {f.get('reason', '')} Route: {route['route_status']}"
            f" ({target}); eligible for assessment: no."
        )
    lines += [
        "",
        "## Refund claims",
        "",
        "| Claim | Lane | Claimed | Undisputed | Reasons |",
        "|---|---|---|---|---|",
    ]
    for c in r["claims"]:
        lane = c["lane"]
        lines.append(
            f"| {c['claim_id']} | {lane['lane']} | "
            f"{_m(lane['claimed_amount']['amount'])} | "
            f"{_m(lane['undisputed_amount']['amount'])} | "
            f"{' '.join(lane['reasons'])} |"
        )
    rows = r["three_way"]["rows"]
    if rows:
        lines += [
            "",
            "## Foreign payments (three-way match)",
            "",
            "| Counterparty | Money out | Booked services | WHT declared | WHT expected |",
            "|---|---|---|---|---|",
        ]
        for row in rows:
            lines.append(
                f"| {row['counterparty']} | {_m(row['money_out']['amount'])} | "
                f"{_m(row['booked_services']['amount'])} | "
                f"{_m(row['wht_declared']['amount'])} | "
                f"{_m(row['wht_expected']['amount'])} |"
            )
        lines += ["", f"_{r['three_way']['cma_caveat']}_"]
    lines += ["", "## Alternative explanations tested", ""]
    for e in r["related_party"]["alternative_explanations_tested"]:
        lines.append(f"- {e['code']} ({e.get('years')})")
    return "\n".join(lines) + "\n"


def cmd_check() -> None:
    params = {
        "n_taxpayers": 30,
        "n_dev": 10,
        "issue_counts": {
            "fake_invoices": 2,
            "untaxed_imported_services": 2,
            "disguised_employment": 2,
            "related_party_loss": 2,
            "policy_gap": 2,
            "mixed": 2,
        },
    }
    world, key = generate(11, params)
    again, again_key = generate(11, params)
    assert canonical_sha256(world) == canonical_sha256(again), "generator not reproducible"
    assert canonical_sha256(key) == canonical_sha256(again_key), "key not reproducible"
    visible = json.dumps(world)
    for secret in ("policy_lane", "fake_vat", "fake_strategy", "amount_at_risk", "counterparts"):
        assert secret not in visible, f"answer-key field {secret} leaked into the world"
    results = run_system(world, sorted(key["taxpayers"]))
    report = score(results, key)
    assert report["taxpayers"] == 30
    assert not report["legal_routes"]["any_finding_eligible_for_assessment"]
    print(
        "assurance benchmark check passed: "
        f"lane accuracy {report['lanes']['accuracy']['rate']}, "
        f"clean flagged {report['false_positive_overall']['clean_taxpayers_flagged']['rate']}"
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
    sub.add_parser("human")
    p = sub.add_parser("rerun")
    p.add_argument("--seed", type=int, required=True)
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
    elif args.command == "human":
        cmd_human()
    elif args.command == "rerun":
        cmd_rerun(args.seed)
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
