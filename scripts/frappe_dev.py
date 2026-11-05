"""Initialize/start/verify an isolated Frappe developer site; never touches existing sites."""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", "docker-compose.frappe.yml"]
DEFAULT_PROJECT = "rsl-taxathon-frappe-dev"
PROJECT_RE = re.compile(r"^rsl-taxathon-frappe-[a-z0-9-]+$")
VERIFY_SITE_RE = re.compile(r"^verify-[0-9]{8}t[0-9]{6}\.localhost$")

# TAX-085 / ADR-035: integration/ is mounted read-only at /workspace/vati_shared/integration.
# These adapter directories go on the bench path (the repository imports them flat, e.g.
# ``import case_lifecycle``); they use only the standard library, so nothing is installed.
ADAPTER_DIRS = ("jube", "docling", "openaleph", "followthemoney", "yente")
SHARED_PATHS = ["/workspace/vati_shared"] + [
    f"/workspace/vati_shared/integration/{name}" for name in ADAPTER_DIRS
]
# From inside the Frappe container the services are not on 127.0.0.1. Docker Desktop names
# the developer's machine host.docker.internal (docker-compose.frappe.yml maps it on Linux).
DEFAULT_SERVICE_HOST = "host.docker.internal"
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# Same values as vati_tax.integrations.config.SITE_FACTORIES (a tooling test checks this).
SITE_FACTORIES = {
    "vati_case_lifecycle": "vati_tax.integrations.factories.case_lifecycle",
    "vati_risk_evaluator": "vati_tax.integrations.factories.risk_evaluator",
}


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE pairs from an ignored local env file; a missing file is empty."""
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def container_url(url: str, host: str) -> str:
    """Rewrite a loopback service URL so the Frappe container reaches the host's port."""
    parts = urllib.parse.urlsplit(url)
    if parts.hostname in LOOPBACK:
        netloc = host + (f":{parts.port}" if parts.port else "")
        parts = parts._replace(netloc=netloc)
    return urllib.parse.urlunsplit(parts).rstrip("/")


def service_config(root: Path = ROOT, host: str = DEFAULT_SERVICE_HOST) -> tuple[dict, list[str]]:
    """``vati_services`` for the site from ignored local config, and what is missing.

    Sources: ``.local/jube.env`` and ``.local/jube-case-workflow.json`` (scripts/jube_dev.py,
    jube_cases.py, jube_rules.py), ``.local/openaleph.env``, ``.local/yente.env`` and the root
    ``.env`` (DOCLING_API_KEY; any *_BASE_URL there overrides the default local port). A
    service without its secret is left out, so the site reports it as not configured.
    """
    if not HOST_RE.match(host):
        raise SystemExit("--service-host must be a plain host name")
    env = read_env_file(root / ".env")
    local = root / ".local"
    services: dict[str, dict] = {}
    missing: list[str] = []

    jube = read_env_file(local / "jube.env")
    workflow_file = local / "jube-case-workflow.json"
    if jube.get("JUBE_CASE_ADAPTER_API_KEY") and workflow_file.is_file():
        workflow = json.loads(workflow_file.read_text(encoding="utf-8"))
        base = env.get("JUBE_BASE_URL") or f"http://127.0.0.1:{jube.get('JUBE_PORT', '55501')}"
        section = {
            "base_url": container_url(base, host),
            "case_adapter_api_key": jube["JUBE_CASE_ADAPTER_API_KEY"],
            "vati_tenant_id": workflow.get("vati_tenant_id"),
            "workflow_guid": workflow.get("workflow_guid"),
            "workflow_version": workflow.get("workflow_version"),
            "status_guids": workflow.get("status_guids"),
        }
        if jube.get("JUBE_RISK_INVOKER_API_KEY") and jube.get("JUBE_RISK_MODEL_GUID"):
            section["risk_invoker_api_key"] = jube["JUBE_RISK_INVOKER_API_KEY"]
            section["risk_model_guid"] = jube["JUBE_RISK_MODEL_GUID"]
        else:
            missing.append("jube_risk")
        services["jube"] = section
    else:
        missing += ["jube", "jube_risk"]

    if env.get("DOCLING_API_KEY"):
        base = env.get("DOCLING_BASE_URL") or "http://127.0.0.1:55001"
        services["docling"] = {
            "base_url": container_url(base, host),
            "api_key": env["DOCLING_API_KEY"],
        }
    else:
        missing.append("docling")

    aleph = read_env_file(local / "openaleph.env")
    aleph_key = env.get("OPENALEPH_API_KEY") or aleph.get("OA_USER_A_API_KEY")
    collection = env.get("OPENALEPH_COLLECTION_ID") or aleph.get("OA_COLLECTION_A_ID")
    if aleph_key and collection:
        port = aleph.get("OPENALEPH_PORT", "55580")
        base = env.get("OPENALEPH_BASE_URL") or f"http://127.0.0.1:{port}"
        services["openaleph"] = {
            "base_url": container_url(base, host),
            "api_key": aleph_key,
            "collection_id": collection,
        }
    else:
        missing.append("openaleph")

    yente = read_env_file(local / "yente.env")
    if yente or env.get("YENTE_BASE_URL"):
        base = env.get("YENTE_BASE_URL") or f"http://127.0.0.1:{yente.get('YENTE_PORT', '55800')}"
        services["yente"] = {
            "base_url": container_url(base, host),
            "dataset": env.get("YENTE_DATASET") or "vati_synthetic",
        }
    else:
        missing.append("yente")
    return services, missing


def ensure_risk_model_guid(root: Path = ROOT) -> None:
    """Record the TAX-035 model GUID once (needs Jube running); failure is reported, not fatal."""
    jube = read_env_file(root / ".local" / "jube.env")
    if not jube.get("JUBE_CASE_ADAPTER_API_KEY") or jube.get("JUBE_RISK_MODEL_GUID"):
        return
    result = subprocess.run(
        [sys.executable, "scripts/jube_rules.py", "record-model"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        print(
            "Jube risk model GUID not recorded (is Jube running?). Start Jube, run "
            "python3 scripts/jube_rules.py record-model, then re-run init."
        )


def configure(project: str, http_port: int, socketio_port: int) -> list[str]:
    """Select an isolated Compose project and loopback ports for this checkout.

    Defaults keep the documented stack; another checkout passes its own project and ports so
    it never recreates containers that a different checkout mounted.
    """
    if not PROJECT_RE.match(project):
        raise SystemExit("Project must look like rsl-taxathon-frappe-<name> (lowercase, digits, -)")
    for port in (http_port, socketio_port):
        if not 1024 < port < 65536:
            raise SystemExit("Ports must be between 1025 and 65535")
    os.environ["FRAPPE_HTTP_PORT"] = str(http_port)
    os.environ["FRAPPE_SOCKETIO_PORT"] = str(socketio_port)
    return ["docker", "compose", "-p", project, "-f", "docker-compose.frappe.yml"]


def verify_site_name(now: float | None = None) -> str:
    """A unique, disposable site name; verify refuses to create or drop anything else."""
    stamp = time.strftime("%Y%m%dt%H%M%S", time.gmtime(now))
    name = f"verify-{stamp}.localhost"
    assert VERIFY_SITE_RE.match(name)
    return name


def run(args, **kwargs):
    result = subprocess.run(args, cwd=ROOT, **kwargs)
    if result.returncode:
        raise SystemExit(
            "Frappe developer command failed; existing data preserved. Inspect the isolated container logs."
        )


# Writes the TAX-085 service adapter keys into one site's site_config.json. Values arrive in
# environment variables (never argv) and are never printed; only service names are.
# Idempotent: init owns vati_services and the factory keys and rewrites them on every run,
# so a service removed from local config is removed from the site too.
APPLY_SERVICES = r"""
import json, os, pathlib
def apply_services(site_dir):
    services = json.loads(os.environ.get('VATI_SERVICES_JSON') or '{}')
    factories = json.loads(os.environ['VATI_SITE_FACTORIES'])
    path = pathlib.Path(site_dir) / 'site_config.json'
    conf = json.loads(path.read_text()) if path.exists() else {}
    for key in ['vati_services', *factories]:
        conf.pop(key, None)
    if services:
        conf['vati_services'] = services
    if 'jube' in services:
        conf['vati_case_lifecycle'] = factories['vati_case_lifecycle']
        if services['jube'].get('risk_model_guid'):
            conf['vati_risk_evaluator'] = factories['vati_risk_evaluator']
    tmp = path.with_name('site_config.json.tmp')
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
        json.dump(conf, handle, indent=1, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return sorted(services)
"""

INIT = (
    APPLY_SERVICES
    + r"""
import json, os, pathlib, subprocess, sys
root=pathlib.Path('/workspace/frappe-bench')
def cmd(args, cwd=root):
    result=subprocess.run(args,cwd=cwd)
    if result.returncode:sys.exit('Developer setup step failed; no existing site was deleted')
if not root.exists():
    cmd(['bench','init','--skip-redis-config-generation','--frappe-branch',os.environ['FRAPPE_TAG'],'frappe-bench'],cwd='/workspace')
actual=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root/'apps/frappe',text=True).strip()
if actual!=os.environ['FRAPPE_COMMIT']:sys.exit('Framework revision differs from lock; review before upgrading')
for key,value in [('db_host','mariadb'),('redis_cache','redis://redis-cache:6379'),('redis_queue','redis://redis-queue:6379'),('redis_socketio','redis://redis-queue:6379')]:
    cmd(['bench','set-config','-g',key,value])
cmd(['bench','set-config','-g','developer_mode','1'])
site=root/'sites/taxathon.localhost'
if not site.exists():
    cmd(['bench','new-site','taxathon.localhost','--db-root-password',os.environ['TAXATHON_DB_ROOT_PASSWORD'],'--admin-password',os.environ['FRAPPE_ADMIN_PASSWORD'],'--mariadb-user-host-login-scope=%'])
app=root/'apps/vati_tax'
if not app.exists():
    app.symlink_to('/workspace/vati_tax', target_is_directory=True)
cmd([str(root/'env/bin/python'),'-m','pip','install','-e',str(app)])
# TAX-044 / ADR-023: make the read-only ai/ mount importable as `ai` without installing
# its dependencies (langgraph is never installed in the Frappe bench).
site_packages=subprocess.check_output([str(root/'env/bin/python'),'-c','import sysconfig;print(sysconfig.get_paths()["purelib"])'],text=True).strip()
# TAX-085 / ADR-035: the same path file also lists the stdlib-only integration/ adapter
# directories (mounted read-only), so the site factories can import them; nothing installed.
pathlib.Path(site_packages,'vati_shared.pth').write_text('\n'.join(json.loads(os.environ['VATI_SHARED_PATHS']))+'\n')
apps_file=root/'sites/apps.txt'
names=apps_file.read_text().splitlines()
if 'vati_tax' not in names: apps_file.write_text('\n'.join(names+['vati_tax'])+'\n')
installed=subprocess.check_output(['bench','--site','taxathon.localhost','list-apps','--format','json'],cwd=root,text=True)
if 'vati_tax' not in installed:
    cmd(['bench','--site','taxathon.localhost','install-app','vati_tax'])
cmd(['bench','--site','taxathon.localhost','migrate'])
cmd(['bench','use','taxathon.localhost'])
configured=apply_services(site)
print('Service adapters configured for taxathon.localhost: '+(', '.join(configured) or 'none'))
print('Pinned Frappe developer site and Vati app initialized')
"""
)

START = r"""
import fcntl, pathlib, subprocess
lock = open('/workspace/bench-start.lock', 'w')
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit('Developer processes already running')
with open('/workspace/bench.log', 'a') as log:
    subprocess.run(['bench', 'start'], cwd='/workspace/frappe-bench', stdout=log, stderr=subprocess.STDOUT)
"""


VERIFY = (
    APPLY_SERVICES
    + r"""
import json, os, re, subprocess, sys, pathlib
root = pathlib.Path('/workspace/frappe-bench')
site = os.environ['VERIFY_SITE']
if not re.match(r'^verify-[0-9]{8}t[0-9]{6}\.localhost$', site):
    sys.exit('Refusing: verify only creates/drops disposable verify-* sites')
pw = os.environ['TAXATHON_DB_ROOT_PASSWORD']
python = str(root / 'env/bin/python')
def cmd(args, capture=True):
    result = subprocess.run(args, cwd=root, text=True, capture_output=capture)
    if result.returncode:
        sys.stderr.write((result.stdout or '')[-2000:] + (result.stderr or '')[-2000:])
        sys.exit('Verification step failed: ' + ' '.join(args[:5]))
    return result.stdout if capture else ''
report = {'site': site}
created = False
try:
    if (root / 'sites' / site).exists():
        sys.exit('Verification site already exists; refusing to reuse it')
    cmd(['bench', 'new-site', site, '--db-root-password', pw, '--admin-password',
         os.environ['VERIFY_ADMIN_PASSWORD'], '--mariadb-user-host-login-scope=%'])
    created = True
    # The app's site tests create far more users than Frappe's default limit of
    # 60 per hour (User.throttle_user_creation). Raise it on this disposable
    # verify-* site only; the developer site and real sites keep the default.
    cmd(['bench', '--site', site, 'set-config', '--parse', 'throttle_user_limit', '10000'])
    cmd(['bench', '--site', site, 'install-app', 'vati_tax'])
    report['installed_apps'] = cmd(['bench', '--site', site, 'list-apps', '--format', 'json']).strip()
    for n in (1, 2):
        cmd(['bench', '--site', site, 'migrate'])
        report[f'migrate_{n}'] = 'ok'
    report['health'] = cmd(['bench', '--site', site, 'execute', 'vati_tax.api.health']).strip()
    report['module_defs'] = cmd(['bench', '--site', site, 'execute', 'frappe.get_all', '--kwargs',
        json.dumps({'doctype': 'Module Def', 'filters': {'app_name': 'vati_tax'}, 'pluck': 'name'})]).strip()
    report['import'] = cmd([python, '-c', 'import vati_tax, vati_tax.api, vati_tax.hooks, '
        'vati_tax.portal.access, vati_tax.www.taxpayer, vati_tax.ids.mapping; print(vati_tax.__version__ if hasattr(vati_tax, "__version__") else "ok")']).strip()
    # TAX-085: every integration/ adapter imports under the bench interpreter from the
    # read-only mount, with no AI or vendor dependency installed.
    report['adapter_imports'] = json.loads(cmd([python, '-c', 'import importlib, importlib.util, json, sys; '
        'mods = ["vati_tax.integrations.factories", "case_lifecycle", "risk_invoke", "tax_risk", '
        '"extraction_adapter", "entity_ingest", "tax_mapping", "scheme_graph", "candidate_review"]; '
        'files = {m: importlib.import_module(m).__file__ for m in mods}; '
        'print(json.dumps({"python": sys.version.split()[0], "modules": {m: f.startswith("/workspace/vati_shared/integration/") or m.startswith("vati_tax") for m, f in files.items()}, '
        '"langgraph_installed": importlib.util.find_spec("langgraph") is not None}))']).strip())
    if not all(report['adapter_imports']['modules'].values()) or report['adapter_imports']['langgraph_installed']:
        sys.exit('Adapter import check failed')
    app = root / 'apps' / 'vati_tax'
    tests = subprocess.run([python, '-m', 'unittest', 'discover', '-s', str(app / 'vati_tax'),
        '-t', str(app), '-p', 'test_*.py'], cwd=root, text=True, capture_output=True)
    report['app_tests_exit'] = tests.returncode
    report['app_tests_summary'] = [l for l in tests.stderr.splitlines() if l.startswith(('Ran ', 'OK', 'FAILED'))]
    # Site-backed tests (DocTypes, unique keys, permissions) run on the disposable site only.
    cmd(['bench', '--site', site, 'set-config', 'allow_tests', 'true'])
    bench_tests = subprocess.run(['bench', '--site', site, 'run-tests', '--app', 'vati_tax'],
        cwd=root, text=True, capture_output=True)
    report['bench_tests_exit'] = bench_tests.returncode
    report['bench_tests_summary'] = [l for l in (bench_tests.stdout + bench_tests.stderr).splitlines()
        if l.startswith(('Ran ', 'OK', 'FAILED'))]
    if bench_tests.returncode:
        sys.stderr.write(bench_tests.stdout[-3000:] + bench_tests.stderr[-3000:])
    # TAX-085 live proof (--live-services): only after the tests above ran without service
    # config, give this disposable site the developer site's vati_services and let an officer
    # decision reach the real local Jube. Output holds IDs and statuses, never config values.
    if os.environ.get('VERIFY_LIVE') == '1':
        report['live_services_configured'] = apply_services(root / 'sites' / site)
        live = subprocess.run(['bench', '--site', site, 'execute', 'vati_tax.integrations.live_check.run'],
            cwd=root, text=True, capture_output=True)
        report['live_check_exit'] = live.returncode
        try:
            report['live_check'] = json.loads(live.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            report['live_check'] = None
            sys.stderr.write(live.stdout[-3000:] + live.stderr[-3000:])
finally:
    if created and os.environ.get('VERIFY_KEEP') != '1':
        cmd(['bench', 'drop-site', site, '--force', '--no-backup', '--db-root-password', pw])
        report['dropped_after_verify'] = True
print(json.dumps(report, indent=2))
if report.get('app_tests_exit') or report.get('bench_tests_exit'):
    sys.exit('vati_tax tests failed')
if os.environ.get('VERIFY_LIVE') == '1' and not (report.get('live_check') or {}).get('ok'):
    sys.exit('Live service check failed')
"""
)


def read_env_values():
    content = (ROOT / ".env").read_text()
    return dict(
        line.split("=", 1)
        for line in content.splitlines()
        if "=" in line and not line.startswith("#")
    )


SERVICE_ENV = ["VATI_SERVICES_JSON", "VATI_SITE_FACTORIES", "VATI_SHARED_PATHS"]


def service_env(host: str) -> dict[str, str]:
    """Environment for APPLY_SERVICES; names only are passed to docker (``-e KEY``)."""
    services, missing = service_config(ROOT, host)
    print("Service adapters from local config: " + (", ".join(sorted(services)) or "none"))
    if missing:
        print("Not configured locally (reported as unavailable): " + ", ".join(missing))
    return {
        "VATI_SERVICES_JSON": json.dumps(services),
        "VATI_SITE_FACTORIES": json.dumps(SITE_FACTORIES),
        "VATI_SHARED_PATHS": json.dumps(SHARED_PATHS),
    }


def verify(keep: bool, live: bool = False, host: str = DEFAULT_SERVICE_HOST) -> None:
    """Clean-site proof: install vati_tax on a new site, migrate twice, import, run app tests.

    ``live`` then gives the disposable site the local service config and proves an officer
    decision reaches the real local Jube (TAX-085); Jube must be running.
    """
    values = read_env_values()
    env = os.environ.copy()
    env.update(
        TAXATHON_DB_ROOT_PASSWORD=values["FRAPPE_DB_PASSWORD"],
        VERIFY_ADMIN_PASSWORD=secrets.token_urlsafe(24),
        VERIFY_SITE=verify_site_name(),
        VERIFY_KEEP="1" if keep else "0",
        VERIFY_LIVE="1" if live else "0",
    )
    if live:
        ensure_risk_model_guid()
        env.update(service_env(host))
    else:
        env.update(VATI_SITE_FACTORIES=json.dumps(SITE_FACTORIES))
    command = COMPOSE + ["exec", "-T"]
    keys = ["TAXATHON_DB_ROOT_PASSWORD", "VERIFY_ADMIN_PASSWORD", "VERIFY_SITE", "VERIFY_KEEP"]
    for key in keys + ["VERIFY_LIVE", *SERVICE_ENV]:
        if key in env:
            command += ["-e", key]
    run(command + ["frappe", "python", "-c", VERIFY], env=env)


def main():
    global COMPOSE
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["init", "start", "status", "verify"])
    p.add_argument("--project", default=DEFAULT_PROJECT, help="Compose project for this checkout")
    p.add_argument("--http-port", type=int, default=58000)
    p.add_argument("--socketio-port", type=int, default=59000)
    p.add_argument("--keep", action="store_true", help="verify: keep the disposable site")
    p.add_argument(
        "--live-services",
        action="store_true",
        help="verify: also prove an officer decision reaches the running local Jube (TAX-085)",
    )
    p.add_argument(
        "--service-host",
        default=os.environ.get("VATI_SERVICE_HOST", DEFAULT_SERVICE_HOST),
        help="host name the Frappe container uses to reach local services (default "
        "host.docker.internal)",
    )
    a = p.parse_args()
    COMPOSE = configure(a.project, a.http_port, a.socketio_port)
    if a.command == "status":
        run(COMPOSE + ["ps"])
        return
    if a.command == "verify":
        verify(a.keep, a.live_services, a.service_host)
        return
    if a.command == "start":
        run(COMPOSE + ["exec", "-d", "frappe", "python", "-c", START])
        print(
            f"Developer start requested; check http://127.0.0.1:{a.http_port} and container /workspace/bench.log"
        )
        return
    subprocess.check_call(["python3", "scripts/dev_env.py"], cwd=ROOT)
    envfile = ROOT / ".env"
    content = envfile.read_text()
    values = dict(
        line.split("=", 1)
        for line in content.splitlines()
        if "=" in line and not line.startswith("#")
    )
    if not values.get("FRAPPE_ADMIN_PASSWORD"):
        values["FRAPPE_ADMIN_PASSWORD"] = secrets.token_urlsafe(24)
        with envfile.open("a") as f:
            f.write("FRAPPE_ADMIN_PASSWORD=" + values["FRAPPE_ADMIN_PASSWORD"] + "\n")
    env = os.environ.copy()
    env.pop("FRAPPE_DB_PASSWORD", None)
    env.update(
        TAXATHON_DB_ROOT_PASSWORD=values["FRAPPE_DB_PASSWORD"],
        FRAPPE_ADMIN_PASSWORD=values["FRAPPE_ADMIN_PASSWORD"],
    )
    lock = json.loads((ROOT / "integration/frappe-framework.lock.json").read_text())
    env.update(FRAPPE_TAG=lock["tag"], FRAPPE_COMMIT=lock["commit"])
    ensure_risk_model_guid()
    env.update(service_env(a.service_host))
    run(COMPOSE + ["up", "-d"])
    run(COMPOSE + ["exec", "-T", "-u", "root", "frappe", "chown", "frappe:frappe", "/workspace"])
    command = COMPOSE + ["exec", "-T"]
    for key in [
        "TAXATHON_DB_ROOT_PASSWORD",
        "FRAPPE_ADMIN_PASSWORD",
        "FRAPPE_TAG",
        "FRAPPE_COMMIT",
        *SERVICE_ENV,
    ]:
        command += ["-e", key]
    run(command + ["frappe", "python", "-c", INIT], env=env)
    print("Local credentials are in ignored .env. Do not commit or share them.")


if __name__ == "__main__":
    main()
