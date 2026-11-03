"""Initialize/start an isolated Frappe developer site; never touches existing sites."""

import argparse
import json
import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", "docker-compose.frappe.yml"]


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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["init", "start", "status"])
    a = p.parse_args()
    if a.command == "status":
        run(COMPOSE + ["ps"])
        return
    if a.command == "start":
        run(COMPOSE + ["exec", "-d", "frappe", "python", "-c", START])
        print(
            "Developer start requested; check http://127.0.0.1:58000 and container /workspace/bench.log"
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
