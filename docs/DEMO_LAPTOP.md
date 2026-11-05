# Demo laptop: reduced presentation profile and rehearsal runbook (TAX-095)

`scripts/demo.py` runs the RSL presentation on one laptop. It is the **reduced
recorded-data demo**, not the full pilot profile.

| Service | Demo profile | How it runs |
|---|---|---|
| Frappe (site, Vati app, portal, officer UI host) | live | `docker-compose.frappe.yml`, project `rsl-taxathon-frappe-dev`, loopback 58000/59000 |
| Jube (cases, risk) | live | `scripts/jube_dev.py`, project `rsl-taxathon-jube`, loopback 55501 |
| AI runtime | live | in-process ADR-031 router: hosted (Groq GPT-OSS 120B/20B, Gemini `gemini-3.6-flash`), then an optional local Ollama model, then the deterministic template |
| Docling | recorded | captured beforehand from a real run, not started |
| OpenAleph | recorded | captured beforehand from a real run, not started |
| yente | recorded | captured beforehand from a real run, not started |

Recorded output never counts as native-UI acceptance. Full acceptance of Jube case
work and OpenAleph search/graph needs reachable, authorized native Jube and
OpenAleph interfaces on measured, suitable local or remote hardware (TAX-105,
`docs/UI_INTEGRATION_SCOPE.md`). This profile does not provide those.

## Commands

```sh
python scripts/demo.py up             # loopback check, start Jube then Frappe, wait for health, time it
python scripts/demo.py status         # live / recorded / unavailable per service and the model level
python scripts/demo.py status --html .local/demo/status.html   # the same as a static page
python scripts/demo.py memory --samples 12 --interval 5 --limit-mib <laptop limit>
python scripts/demo.py capture docling|openaleph|yente          # that service must be running
python scripts/demo.py verify-live    # TAX-085 live check: frappe_dev.py verify --live-services
python scripts/demo.py down           # docker compose stop; volumes and data are kept
```

- **Access:** `up` refuses to start if a live Compose file publishes a port that is
  not bound to `127.0.0.1`. `status` also inspects the running containers'
  published ports and marks the demo NOT READY if any listens beyond loopback.
  Frappe and Jube keep their own logins. Nothing is exposed on the LAN.
- **Status:** a service is `LIVE` only after a successful probe (Frappe
  `/api/method/ping` 200; Jube Swagger 200 or 401, as in `jube_dev.py`). A recorded
  service is `RECORDED` only when a valid capture exists, otherwise `UNAVAILABLE`
  with the reason. Nothing is faked. Exit code 1 means not ready.
- **Model level:** `status` builds each role's chain with the router itself
  (`get_model(role).chain`) and shows the highest available level: `hosted`,
  `local` (only if Ollama answers `/api/tags`) or `template`. `MOCK` means no keys
  or mock mode, and blocks readiness. Hosted levels are configured, not called. The
  level a call actually used is recorded on that output's provenance (TAX-089/096).
  Only key presence is printed, never a key.
- **Timing and memory logs:** `up` appends elapsed seconds and states to the
  ignored `.local/demo/runs.jsonl`. `memory` samples `docker stats` for the
  `rsl-*` containers, reports the peak total and per-container peaks, and appends
  to `.local/demo/memory.jsonl`. It does not include the browser, a native Ollama
  process or the Docker VM overhead. Read those from the OS (Task Manager or
  Activity Monitor) at the same time.

## Recorded outputs

`capture` runs the service script's own `smoke()` against the real running
service and writes `data/demo/recorded/<service>/<scenario>.json`. Each file
contains `recorded: true`, `synthetic: true`, a `RECORDED ...` label, the service,
scenario, pinned version (`integration/services.json`), locked source commit
(`integration/upstreams.lock.json`), `captured_at` (UTC), the capture command,
the output and its SHA-256. The loader rejects a file whose hash, flags,
timestamp, scenario or provenance is wrong, or whose output carries
credential-like fields. A rejected file is reported, not shown.

Only the `service-smoke` probe exists. Scenario captures (`SYN-001`...`SYN-005`)
need a scenario probe through the TAX-038/036/037 adapters before they can be
recorded. `capture` refuses rather than inventing one. Review a capture before
committing it: synthetic data only.

## Rehearsal (record each result as task evidence)

1. **Prerequisites, once:** `upstreams.py fetch jube`, `jube_dev.py up`, the
   Jube case/risk setup (`jube_cases.py`, `jube_rules.py`) and
   `frappe_dev.py init` have been run on the laptop. Captures for Docling,
   OpenAleph and yente were made on a machine running those services and
   committed. Stop those three stacks before measuring.
2. **Keys:** put `VATI_MODEL_PROVIDER=hosted`, `GROQ_API_KEY` and `GEMINI_API_KEY`
   only in the laptop's ignored `.env`. The Groq organisation must have Zero
   Data Retention enabled (Data Controls). Synthetic data only (ADR-031).
   Optionally set `OLLAMA_BASE_URL` and `VATI_MODEL_LOCAL`.
3. **Cold start:** reboot or stop Docker Desktop, start Docker, then run
   `python scripts/demo.py up`. Record the elapsed time from the output or
   `runs.jsonl`, and whether any manual fix was needed (the criterion requires
   none).
4. **Health:** `python scripts/demo.py status --html .local/demo/status.html`.
   Expect frappe/jube LIVE, ai LIVE (or FALLBACK if intended), and
   docling/openaleph/yente RECORDED. Run `python scripts/demo.py verify-live` once.
5. **Memory:** run `python scripts/demo.py memory --samples 60 --interval 5
   --limit-mib <limit>` while the five scenarios are walked through. Also record
   the OS-level total. The laptop limit is a human input: measure it, do not
   assume one.
6. **Models and rate limits:** `python -m ai.models.smoke --live` records the
   observed model IDs, latency, tokens and rate-limit headers per role. Copy the
   observed limits from console.groq.com/settings/limits and
   aistudio.google.com/rate-limit as well.
7. **Fallback:** disconnect the network and repeat one scenario. Confirm it
   completes, visibly labelled, on the local or template level. `status` does not
   call hosted models, so it still shows `hosted`. The fallback appears on each
   output's provenance and in the UI. Also repeat it with the keys removed:
   `status` then shows `MOCK`, or the configured local/template level when
   `VATI_MODEL_PROVIDER=local` or `template`.
8. **Disclosure:** confirm every screen shown labels live versus recorded data.
   The `status` output covers services only. Screens are TAX-105/UI work.
9. `python scripts/demo.py down`.
