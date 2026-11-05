"""Initialize/start/verify an isolated Frappe developer site; never touches existing sites."""

import argparse
import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", "docker-compose.frappe.yml"]
DEFAULT_PROJECT = "rsl-taxathon-frappe-dev"
PROJECT_RE = re.compile(r"^rsl-taxathon-frappe-[a-z0-9-]+$")
VERIFY_SITE_RE = re.compile(r"^verify-[0-9]{8}t[0-9]{6}\.localhost$")


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


INIT = r"""
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
# TAX-044 / ADR-020: make the read-only ai/ mount importable as `ai` without installing
# its dependencies (langgraph is never installed in the Frappe bench).
site_packages=subprocess.check_output([str(root/'env/bin/python'),'-c','import sysconfig;print(sysconfig.get_paths()["purelib"])'],text=True).strip()
pathlib.Path(site_packages,'vati_shared.pth').write_text('/workspace/vati_shared')
apps_file=root/'sites/apps.txt'
names=apps_file.read_text().splitlines()
if 'vati_tax' not in names: apps_file.write_text('\n'.join(names+['vati_tax'])+'\n')
installed=subprocess.check_output(['bench','--site','taxathon.localhost','list-apps','--format','json'],cwd=root,text=True)
if 'vati_tax' not in installed:
    cmd(['bench','--site','taxathon.localhost','install-app','vati_tax'])
cmd(['bench','--site','taxathon.localhost','migrate'])
cmd(['bench','use','taxathon.localhost'])
print('Pinned Frappe developer site and Vati app initialized')
"""

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


VERIFY = r"""
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
finally:
    if created and os.environ.get('VERIFY_KEEP') != '1':
        cmd(['bench', 'drop-site', site, '--force', '--no-backup', '--db-root-password', pw])
        report['dropped_after_verify'] = True
print(json.dumps(report, indent=2))
if report.get('app_tests_exit') or report.get('bench_tests_exit'):
    sys.exit('vati_tax tests failed')
"""


def read_env_values():
    content = (ROOT / ".env").read_text()
    return dict(
        line.split("=", 1)
        for line in content.splitlines()
        if "=" in line and not line.startswith("#")
    )


def verify(keep: bool) -> None:
    """Clean-site proof: install vati_tax on a new site, migrate twice, import, run app tests."""
    values = read_env_values()
    env = os.environ.copy()
    env.update(
        TAXATHON_DB_ROOT_PASSWORD=values["FRAPPE_DB_PASSWORD"],
        VERIFY_ADMIN_PASSWORD=secrets.token_urlsafe(24),
        VERIFY_SITE=verify_site_name(),
        VERIFY_KEEP="1" if keep else "0",
    )
    command = COMPOSE + ["exec", "-T"]
    for key in ["TAXATHON_DB_ROOT_PASSWORD", "VERIFY_ADMIN_PASSWORD", "VERIFY_SITE", "VERIFY_KEEP"]:
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
    a = p.parse_args()
    COMPOSE = configure(a.project, a.http_port, a.socketio_port)
    if a.command == "status":
        run(COMPOSE + ["ps"])
        return
    if a.command == "verify":
        verify(a.keep)
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
    run(COMPOSE + ["up", "-d"])
    run(COMPOSE + ["exec", "-T", "-u", "root", "frappe", "chown", "frappe:frappe", "/workspace"])
    command = COMPOSE + ["exec", "-T"]
    for key in [
        "TAXATHON_DB_ROOT_PASSWORD",
        "FRAPPE_ADMIN_PASSWORD",
        "FRAPPE_TAG",
        "FRAPPE_COMMIT",
    ]:
        command += ["-e", key]
    run(command + ["frappe", "python", "-c", INIT], env=env)
    print("Local credentials are in ignored .env. Do not commit or share them.")


if __name__ == "__main__":
    main()
