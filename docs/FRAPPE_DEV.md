# Frappe developer setup

From the repository root with Docker Compose running:

```sh
python3 scripts/frappe_dev.py init
python3 scripts/frappe_dev.py start
python3 scripts/frappe_dev.py status
python3 scripts/frappe_dev.py verify   # clean-site proof for vati_tax (TAX-019)
```

**Second checkout on the same machine.** The default project `rsl-taxathon-frappe-dev` and ports 58000/59000 belong to whichever checkout ran `init` first, and that stack mounts *that* checkout's `backend/vati_tax`. Running `init` from a different checkout with the defaults would recreate those containers with the other code. Give each extra checkout its own project and ports, and pass the same options to every command:

```sh
python3 scripts/frappe_dev.py init   --project rsl-taxathon-frappe-<name> --http-port 58010 --socketio-port 59010
python3 scripts/frappe_dev.py verify --project rsl-taxathon-frappe-<name> --http-port 58010 --socketio-port 59010
```

**Clean-site verification (`verify`).** Inside the selected stack's bench, `verify` creates a new disposable site named `verify-<UTC timestamp>.localhost`. It installs `vati_tax`, runs `bench migrate` twice, executes `vati_tax.api.health`, lists the app's Module Defs, imports the app modules, runs the app's Frappe-free unit tests with the bench interpreter and runs the site-backed tests with `bench run-tests --app vati_tax` (enabling `allow_tests` and raising Frappe's `throttle_user_limit` from 60 new users per hour on that disposable site only, because the site tests create more test users than that). It then drops that disposable site and prints a JSON report. It never creates, reuses or drops any other site (the name is checked both on the host and inside the container) and makes no manual database edits. Pass `--keep` to keep the site for inspection. If the portal pre-submission tests fail with `No module named 'ai'`, your stack predates ADR-023: re-run `frappe_dev.py init` with the same `--project` and ports to add the read-only `vati_shared` path (it is idempotent and keeps existing sites).

**Service adapters inside the bench (TAX-085, [ADR-035](decisions/ADR-035-service-adapters-in-frappe.md)).** `init` mounts `integration/` read-only and puts the stdlib-only adapters on the bench path (nothing is installed). It then writes `vati_services`, `vati_case_lifecycle` and `vati_risk_evaluator` into `taxathon.localhost`'s `site_config.json` from your ignored `.local/*.env`, `.local/jube-case-workflow.json` and root `.env`; only service names are printed. Run the service setup first (`jube_dev.py up`, `jube_rules.py apply --approve-local`, `jube_cases.py provision`, and the Docling/OpenAleph/yente `env`/`up` commands you need), then **re-run `init` once** with your usual `--project` and ports; re-running is idempotent. A service without local config is left out and reported by the app as `upstream_unavailable` (`<service>_not_configured`), never faked. The container reaches your services through `host.docker.internal`; pass `--service-host NAME` (or set `VATI_SERVICE_HOST`) if your Docker uses another name. With Jube running, `verify --live-services` also proves on the clean site that an officer decision reaches the real Jube.

**Online Refunds simulator (TAX-125, ADR-043).** `init` sets `vati_online_refunds_simulator` on `taxathon.localhost`, so the developer site keeps the portal's claim form as a labelled simulator of RSL's Online Refunds Application. Other sites leave it unset: taxpayers file claims with RSL, and the form, its route and its API methods are absent. To show taxpayers a link to RSL's dashboard, set `vati_rsl_links` to `{"online_refunds_dashboard": "https://..."}` (https only); Vati does not guess RSL's URLs.

Open http://127.0.0.1:58000. The local user is `Administrator`; find its generated password under `FRAPPE_ADMIN_PASSWORD` in your ignored `.env`. Never commit or share that file. Each teammate initializes their own isolated site. Initial installation downloads framework dependencies and builds assets, so allow several minutes.

The script creates only the `rsl-taxathon-frappe-dev` stack and `taxathon.localhost` site. It pins Frappe v16.54.0 at the revision in `integration/frappe-framework.lock.json`, installs the mounted `backend/vati_tax` package in editable mode, registers the app and runs migrations. Re-running init preserves the existing site and credentials. The start command uses a process lock to avoid duplicate developer servers. Logs are inside the container at `/workspace/bench.log`. Stop this stack with `docker compose -f docker-compose.frappe.yml stop`; no volume deletion is needed.

The bench development image is pinned to the tested digest in docker-compose.frappe.yml. Framework code is also pinned. MariaDB/Redis use version tags; any runtime upgrade needs a smoke test. Development mode and the Administrator account are local-only. TAX-019/029/031 cover domain schemas, roles and application behavior before pilot use.

## Verified on the setup machine

- Framework initialization, Vati app installation and database migration succeeded; repeating initialization also succeeded without replacing the site.
- `GET /api/method/ping` returned HTTP 200 with pong.
- Unauthenticated `GET /api/method/vati_tax.api.health` returned HTTP 403.
- Local Administrator login and authenticated Vati health returned HTTP 200, explicitly reporting `status: scaffold` and `live_integrations: false`.
- Site uses separate MariaDB/Redis volumes and loopback ports 58000/59000; existing Vati and Lekuka sites were not reused.

This is a working coding environment, not a completed domain application or a production deployment. The other external-service adapters remain implementation tasks.
