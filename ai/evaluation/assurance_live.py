"""Live benchmark mode: the TAX-118 measurement taken through the platform (TAX-121).

TAX-118 called ``assess_taxpayer`` directly. Here the Frappe platform is responsible for
the result: the sealed test taxpayers are seeded into a disposable Frappe site, the outbox
runs the ``assurance.requested`` step for every sent refund claim (ADR-042), and findings
and lane proposals are read back through ``vati_tax.assurance.store.case_assurance`` as an
auditor. They are converted to the scorer's per-taxpayer shape and scored with the frozen
TAX-118 scorer (``assurance_benchmark.score``), next to a standalone run of the same
taxpayers, so every difference can be explained.

Commands (run on the host that drives the bench; ``.benchmark/`` is git-ignored)::

    VATI_EXTRA_CA_BUNDLE=... python3 -m ai.evaluation.assurance_live run [--limit N]
    python3 -m ai.evaluation.assurance_live report   # re-score the last platform export

``run`` creates a disposable ``tax121-live-<UTC stamp>.localhost`` site in the developer
bench (``scripts/frappe_dev.py`` project), installs ``vati_tax``, records the RSL source
fixture for it (site key ``vati_rsl_sources``), seeds and drains, exports, and drops the
site unless ``--keep-site``. It never touches another site. The seed holds the taxpayers'
own records and claims only; the answer key never leaves the host.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from ai.evaluation import assurance_benchmark as bench

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / ".benchmark" / "tax121"
EVIDENCE = ROOT / "docs" / "evidence" / "TAX-121"
CONTAINER_DIR = "/workspace/tax121"
SOURCES_FORMAT = "vati.rsl_sources_fixture.v1"
CATEGORY_ISSUE = {
    "refund_reconciliation": "fake_invoices",
    "imported_services": "untaxed_imported_services",
    "worker_classification": "disguised_employment",
    "related_party": "related_party_loss",
    "policy_gap": "policy_gap",
}


# --------------------------------------------------------------------------- inputs


def sources_fixture(world: Mapping[str, Any]) -> dict[str, Any]:
    """The RSL source records only (no taxpayer submissions), as the site's fixture file."""
    return {
        "format": SOURCES_FORMAT,
        "fixture": True,
        "synthetic": True,
        "generator_version": world["generator_version"],
        "tenant_id": world["tenant_id"],
        "retrieved_at": world["retrieved_at"],
        "rates_assumed": world["rates_assumed"],
        "materiality": world["materiality"],
        "records": world["records"],
    }


def seed(world: Mapping[str, Any], tins: list[str]) -> dict[str, Any]:
    """Per taxpayer: name, the taxpayer's own records and the claims they filed."""
    claims: dict[str, list[dict[str, str]]] = {}
    for c in world["records"]["online_refunds.claim"]:
        claims.setdefault(str(c["tin"]), []).append(
            {
                "claim_id": c["claim_id"],
                "period": c["period"],
                "claimed_amount": c["claimed_amount"],
            }
        )
    out = []
    for tin in tins:
        records = world["submissions"][tin]
        out.append(
            {
                "tin": tin,
                "name": f"Synthetic {records['saft']['header'].get('company_name', tin)}",
                "records": records,
                "claims": sorted(claims.get(tin, []), key=lambda c: c["period"]),
            }
        )
    body = {"synthetic": True, "task": "TAX-121", "taxpayers": out}
    body["seed_sha256"] = bench.canonical_sha256(out)
    return body


# --------------------------------------------------------------------------- conversion


def _amount(value: Any) -> Decimal:
    return Decimal(str(value["amount"])) if value else Decimal("0.00")


def _current_run(case: Mapping[str, Any]) -> Mapping[str, Any] | None:
    runs = case.get("assurance", {}).get("runs", [])
    return runs[-1] if runs else None


def platform_results(export: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict]:
    """Platform readback -> the scorer's per-taxpayer result shape, plus what was missing.

    - flags: issues of the ``vati_check`` findings recorded on the taxpayer's cases;
    - findings: one per distinct finding subject (each case of a taxpayer records the same
      taxpayer-level findings), with the stored candidate route;
    - claims: the current lane proposal of each case (lane, amounts, inputs, line matches);
    - recompute / three_way / work_arrangement: the counts the checks reported in the
      ``Vati Assurance Run`` summary (the scorer only counts these lists);
    - flagged_amounts: disputed VAT of lane proposals with an authenticity indicator, and
      the three-way gap total from the run summary;
    - seconds: the platform step time summed over the taxpayer's cases.
    """
    results: dict[str, dict[str, Any]] = {}
    gaps: dict[str, Any] = {"cases_without_completed_run": [], "summary_mismatch": []}
    for item in export["results"]:
        tin = str(item["tin"])
        flags: set[str] = set()
        findings: dict[str, dict[str, Any]] = {}
        claims = []
        summaries = []
        seconds = 0.0
        disputed_auth = Decimal("0.00")
        for case in item["cases"]:
            run = _current_run(case)
            if run is None or run["status"] not in ("completed", "degraded"):
                gaps["cases_without_completed_run"].append(
                    {"tin": tin, "claim_ref": case["claim_ref"],
                     "status": run["status"] if run else None,
                     "reason": run["reason"] if run else None}
                )  # fmt: skip
                continue
            seconds += float(run["seconds"])
            summaries.append(run["summary"])
            for f in case["assurance"]["findings"]:
                issue = CATEGORY_ISSUE[f["category"]]
                flags.add(issue)
                route = f["candidate_route"]
                findings.setdefault(
                    str(f.get("subject")),
                    {
                        "code": f["code"],
                        "issue": issue,
                        "route": {
                            "finding_code": f["code"],
                            "route_status": route["route_status"],
                            "candidates": route["candidates"],
                            "policy_gaps": route["policy_gaps"],
                            "eligible_for_assessment": route["eligible_for_assessment"],
                        },
                    },
                )
            lanes = [lane for lane in case["assurance"]["lane_proposals"] if lane.get("current")]
            for lane in lanes:
                if lane["authenticity_indicator"]:
                    disputed_auth += _amount(lane["disputed_amount"])
                claims.append(
                    {
                        "claim_id": lane["claim_ref"],
                        "period": lane["period"],
                        "lane": {
                            "lane": lane["lane"],
                            "claimed_amount": lane["claimed_amount"],
                            "undisputed_amount": lane["undisputed_amount"],
                            "disputed_amount": lane["disputed_amount"],
                            "inputs": lane["inputs"],
                            "service_target": lane["service_target"],
                        },
                        "match": {"lines": lane["line_match"]},
                        "authenticity_indicator": lane["authenticity_indicator"],
                    }
                )
        if not summaries:
            continue
        first = summaries[0]
        keys = ("recompute_comparison_rows", "recompute_findings", "three_way_rows",
                "three_way_total_gap", "work_referrals", "issues")  # fmt: skip
        if any({k: s.get(k) for k in keys} != {k: first.get(k) for k in keys} for s in summaries):
            gaps["summary_mismatch"].append(tin)
        gap = first.get("three_way_total_gap")
        results[tin] = {
            "tin": tin,
            "flags": sorted(flags),
            "findings": list(findings.values()),
            "claims": sorted(claims, key=lambda c: c["claim_id"]),
            "recompute": {
                "comparison": [None] * int(first["recompute_comparison_rows"]),
                "findings": [None] * int(first["recompute_findings"]),
            },
            "three_way": {"rows": [None] * int(first["three_way_rows"])},
            "work_arrangement": {"referrals": [None] * int(first["work_referrals"])},
            "flagged_amounts": {
                "fake_invoices": str(disputed_auth),
                "untaxed_imported_services": str(_amount(gap) if gap else Decimal("0.00")),
            },
            "seconds": round(seconds, 4),
        }
    return results, gaps


# --------------------------------------------------------------------------- comparison


def _claim_view(c: Mapping[str, Any]) -> dict[str, Any]:
    lane = c["lane"]
    return {
        "lane": lane["lane"],
        "claimed": lane["claimed_amount"]["amount"],
        "undisputed": lane["undisputed_amount"]["amount"],
        "disputed": lane["disputed_amount"]["amount"],
        "lines_not_in_books": list(
            lane["inputs"].get("recompute", {}).get("lines_not_in_books", [])
        ),
        "lines": len(c["match"]["lines"]),
        "authenticity_indicator": bool(c.get("authenticity_indicator")),
    }


def differences(
    live: Mapping[str, Mapping[str, Any]], standalone: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Every per-taxpayer and per-claim difference the scorer could see (time excluded)."""
    out: list[dict[str, Any]] = []
    for tin in sorted(standalone):
        s = standalone[tin]
        r = live.get(tin)
        if r is None:
            out.append({"tin": tin, "field": "taxpayer", "standalone": "assessed", "live": None})
            continue
        if sorted(s["flags"]) != r["flags"]:
            out.append({"tin": tin, "field": "flags", "standalone": sorted(s["flags"]),
                        "live": r["flags"]})  # fmt: skip
        s_routes = sorted((f["issue"], f["code"], json.dumps(f["route"], sort_keys=True))
                          for f in s["findings"])  # fmt: skip
        l_routes = sorted((f["issue"], f["code"], json.dumps(f["route"], sort_keys=True))
                          for f in r["findings"])  # fmt: skip
        if s_routes != l_routes:
            out.append({"tin": tin, "field": "findings", "standalone": s_routes, "live": l_routes})
        for field, a, b in (
            ("recompute.comparison", len(s["recompute"]["comparison"]),
             len(r["recompute"]["comparison"])),
            ("recompute.findings", len(s["recompute"]["findings"]),
             len(r["recompute"]["findings"])),
            ("three_way.rows", len(s["three_way"]["rows"]), len(r["three_way"]["rows"])),
            ("work_arrangement.referrals", len(s["work_arrangement"].get("referrals", [])),
             len(r["work_arrangement"]["referrals"])),
        ):  # fmt: skip
            if a != b:
                out.append({"tin": tin, "field": field, "standalone": a, "live": b})
        for key in ("fake_invoices", "untaxed_imported_services"):
            x, y = Decimal(s["flagged_amounts"][key]), Decimal(r["flagged_amounts"][key])
            if x != y:
                out.append({"tin": tin, "field": f"flagged_amounts.{key}",
                            "standalone": str(x), "live": str(y)})  # fmt: skip
        live_claims = {c["claim_id"]: c for c in r["claims"]}
        for c in s["claims"]:
            other = live_claims.get(c["claim_id"])
            if other is None:
                out.append({"tin": tin, "claim": c["claim_id"], "field": "claim",
                            "standalone": "proposed", "live": None})  # fmt: skip
                continue
            mine, theirs = _claim_view(c), _claim_view(other)
            for name, value in mine.items():
                if value != theirs[name]:
                    out.append({"tin": tin, "claim": c["claim_id"], "field": f"claim.{name}",
                                "standalone": value, "live": theirs[name]})  # fmt: skip
    return out


def services_report(export: Mapping[str, Any]) -> dict[str, Any]:
    """Which services each case used: live, recorded, unavailable or not used."""
    modes: Counter[str] = Counter()
    systems: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    jube: Counter[str] = Counter()
    not_used: Counter[str] = Counter()
    for item in export["results"]:
        for case in item["cases"]:
            run = _current_run(case)
            if run is None:
                statuses["no_run"] += 1
                continue
            statuses[run["status"]] += 1
            if run["reason"]:
                reasons[run["reason"]] += 1
            services = run["services"]
            for name in ("frappe", "taxpayer_records", "rsl_sources", "checks"):
                value = services.get(name)
                mode = value if isinstance(value, str) else (value or {}).get("mode")
                modes[f"{name}:{mode}"] += 1
            for system, state in (services.get("rsl_sources", {}).get("systems") or {}).items():
                systems[f"{system}:{state}"] += 1
            for name, state in (services.get("not_used_by_this_step") or {}).items():
                not_used[f"{name}:{state}"] += 1
            for event in case.get("events", []):
                if event["event_type"] == "case.registered":
                    jube[f"{event['status']}:{event['last_error_code'] or '-'}"] += 1
    return {
        "run_status": dict(sorted(statuses.items())),
        "run_reasons": dict(sorted(reasons.items())),
        "modes": dict(sorted(modes.items())),
        "rsl_systems": dict(sorted(systems.items())),
        "not_used_by_assurance_step": dict(sorted(not_used.items())),
        "jube_case_registered_step": dict(sorted(jube.items())),
    }


# --------------------------------------------------------------------------- report


def build_report(
    export: Mapping[str, Any], world: Mapping[str, Any], key: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:  # fmt: skip
    live, gaps = platform_results(export)
    tins = [str(i["tin"]) for i in export["results"]]
    started = time.perf_counter()
    standalone = bench.run_system(world, tins)
    standalone_seconds = round(time.perf_counter() - started, 2)
    committed = json.loads((bench.EVIDENCE / "results.json").read_text())["main"]
    fingerprint = bench.rules_fingerprint()["fingerprint"]
    diffs = differences(live, standalone)
    return {
        "synthetic": True,
        "task": "TAX-121",
        "scored_at": bench._now(),
        "seed": manifest["seed"],
        "dataset_sha256": manifest["dataset_sha256"],
        "answer_key_sha256": manifest["answer_key_sha256"],
        "rules_fingerprint": fingerprint,
        "rules_match_frozen_tax118": fingerprint == manifest["frozen"]["fingerprint"],
        "split": "test",
        "taxpayers_seeded": len(tins),
        "taxpayers_in_test_split": len(bench.split(key, "test")),
        "platform": {k: v for k, v in export.items() if k != "results"},
        "services": services_report(export),
        "readback_gaps": gaps,
        "live": bench.score(live, key) if live else None,
        "standalone_same_taxpayers": bench.score(standalone, key),
        "standalone_seconds_total": standalone_seconds,
        "tax118_committed_main": committed,
        "differences": diffs,
        "difference_counts": dict(Counter(d["field"] for d in diffs)),
        "errors_live": bench.error_analysis(live, key) if live else [],
    }


METRICS = (
    ("Taxpayers with an issue flagged", ("detection_overall", "taxpayers_with_any_issue_flagged")),
    ("Planted issue instances found", ("detection_overall", "planted_issue_instances")),
    ("Clean taxpayers flagged", ("false_positive_overall", "clean_taxpayers_flagged")),
    ("Lane matches policy-correct lane", ("lanes", "accuracy")),
    ("Valid claims fast", ("lanes", "valid_claims_fast")),
    ("Claims with fake invoices not fast", ("lanes", "claims_with_fake_invoices_not_fast")),
    ("Claims with fake invoices to investigation",
     ("lanes", "claims_with_fake_invoices_investigation")),
    ("Unbooked fakes caught by recompute",
     ("recompute", "claims_with_unbooked_fakes_caught_by_recompute")),
    ("Clean periods with a recompute finding",
     ("recompute", "clean_taxpayer_periods_with_a_recompute_finding")),
    ("Correct findings routed to expected candidate or gap",
     ("legal_routes", "correct_candidate_route_or_policy_gap")),
)  # fmt: skip
AMOUNTS = (
    ("Valid refund in the undisputed portion", "refund_amounts", "valid_refund_in_undisputed_portion"),
    ("Fake VAT leaked into an undisputed portion", "refund_amounts",
     "fake_vat_leaked_into_undisputed_portion"),
    ("Fake VAT disputed for planted taxpayers", "amount_at_risk",
     "fake_invoices_vat_disputed_by_system_for_planted_taxpayers"),
    ("Imported-services gap computed for planted taxpayers", "amount_at_risk",
     "imported_services_gap_computed_for_planted_taxpayers"),
)  # fmt: skip


def _get(report: Mapping[str, Any] | None, path: tuple[str, ...]) -> Any:
    value: Any = report
    for part in path:
        value = value.get(part) if isinstance(value, Mapping) else None
    return value


def render(report: Mapping[str, Any]) -> str:
    live, alone, tax118 = report["live"], report["standalone_same_taxpayers"], report[
        "tax118_committed_main"]  # fmt: skip
    p = report["platform"]
    s = report["services"]
    lines = [
        "# TAX-121 results: the TAX-118 benchmark measured through the platform",
        "",
        "**System output on synthetic data**, scored automatically against the sealed TAX-118 "
        "answer key. Nothing here is real RSL performance; legal routes are unverified "
        "candidates; every lane and amount is a proposal for an officer.",
        "",
        "## What ran",
        "",
        f"- Site: disposable `{p['site']}` in the developer bench (dropped after the run unless "
        "kept); tenant `" + str(p["tenant_id"]) + "`.",
        f"- Seeded: {report['taxpayers_seeded']} of the {report['taxpayers_in_test_split']} "
        f"test-split taxpayers, {p['cases']} refund claims (one sent `Vati Submission`, "
        "`Vati Refund Claim` and case each), plus one taxpayer-records evidence document per "
        "taxpayer.",
        f"- Seed {report['seed']}, dataset `{report['dataset_sha256'][:16]}...`, answer key "
        f"`{report['answer_key_sha256'][:16]}...` (regenerated by `_load_sealed()` and "
        "hash-checked). Check files unchanged since the TAX-118 freeze: "
        f"{report['rules_match_frozen_tax118']}.",
        f"- Timings: seeding {p['timings'].get('seed_seconds')} s, outbox drain "
        f"{p['timings'].get('drain_seconds')} s ({p['drain']['dispatch_rounds']} dispatch "
        f"rounds, outcomes {p['drain']['outcomes']}), read-back "
        f"{p['timings'].get('readback_seconds')} s. Assurance steps left undelivered: "
        f"{p['drain']['assurance_pending']}.",
        f"- {p['read_by']}.",
        "",
        "## Services",
        "",
        f"- Assurance run status per case: {s['run_status']}; reasons {s['run_reasons'] or 'none'}.",
        f"- Modes per case: {s['modes']}.",
        f"- RSL systems per case (all through the TAX-111 fixture adapter): {s['rsl_systems']}.",
        f"- Not used by the assurance step, site configuration: {s['not_used_by_assurance_step']}.",
        f"- The TAX-039 Jube step (`case.registered`) of each case: "
        f"{s['jube_case_registered_step']}.",
        "",
        "Frappe (records, outbox, findings, lane proposals, runs) was **live**. The RSL source "
        "systems were **recorded**: the sealed synthetic fixture file served through the TAX-111 "
        "fixture adapter, never a live RSL system. Jube, OpenAleph and yente were **unavailable** "
        "(not running, not configured on this site); the Jube step of every case stays pending "
        "or retrying and is reported, never treated as clean. Docling was not used: the taxpayer "
        "records are supplied as structured data, as in TAX-118.",
        "",
        "## Live vs standalone",
        "",
        "| Metric | Live (platform) | Standalone, same taxpayers | TAX-118 committed (200) |",
        "|---|---|---|---|",
    ]
    for label, path in METRICS:
        lines.append(
            f"| {label} | {_fmt(_get(live, path))} | {_fmt(_get(alone, path))} | "
            f"{_fmt(_get(tax118, path))} |"
        )
    for issue in bench.ISSUES:
        for kind in ("detected", "false_positive"):
            where = ("detection_by_issue", issue, kind)
            lines.append(
                f"| {issue} {kind.replace('_', ' ')} | {_fmt(_get(live, where))} | "
                f"{_fmt(_get(alone, where))} | {_fmt(_get(tax118, where))} |"
            )
    for label, section, field in AMOUNTS:
        lines.append(
            f"| {label} | {_m(_get(live, (section, field)))} | {_m(_get(alone, (section, field)))} | "
            f"{_m(_get(tax118, (section, field)))} |"
        )
    lines.append(
        f"| Any finding eligible for assessment | "
        f"{_get(live, ('legal_routes', 'any_finding_eligible_for_assessment'))} | "
        f"{_get(alone, ('legal_routes', 'any_finding_eligible_for_assessment'))} | "
        f"{_get(tax118, ('legal_routes', 'any_finding_eligible_for_assessment'))} |"
    )
    lines.append(
        f"| Seconds per taxpayer (mean) | {_get(live, ('time', 'system_seconds_per_taxpayer_mean'))} "
        f"| {_get(alone, ('time', 'system_seconds_per_taxpayer_mean'))} | "
        f"{_get(tax118, ('time', 'system_seconds_per_taxpayer_mean'))} |"
    )
    confusion_live = _get(live, ("lanes", "confusion_truth_vs_system"))
    confusion_alone = _get(alone, ("lanes", "confusion_truth_vs_system"))
    lines += [
        "",
        f"Lane confusion, live: {confusion_live}",
        "",
        f"Lane confusion, standalone: {confusion_alone}",
        "",
        "## Differences",
        "",
        f"Per-taxpayer and per-claim differences between the platform read-back and the "
        f"standalone run (time excluded): {len(report['differences'])} "
        f"({report['difference_counts'] or 'none'}). Read-back gaps: "
        f"{len(report['readback_gaps']['cases_without_completed_run'])} cases without a "
        f"completed run, {len(report['readback_gaps']['summary_mismatch'])} taxpayers whose "
        "cases disagree on a check summary.",
        "",
    ]
    lines += report.get("explanations", [])
    return "\n".join(lines) + "\n"


def _fmt(r: Any) -> str:
    if not isinstance(r, Mapping) or "rate" not in r:
        return "n/a"
    return bench._pct(r)


def _m(value: Any) -> str:
    return "n/a" if value is None else bench._m(str(value))


# --------------------------------------------------------------------------- bench driving


def compose(project: str) -> list[str]:
    command = ["docker", "compose", "-p", project, "-f", "docker-compose.frappe.yml"]
    override = ROOT / ".local" / "frappe-extra-ca.override.yml"
    if not os.environ.get("VATI_EXTRA_CA_BUNDLE") or not override.is_file():
        raise SystemExit(
            "Set VATI_EXTRA_CA_BUNDLE (as for scripts/frappe_dev.py) so the bench container is "
            "used as configured; this command only runs `exec` and `cp`."
        )
    return command + ["-f", str(override)]


def _db_password() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("FRAPPE_DB_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("FRAPPE_DB_PASSWORD missing from .env; run scripts/frappe_dev.py init")


def _bench(base: list[str], args: list[str], env: dict[str, str] | None = None,
           names: tuple[str, ...] = ()) -> str:  # fmt: skip
    command = base + ["exec", "-T"]
    for name in names:
        command += ["-e", name]
    command += ["frappe", "bash", "-lc", "cd /workspace/frappe-bench && " + " ".join(args)]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True,
                            env={**os.environ, **(env or {})})  # fmt: skip
    if result.returncode:
        sys.stderr.write(result.stdout[-3000:] + result.stderr[-3000:])
        raise SystemExit("bench step failed: " + " ".join(args[:4]))
    return result.stdout


def cmd_run(limit: int | None, project: str, keep: bool) -> None:
    manifest, world, key = bench._load_sealed()
    tins = bench.split(key, "test")
    if limit:
        tins = tins[:limit]
    WORK.mkdir(parents=True, exist_ok=True)
    bench._write_json(WORK / "rsl_sources.json", sources_fixture(world))
    bench._write_json(WORK / "seed.json", seed(world, tins))
    base = compose(project)
    site = "tax121-live-" + time.strftime("%Y%m%dt%H%M%S", time.gmtime()) + ".localhost"
    tenant = str(uuid.uuid4())
    pw = {"TAX121_DB_ROOT_PASSWORD": _db_password(), "TAX121_ADMIN_PASSWORD": uuid.uuid4().hex}
    secret = ("TAX121_DB_ROOT_PASSWORD", "TAX121_ADMIN_PASSWORD")
    created = False
    try:
        _bench(base, ["bench", "new-site", site, "--db-root-password",
                      '"$TAX121_DB_ROOT_PASSWORD"', "--admin-password", '"$TAX121_ADMIN_PASSWORD"',
                      "--mariadb-user-host-login-scope=%"], pw, secret)  # fmt: skip
        created = True
        _bench(base, ["bench", "--site", site, "set-config", "--parse", "throttle_user_limit",
                      "10000"])  # fmt: skip
        _bench(base, ["bench", "--site", site, "install-app", "vati_tax"])
        _bench(base, ["mkdir", "-p", CONTAINER_DIR])
        for name in ("rsl_sources.json", "seed.json"):
            subprocess.run(base + ["cp", str(WORK / name), f"frappe:{CONTAINER_DIR}/{name}"],
                           cwd=ROOT, check=True)  # fmt: skip
        conf = {"fixture_path": f"{CONTAINER_DIR}/rsl_sources.json", "vati_tenant_id": tenant}
        _bench(base, ["bench", "--site", site, "set-config", "--parse", "vati_rsl_sources",
                      "'" + json.dumps(conf) + "'"])  # fmt: skip
        kwargs = {"seed_path": f"{CONTAINER_DIR}/seed.json",
                  "out_path": f"{CONTAINER_DIR}/platform.json", "tenant_id": tenant}  # fmt: skip
        out = _bench(base, ["bench", "--site", site, "execute",
                            "vati_tax.assurance.live_bench.run", "--kwargs",
                            "'" + json.dumps(kwargs) + "'"])  # fmt: skip
        print(out.strip().splitlines()[-1] if out.strip() else "")
        subprocess.run(base + ["cp", f"frappe:{CONTAINER_DIR}/platform.json",
                               str(WORK / "platform.json")], cwd=ROOT, check=True)  # fmt: skip
    finally:
        if created and not keep:
            _bench(base, ["bench", "drop-site", site, "--force", "--no-backup",
                          "--db-root-password", '"$TAX121_DB_ROOT_PASSWORD"'], pw, secret)  # fmt: skip
            print(f"dropped {site}")
    cmd_report()


def cmd_report() -> None:
    manifest, world, key = bench._load_sealed()
    export = json.loads((WORK / "platform.json").read_text())
    report = build_report(export, world, key, manifest)
    explanations = EVIDENCE / "explanations.md"
    report["explanations"] = (
        explanations.read_text(encoding="utf-8").splitlines() if explanations.exists() else []
    )
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    stored = {k: v for k, v in report.items() if k != "explanations"}
    bench._write_json(EVIDENCE / "results.json", stored)
    (EVIDENCE / "RESULTS.md").write_text(render(report), encoding="utf-8")
    print(json.dumps({"differences": report["difference_counts"],
                      "readback_gaps": {k: len(v) for k, v in report["readback_gaps"].items()},
                      "services": report["services"]["run_status"]}, indent=2))  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--limit", type=int, default=None, help="seed only the first N test taxpayers")
    run.add_argument("--project", default="rsl-taxathon-frappe-dev")
    run.add_argument("--keep-site", action="store_true")
    sub.add_parser("report")
    args = parser.parse_args(argv)
    if args.command == "run":
        cmd_run(args.limit, args.project, args.keep_site)
    else:
        cmd_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
