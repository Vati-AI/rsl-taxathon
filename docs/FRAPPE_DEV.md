# Frappe developer setup

From the repository root with Docker Compose running:

```sh
python3 scripts/frappe_dev.py init
python3 scripts/frappe_dev.py start
python3 scripts/frappe_dev.py status
```

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
