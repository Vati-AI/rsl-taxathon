"""Reduced presentation profile for the demo laptop: start, stop, status, memory, capture (TAX-095).

Live: Frappe (site, portal, Vati app), Jube (cases/risk) and the AI runtime (ADR-031 router:
hosted model, optional local Ollama model, deterministic template). Recorded: Docling,
OpenAleph and yente outputs captured beforehand from real runs of those services, each
with provenance (service, version, source commit, captured_at, scenario) and always
labelled RECORDED. This is the reduced recorded-data demo, not the full pilot profile:
recorded OpenAleph/Jube output never counts as native search/graph UI acceptance.

    python scripts/demo.py up            # start live services only, wait for health, time it
    python scripts/demo.py status        # live / recorded / unavailable per service + model level
    python scripts/demo.py status --json | --html .local/demo/status.html
    python scripts/demo.py memory --samples 12 --interval 5 [--limit-mib N]
    python scripts/demo.py capture docling|openaleph|yente   # needs that service running
    python scripts/demo.py verify-live   # TAX-085 live check (frappe_dev.py verify --live-services)
    python scripts/demo.py down          # stop (not remove) the live containers; data kept

Every port is bound to 127.0.0.1. Nothing is ever reported live without a successful probe,
and a missing or invalid recording is reported unavailable, never synthesised. Secrets are
never printed: only key presence is shown.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RECORDED_DIR = ROOT / "data" / "demo" / "recorded"
STATE_DIR = ROOT / ".local" / "demo"
SERVICES_FILE = ROOT / "integration" / "services.json"
LOCK_FILE = ROOT / "integration" / "upstreams.lock.json"
LIVE_SERVICES = ("frappe", "jube")
RECORDED_SERVICES = ("docling", "openaleph", "yente")
# The service scripts whose own smoke() runs the real service; capture reuses them.
CAPTURE_SCRIPTS = {name: f"{name}_dev.py" for name in RECORDED_SERVICES}
SCENARIO_RE = re.compile(r"^(SYN-\d{3}|service-smoke)$")
SECRET_KEY_RE = re.compile(r"(api[_-]?key|token|password|secret|authorization)", re.I)
RECORD_FIELDS = (
    "recorded",
    "synthetic",
    "service",
    "scenario",
    "service_version",
    "source_commit",
    "captured_at",
    "capture_command",
    "output_sha256",
    "output",
)
NOTICE = (
    "Reduced recorded-data demo profile. Docling, OpenAleph and yente data are RECORDED "
    "from earlier real runs; this is not native-UI acceptance for Jube or OpenAleph."
)


@dataclass(frozen=True)
class LiveService:
    name: str
    project: str
    compose_file: Path
    env_file: Path | None
    health_path: str
    ok_statuses: frozenset[int]

    def url(self, root: Path = ROOT) -> str:
        if self.name == "jube":
            port = read_env_file(root / ".local" / "jube.env").get("JUBE_PORT", "55501")
        else:
            port = os.environ.get("FRAPPE_HTTP_PORT", "58000")
        return f"http://127.0.0.1:{port}"

    def compose(self, *args: str) -> list[str]:
        command = ["docker", "compose", "-p", self.project, "-f", str(self.compose_file)]
        if self.env_file is not None:
            command += ["--env-file", str(self.env_file)]
        return [*command, *args]


LIVE = {
    "frappe": LiveService(
        "frappe",
        "rsl-taxathon-frappe-dev",
        ROOT / "docker-compose.frappe.yml",
        None,  # Compose reads the ignored root .env itself
        "/api/method/ping",
        frozenset({200}),
    ),
    "jube": LiveService(
        "jube",
        "rsl-taxathon-jube",
        ROOT / "integration" / "jube" / "docker-compose.jube.yml",
        ROOT / ".local" / "jube.env",
        "/swagger/v1/swagger.json",
        frozenset({200, 401}),  # same readiness rule as scripts/jube_dev.py
    ),
}


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE pairs from an ignored local file; values are used, never printed."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#"):
            values[key.strip()] = value.strip()
    return values


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --- loopback-only access ------------------------------------------------------------


def published_ports(compose_text: str) -> list[str]:
    """Every published port mapping in a Compose file (inline or block list form)."""
    ports: list[str] = []
    lines = compose_text.splitlines()
    for i, raw in enumerate(lines):
        line = raw.split("#", 1)[0]
        if not re.match(r"^\s*ports\s*:", line):
            continue
        rest = line.split(":", 1)[1].strip()
        if rest.startswith("["):
            ports += [a or b for a, b in re.findall(r'"([^"]*)"|\'([^\']*)\'', rest)]
            continue
        indent = len(raw) - len(raw.lstrip())
        for follow in lines[i + 1 :]:
            text = follow.split("#", 1)[0]
            if not text.strip():
                continue
            if len(text) - len(text.lstrip()) <= indent or not text.strip().startswith("-"):
                break
            ports.append(text.strip()[1:].strip().strip("\"'"))
    return ports


def non_loopback(ports: Iterable[str]) -> list[str]:
    """Mappings that would listen beyond 127.0.0.1 (a bare host port binds every interface)."""
    return [p for p in ports if not p.startswith("127.0.0.1:")]


def runtime_exposed(ports_column: str) -> list[str]:
    """``docker ps`` Ports entries published on anything other than 127.0.0.1."""
    exposed = []
    for entry in (part.strip() for part in ports_column.split(",")):
        if "->" in entry and not entry.startswith("127.0.0.1:"):
            exposed.append(entry)
    return exposed


def check_loopback(services: Iterable[LiveService]) -> None:
    for service in services:
        bad = non_loopback(published_ports(service.compose_file.read_text(encoding="utf-8")))
        if bad:
            raise SystemExit(f"{service.name}: refusing to start, ports not loopback-only: {bad}")


# --- recorded outputs ----------------------------------------------------------------


def canonical_sha256(value: object) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def secret_paths(value: object, prefix: str = "") -> list[str]:
    """Keys that look like credentials and carry a non-empty string value."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if SECRET_KEY_RE.search(str(key)) and isinstance(item, str) and item:
                found.append(path)
            found += secret_paths(item, path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += secret_paths(item, f"{prefix}[{index}]")
    return found


def validate_record(record: object, expected_service: str | None = None) -> list[str]:
    """Problems that stop a recording from being shown; an empty list means usable."""
    if not isinstance(record, dict):
        return ["not_an_object"]
    problems = [f"missing_{name}" for name in RECORD_FIELDS if name not in record]
    if problems:
        return problems
    if record["recorded"] is not True:
        problems.append("recorded_flag_not_true")
    if record["synthetic"] is not True:
        problems.append("synthetic_flag_not_true")
    if record["service"] not in RECORDED_SERVICES:
        problems.append("service_not_in_recorded_profile")
    if expected_service and record["service"] != expected_service:
        problems.append("service_does_not_match_location")
    if not isinstance(record["scenario"], str) or not SCENARIO_RE.match(record["scenario"]):
        problems.append("invalid_scenario")
    for name in ("service_version", "source_commit", "capture_command"):
        if not isinstance(record[name], str) or not record[name].strip():
            problems.append(f"empty_{name}")
    try:
        captured = datetime.fromisoformat(str(record["captured_at"]).replace("Z", "+00:00"))
        if captured.tzinfo is None:
            problems.append("captured_at_without_timezone")
        elif captured > datetime.now(timezone.utc):
            problems.append("captured_at_in_future")
    except ValueError:
        problems.append("invalid_captured_at")
    if canonical_sha256(record["output"]) != record["output_sha256"]:
        problems.append("output_sha256_mismatch")
    if secret_paths(record["output"]):
        problems.append("output_contains_credential_fields")
    return problems


def load_recorded(directory: Path = RECORDED_DIR) -> dict[str, dict[str, Any]]:
    """Per recorded service: valid captures (newest first) and rejected files with reasons."""
    result: dict[str, dict[str, Any]] = {
        name: {"captures": [], "rejected": []} for name in RECORDED_SERVICES
    }
    for service in RECORDED_SERVICES:
        for path in sorted((directory / service).glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                result[service]["rejected"].append({"file": path.name, "problems": ["unreadable"]})
                continue
            problems = validate_record(record, service)
            if problems:
                result[service]["rejected"].append({"file": path.name, "problems": problems})
            else:
                result[service]["captures"].append({"file": path, "record": record})
        result[service]["captures"].sort(key=lambda c: c["record"]["captured_at"], reverse=True)
    return result


def service_metadata(service: str) -> tuple[str, str]:
    """Pinned version and locked source commit for a service, from the repository's records."""
    services = json.loads(SERVICES_FILE.read_text(encoding="utf-8"))
    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    entry = services.get(service, {})
    version = str(entry.get("version") or "")
    commit = str(lock.get(service, {}).get("commit") or entry.get("source_commit") or "")
    return version, commit


def build_record(
    service: str, scenario: str, output: object, command: str, captured_at: str | None = None
) -> dict[str, Any]:
    version, commit = service_metadata(service)
    record = {
        "recorded": True,
        "synthetic": True,
        "label": f"RECORDED {service} output - captured from a real run, not live",
        "service": service,
        "scenario": scenario,
        "service_version": version,
        "source_commit": commit,
        "captured_at": captured_at or now(),
        "capture_command": command,
        "output_sha256": canonical_sha256(output),
        "output": output,
    }
    problems = validate_record(record, service)
    if problems:
        raise SystemExit(f"Refusing to save the {service} capture: {problems}")
    return record


def load_service_script(service: str) -> Any:
    import importlib.util

    path = ROOT / "scripts" / CAPTURE_SCRIPTS[service]
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def capture(
    service: str,
    scenario: str,
    directory: Path = RECORDED_DIR,
    probe: Callable[[], object] | None = None,
) -> Path:
    """Run the real service's own smoke (its script's ``smoke()``) and save it with provenance.

    Only the ``service-smoke`` probe exists today. Scenario captures (SYN-001..005) need a
    scenario-specific probe through the TAX-038/036/037 adapters before they can be recorded.
    """
    if service not in RECORDED_SERVICES:
        raise SystemExit(f"{service} is live in the demo profile; nothing to record")
    if not SCENARIO_RE.match(scenario):
        raise SystemExit("Scenario must be service-smoke or SYN-NNN")
    if probe is None:
        if scenario != "service-smoke":
            raise SystemExit("No scenario probe exists yet; only service-smoke can be captured")
        probe = load_service_script(service).smoke
    output = probe()
    command = f"python scripts/demo.py capture {service} --scenario {scenario}"
    record = build_record(service, scenario, output, command)
    target = directory / service / f"{scenario}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


# --- health --------------------------------------------------------------------------


def http_status(url: str, timeout: float = 5.0) -> int | str:
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        return f"unreachable ({type(reason).__name__})"


Probe = Callable[[str], "int | str"]


def live_status(service: LiveService, probe: Probe = http_status) -> dict[str, Any]:
    url = service.url()
    result = probe(url + service.health_path)
    healthy = isinstance(result, int) and result in service.ok_statuses
    return {
        "service": service.name,
        "state": "live" if healthy else "unavailable",
        "url": url,
        "detail": f"{service.health_path} -> {result}",
    }


def recorded_status(service: str, recorded: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    entry = recorded.get(service, {"captures": [], "rejected": []})
    if not entry["captures"]:
        reasons = [f"{r['file']}: {', '.join(r['problems'])}" for r in entry["rejected"]]
        detail = "; ".join(reasons) or "no capture yet (run demo.py capture before the demo)"
        return {"service": service, "state": "unavailable", "detail": detail}
    latest = entry["captures"][0]["record"]
    return {
        "service": service,
        "state": "recorded",
        "label": latest.get("label") or f"RECORDED {service} output",
        "scenarios": sorted({c["record"]["scenario"] for c in entry["captures"]}),
        "service_version": latest["service_version"],
        "source_commit": latest["source_commit"],
        "captured_at": latest["captured_at"],
        "rejected": len(entry["rejected"]),
        "detail": f"recorded {latest['captured_at']} ({latest['scenario']}, "
        f"{latest['service_version'] or 'version unknown'})",
    }


def ai_status(settings: Any = None, probe: Probe = http_status) -> dict[str, Any]:
    """The configured ADR-031 chain per role, from the router itself; no hosted call is made.

    The level actually used is decided per call and recorded on each output's provenance;
    this shows the highest level available. Hosted levels are configured, not probed: run
    ``python -m ai.models.smoke --live`` to measure them (uses quota).
    """
    from ai.models.config import ROLES, load_settings
    from ai.models.router import get_model

    settings = settings if settings is not None else load_settings()
    public = settings.public_view()
    local_reachable: bool | None = None
    if settings.ollama_base_url and settings.local_model_id:
        result = probe(settings.ollama_base_url.rstrip("/") + "/api/tags")
        local_reachable = isinstance(result, int) and result == 200
    roles: dict[str, Any] = {}
    for role in ROLES:
        chain = get_model(role, settings=settings).chain
        usable = [
            level
            for level in chain
            if level["provider"] is not None
            and not (level["name"] == "local" and local_reachable is False)
        ]
        top = usable[0]
        roles[role] = {
            "level": top["name"],
            "model_id": top["model_id"],
            "chain": [level["name"] for level in chain],
        }
    levels = {r["level"] for r in roles.values()}
    if "mock" in levels:
        state = "mock"
    elif levels == {"hosted"}:
        state = "live"
    else:
        state = "fallback"
    return {
        "service": "ai",
        "state": state,
        "provider_mode": public["provider_mode"],
        "keys_present": public["keys_present"],
        "local_configured": public["local"]["configured"],
        "local_reachable": local_reachable,
        "roles": roles,
        "detail": ", ".join(f"{k}={v['level']}" for k, v in roles.items())
        + ("; hosted configured, not probed" if "hosted" in levels else "")
        + (
            "; MOCK fixtures, not a live model (set VATI_MODEL_PROVIDER=hosted)"
            if state == "mock"
            else ""
        ),
    }


def docker_ports(projects: Iterable[str]) -> dict[str, list[str]] | None:
    """Published ports of the running demo containers; None when Docker is not reachable."""
    exposed: dict[str, list[str]] = {}
    for project in projects:
        label = f"label=com.docker.compose.project={project}"
        try:
            out = subprocess.run(
                ["docker", "ps", "--filter", label, "--format", "{{.Names}}\t{{.Ports}}"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if out.returncode:
            return None
        for line in out.stdout.splitlines():
            name, _, ports = line.partition("\t")
            bad = runtime_exposed(ports)
            if bad:
                exposed[name] = bad
    return exposed


def collect_status(
    probe: Probe = http_status,
    recorded_dir: Path = RECORDED_DIR,
    settings: Any = None,
    ports: Callable[[Iterable[str]], dict[str, list[str]] | None] = docker_ports,
) -> dict[str, Any]:
    recorded = load_recorded(recorded_dir)
    services = [live_status(LIVE[name], probe) for name in LIVE_SERVICES]
    services.append(ai_status(settings, probe))
    services += [recorded_status(name, recorded) for name in RECORDED_SERVICES]
    for item in services:
        item["profile_mode"] = "recorded" if item["service"] in RECORDED_SERVICES else "live"
    exposed = ports([s.project for s in LIVE.values()])
    ready = all(s["state"] in ("live", "recorded", "fallback") for s in services)
    return {
        "checked_at": now(),
        "profile": "demo",
        "notice": NOTICE,
        "ready": ready and not exposed,
        "loopback_only": None if exposed is None else not exposed,
        "exposed_ports": exposed or {},
        "services": services,
    }


def render_text(report: Mapping[str, Any]) -> str:
    lines = [f"DEMO STATUS {report['checked_at']} - {report['notice']}"]
    for item in report["services"]:
        lines.append(f"  {item['service']:<10} {item['state'].upper():<12} {item['detail']}")
    loop = report["loopback_only"]
    lines.append(
        "  loopback   "
        + ("not checked (Docker not reachable)" if loop is None else "ok" if loop else "EXPOSED")
    )
    lines.append("READY" if report["ready"] else "NOT READY - see unavailable services above")
    return "\n".join(lines)


def render_html(report: Mapping[str, Any]) -> str:
    rows = "".join(
        f"<tr class='{html.escape(s['state'])}'><td>{html.escape(s['service'])}</td>"
        f"<td>{html.escape(s['profile_mode'])}</td><td><b>{html.escape(s['state'].upper())}</b>"
        f"</td><td>{html.escape(str(s['detail']))}</td></tr>"
        for s in report["services"]
    )
    return (
        "<!doctype html><html lang='en'><meta charset='utf-8'><title>Demo status</title>"
        "<style>body{font:15px system-ui;margin:24px;background:#fff;color:#111}"
        "td,th{padding:6px 10px;border-bottom:1px solid #ddd;text-align:left}"
        ".live b{color:#11663a}.recorded b{color:#7a4b00}.unavailable b,.mock b{color:#a11}"
        ".fallback b{color:#7a4b00}</style>"
        f"<h1>Demo status</h1><p>{html.escape(report['notice'])}</p>"
        f"<p>Checked {html.escape(report['checked_at'])}. "
        f"{'READY' if report['ready'] else 'NOT READY'}.</p>"
        "<table><tr><th>Service</th><th>Profile</th><th>State</th><th>Detail</th></tr>"
        f"{rows}</table></html>\n"
    )


# --- memory --------------------------------------------------------------------------

UNITS = {"b": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3, "kb": 1000, "mb": 1000**2}
UNITS["gb"] = 1000**3


def parse_size(text: str) -> int | None:
    match = re.match(r"^\s*([\d.]+)\s*([A-Za-z]+)\s*$", text)
    if not match or match.group(2).lower() not in UNITS:
        return None
    return int(float(match.group(1)) * UNITS[match.group(2).lower()])


def parse_stats(lines: Iterable[str]) -> list[dict[str, Any]]:
    """``docker stats`` lines formatted as ``name<TAB>used / limit``."""
    rows = []
    for line in lines:
        name, _, usage = line.partition("\t")
        used, _, limit = usage.partition("/")
        size = parse_size(used)
        if name and size is not None:
            rows.append({"container": name.strip(), "used_bytes": size, "limit": limit.strip()})
    return rows


def memory_snapshot() -> list[dict[str, Any]]:
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if out.returncode:
        raise SystemExit("docker stats failed; is Docker running?")
    # Only this project's containers (Compose names them <project>-<service>-<n>).
    return [r for r in parse_stats(out.stdout.splitlines()) if r["container"].startswith("rsl-")]


def summarise_memory(samples: list[list[dict[str, Any]]]) -> dict[str, Any]:
    totals = [sum(r["used_bytes"] for r in sample) for sample in samples]
    peak: dict[str, int] = {}
    for sample in samples:
        for row in sample:
            peak[row["container"]] = max(peak.get(row["container"], 0), row["used_bytes"])
    mib = 1024**2
    return {
        "samples": len(samples),
        "peak_total_mib": round(max(totals, default=0) / mib, 1),
        "peak_by_container_mib": {k: round(v / mib, 1) for k, v in sorted(peak.items())},
        "note": "docker stats only: add browser, Ollama and Docker VM overhead from the OS",
    }


# --- commands ------------------------------------------------------------------------


def run(args: list[str]) -> None:
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode:
        raise SystemExit(f"Command failed ({result.returncode}): {' '.join(args[:4])}; data kept")


def append_state(name: str, entry: Mapping[str, Any]) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / name
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return path


def up(deadline_s: int) -> int:
    started = time.monotonic()
    started_at = now()
    check_loopback(LIVE.values())
    run([sys.executable, "scripts/jube_dev.py", "up"])
    run(LIVE["frappe"].compose("up", "-d", "--wait"))
    run([sys.executable, "scripts/frappe_dev.py", "start"])
    end = time.monotonic() + deadline_s
    while True:
        report = collect_status()
        if report["ready"] or time.monotonic() > end:
            break
        time.sleep(5)
    elapsed = round(time.monotonic() - started, 1)
    print(render_text(report))
    path = append_state(
        "runs.jsonl",
        {
            "started_at": started_at,
            "finished_at": now(),
            "elapsed_s": elapsed,
            "ready": report["ready"],
            "states": {s["service"]: s["state"] for s in report["services"]},
        },
    )
    print(f"{'Healthy' if report['ready'] else 'NOT healthy'} after {elapsed}s (logged to {path})")
    return 0 if report["ready"] else 1


def memory(samples: int, interval: float, limit_mib: float | None) -> int:
    taken = []
    for index in range(samples):
        taken.append(memory_snapshot())
        if index + 1 < samples:
            time.sleep(interval)
    summary = summarise_memory(taken)
    summary["measured_at"] = now()
    if limit_mib is not None:
        summary["limit_mib"] = limit_mib
        summary["within_limit"] = summary["peak_total_mib"] <= limit_mib
    print(json.dumps(summary, indent=2))
    append_state("memory.jsonl", summary)
    return 0 if summary.get("within_limit", True) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_up = sub.add_parser("up", help="start the live profile and wait for health")
    p_up.add_argument("--deadline", type=int, default=900, help="seconds to wait for health")
    sub.add_parser("down", help="stop the live containers (volumes kept)")
    p_status = sub.add_parser("status", help="live / recorded / unavailable per service")
    p_status.add_argument("--json", action="store_true")
    p_status.add_argument("--html", type=Path, help="also write a static status page here")
    p_mem = sub.add_parser("memory", help="docker stats snapshots and peak")
    p_mem.add_argument("--samples", type=int, default=1)
    p_mem.add_argument("--interval", type=float, default=5.0)
    p_mem.add_argument("--limit-mib", type=float)
    p_cap = sub.add_parser("capture", help="record a real run of a recorded-profile service")
    p_cap.add_argument("service", choices=RECORDED_SERVICES)
    p_cap.add_argument("--scenario", default="service-smoke")
    sub.add_parser("verify-live", help="TAX-085 live check on a disposable Frappe site")
    args = parser.parse_args(argv)

    if args.command == "up":
        return up(args.deadline)
    if args.command == "down":
        run(LIVE["frappe"].compose("stop"))
        run([sys.executable, "scripts/jube_dev.py", "stop"])
        return 0
    if args.command == "status":
        report = collect_status()
        print(json.dumps(report, indent=2) if args.json else render_text(report))
        if args.html:
            args.html.parent.mkdir(parents=True, exist_ok=True)
            args.html.write_text(render_html(report), encoding="utf-8")
        return 0 if report["ready"] else 1
    if args.command == "memory":
        return memory(args.samples, args.interval, args.limit_mib)
    if args.command == "capture":
        path = capture(args.service, args.scenario)
        print(f"Saved RECORDED {args.service} output with provenance: {path.relative_to(ROOT)}")
        return 0
    run([sys.executable, "scripts/frappe_dev.py", "verify", "--live-services"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
