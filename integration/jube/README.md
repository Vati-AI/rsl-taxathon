# jube

Risk evaluation and operational investigation case management. Owner: Keletso; LangGraph implementation is owned by Gupta.

- Upstream: https://github.com/jube-home/aml-fraud-transaction-monitoring (AGPL-3.0, kept external, never copied here)
- Official documentation: https://www.jube.io/docs/GettingStarted/DeployingWithDocker/
- Configuration: `JUBE_BASE_URL`
- Status: isolated development stack running and smoke-tested (TAX-025). No Vati adapter yet (TAX-034/035), so there is no product integration.

## Run it (TAX-025)

```sh
python3 scripts/upstreams.py fetch jube   # locked source into ignored external/jube
python3 scripts/jube_dev.py up            # generates .local/jube.env once, builds, starts, waits
python3 scripts/jube_dev.py smoke         # auth, denied access, Swagger summary, resource use
python3 scripts/jube_dev.py status
python3 scripts/jube_dev.py stop          # stops containers; data volumes are kept
```

- UI and API: http://127.0.0.1:55501/ (loopback only). Swagger UI: `/swagger`; JSON: `/swagger/v1/swagger.json`.
- Login: user `Administrator`; the password is `JUBE_ADMIN_PASSWORD` in the ignored `.local/jube.env`. The first smoke run performs Jube's mandatory first-login password change from the upstream default to that generated value.
- Compose project `rsl-taxathon-jube` (`integration/jube/docker-compose.jube.yml`). Containers are named `rsl-taxathon-jube-*`, with volumes `jube-postgres` and `jube-redis`.
- First start takes about 5 minutes while Jube migrates and seeds its database. The image build takes several minutes and about 830 MB of disk.
- Reset disposable synthetic data only on purpose: `docker compose -f integration/jube/docker-compose.jube.yml --env-file .local/jube.env down -v`. This deletes Jube data and nothing else. The next `up` plus `smoke` repeats the first-login password change with the same generated password.

## Deliberate differences from upstream `docker-compose.yml`

| Upstream | Here | Why |
|---|---|---|
| Fixed `container_name` postgres/redis/jube | Compose-scoped names | Avoids clashing with other projects' `postgres`/`redis` containers |
| Ports 5432, 6379, 8001, 5001 on all interfaces | Only Jube, on `127.0.0.1:55501` | No unauthenticated database/cache exposure |
| `jube-monitoring` mounts `/var/run/docker.sock` | Omitted | Host Docker control is not needed for TAX-025 |
| `jube-r`, `jube-otel-listener` sidecars | Omitted | Optional (`RServerUrl` R analytics, OpenTelemetry); saves about 2 GB RAM. Add back in TAX-035 only if R-based models are needed |
| `EnableSanctionLoader=True` | `False` (Jube's own default) | No outbound sanctions downloads; synthetic data only |
| `redis/redis-stack:latest`, 18 GB maxmemory | `redis:7-alpine` (as in upstream's cluster deployment), 192 MB | Pinned image; no Redis Stack modules are used by Jube source |
| Placeholder secrets from shell | Random secrets generated once into `.local/jube.env` (mode 600) | Never committed or printed |
| No limits | Postgres 768 MB, Redis 256 MB, Jube 2 GB with 1.25 GB GC heap limit | Shared developer machine |

Postgres still connects as superuser, which is acceptable for local evaluation (upstream's documented quickstart posture). `docs/GettingStarted/CreateUsers.sql` describes restricted users for any shared deployment.

## Recorded smoke result (2026-11-03)

| Item | Observed |
|---|---|
| Source | `01c4afdbcdb186cc36b128ed79283d544cfb9ec9` (upstream commit date 2026-10-25), matches `integration/upstreams.lock.json` |
| Built image | `rsl-taxathon-jube-app:01c4afd…`, id `sha256:419b4d14…`, 832 MB |
| Dependency images | `pgvector/pgvector:pg17@sha256:7d0a41464797aeb9862dfdc0bbf1526b82cfd324cdd8036df19e3545f4a9ccb8`, `redis:7-alpine@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf` |
| Readiness | `/api/ready` 200 after about 290 s on first start; container health check `healthy` |
| Authentication | Wrong password returns 401. Login with the generated password succeeds; second run logs in directly, so the setup is repeatable |
| Denied access | Unauthenticated `GET /api/Case/` returns 401 |
| API spec | Swagger/OpenAPI 3.0.1, title `Jube.App.Api`, document version `v1`, 704 paths; security schemes `Bearer` and `ApiKey`. Normalized spec SHA-256 `521a8398c4c5c95e37071931862254a09f8ba599dffba7a355f30534228f0f7e`. A full copy is saved to ignored `.local/jube-swagger.json` |
| Resource use (idle after smoke) | Jube 1.11 GiB of 2 GiB, Postgres 264 MiB of 768 MiB, Redis 7 MiB of 256 MiB |

`v1` is Jube's Swagger document name, not a release number; the source commit is the version of record.

## API surface relevant to Vati (from deployed Swagger)

All routes below require `Bearer` (JWT from login) or `ApiKey`. Request/response schemas are in the saved Swagger; the adapter tasks must use them rather than guessing.

| Purpose | Method and route |
|---|---|
| Login (returns JWT) | `POST /api/Authentication/ByUserNamePassword` (`userName`, `password`; first login also `newPassword`, `repeatNewPassword`, `passwordChangeState`) |
| Model invocation (risk scoring) | `POST /api/Invoke/EntityAnalysisModel/{guid}`, async variant `POST /api/Invoke/EntityAnalysisModel/{guid}/{async}`, result `GET /api/Invoke/EntityAnalysisModel/Callback/{guid}` |
| Model configuration | `GET/POST/PUT /api/EntityAnalysisModel/` |
| Cases | `GET/POST/PUT /api/Case/`, `GET /api/Case/{id}`, `POST /api/Case/CreateFromCaseKeyValue` |
| Case notes and files | `POST /api/CaseNote/`, `/api/CaseNote/ByCaseKeyValue`, `POST /api/CaseFile/Upload`, `/api/CaseFile/ByCaseKeyValue` |
| Case workflow/state configuration | `/api/CaseWorkflow/…`, `/api/CaseWorkflowAction/…`, `/api/CaseWorkflowDisplay/…` |

Next tasks: TAX-034 (case lifecycle adapter mapping Vati `case_id`/`service_links` to Jube case keys) and TAX-035 (tax-risk model configuration). Use a least-privilege Jube user or API key for the adapter, not `Administrator`.
