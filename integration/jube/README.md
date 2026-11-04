# jube

Risk evaluation and operational investigation case management. Owner: Keletso; LangGraph implementation is owned by Gupta.

- Upstream: https://github.com/jube-home/aml-fraud-transaction-monitoring (AGPL-3.0, kept external, never copied here)
- Official documentation: https://www.jube.io/docs/GettingStarted/DeployingWithDocker/
- Configuration: `JUBE_BASE_URL`
- Status: isolated development stack running and smoke-tested (TAX-025). Versioned synthetic tax-risk rules configured and verified on the local stack (TAX-035). Case lifecycle adapter with live contract tests (TAX-034). Nothing calls the adapter yet: the outbox (TAX-033), the Frappe bridge (TAX-039) and decision execution (TAX-046) wire it into the product.

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

## Explainable tax-risk rules (TAX-035)

`rules/tax_risk_rules.v1.json` is the reviewable source of truth: ruleset version, per-rule semantic versions, the derived inputs, conditions, evidence selectors, explanations and what each signal never implies. `tax_risk.py` validates it, derives inputs from linked synthetic records (`data/synthetic/tax024_records.json`), evaluates every rule deterministically and renders the Jube scripts and payload from the same conditions, so the two cannot drift.

```sh
python3 scripts/jube_rules.py evaluate            # no Jube needed; --full prints every rule result
python3 scripts/jube_rules.py apply --approve-local
python3 scripts/jube_rules.py verify              # invokes Jube per scenario, compares activations
```

| Rule | Version | Condition | Signal / cleared code | Evidence |
|---|---|---|---|---|
| `TR-VAT-REFUND-MISMATCH` | 1.0.0 | refund claim > 0 and claimed input VAT − invoice VAT > 0 | `refund_claim_exceeds_documented_input_vat` / `refund_arithmetic_matches_documents` | return + VAT-bearing purchase invoices |
| `TR-RELATED-PARTY-FEES` | 1.0.0 | net purchases under related-party contracts > 0 | `related_party_transaction` | related contracts + invoices |
| `TR-PERSISTENT-LOSSES` | 1.0.0 | consecutive loss quarters ending at the return period ≥ 3 | `consecutive_loss_periods` | return |
| `TR-IMPORTED-SERVICE-GAP` | 1.0.0 | purchase invoices with `vat_status` `treatment_unverified` > 0 | `imported_service_treatment_unverified` | contracts + invoices |

Each result records `ruleset_version`, `rule_version`, outcome (`signal`, `cleared`, `no_signal`, `not_applicable`), input values as decimal strings, evidence IDs and a SHA-256 digest of the source record. Signals request human review or evidence; they are not assessments, refund decisions, fraud findings or legal conclusions. SYN-003's classification gap and SYN-005's policy gap are deliberately not rule signals (TAX-051 and TAX-052).

Expected outcomes: SYN-001 cleared; SYN-002 refund mismatch + related party + losses; SYN-003 none; SYN-004 refund mismatch + imported service; SYN-005 cleared.

How it maps to Jube (locked source, docs `Configuration/Models` and `Concepts/MakerChecker`):

- One model, `Vati synthetic tax risk v1`, entry `$.case_id`. Each derived input is a Request XPath (float for money, integer for counts). Jube receives decimal strings and compares them as doubles. Integral thresholds keep that comparison exact for these values; the Python evaluator uses `Decimal` and stays the reference.
- A `Return True` gateway rule (`TR-GATEWAY-ALL v1.0.0`) is required, because Jube evaluates activation rules only after a gateway rule matches.
- Activation rules are coder scripts (`ruleScriptTypeId` 2) named `RULE-ID vX.Y.Z`. A changed rule is updated in place, so Jube keeps its own version history. Case creation is off; cases belong to the TAX-034 adapter.
- Jube's maker-checker loads only approved entities. `--approve-local` approves as `Administrator` using the landlord self-approval privilege, on the local synthetic stack only. A shared deployment needs a separate human checker.
- Invocation is admitted only for a user with a live API key whose role is granted the model. `apply` provisions `vati-risk-invoker`, whose role has no permissions and only this model grant, inside the non-landlord tenant described below. Its key goes only to the ignored `.local/jube.env` (`JUBE_RISK_INVOKER_API_KEY`). Keys missing from that file cannot be recovered and are revoked before a new one is made. A wrong key on invoke returns 401.
- Since TAX-034, `apply` and `verify` run with Administrator switched into the `Vati synthetic (local)` tenant, and `apply` asks Jube to synchronise that tenant's models (a new tenant has no synchronisation schedule, so its model answers 404 until one is requested). `apply` also revokes the key of the first TAX-035 invoker, `vati-tax-risk-invoker`, and deactivates it: it had been created in the landlord tenant, which made it an installation administrator (see Least privilege).
- Jube cannot hold evidence IDs, explanations or decimal-exact amounts on a rule. Those stay in the Vati evaluator output; Jube reports which versioned rule names fired.

## Case lifecycle adapter (TAX-034)

`case_lifecycle.py` is a client and mapping layer, not a second case store. Jube holds the case, its status, assignee and closure; every value the adapter returns was just read from Jube. `case_workflow.v1.json` is the reviewable workflow definition. Design and tradeoffs: [ADR-008](../../docs/decisions/ADR-008-jube-case-lifecycle-adapter.md).

```sh
python3 scripts/jube_rules.py apply --approve-local   # model + risk invoker in the Vati tenant
python3 scripts/jube_cases.py provision               # workflow, statuses, roles, adapter key
python3 scripts/jube_cases.py officer --officer-id UUID   # synthetic Jube user for assignment
python3 scripts/jube_cases.py contract                # live contract tests with the adapter key
```

`provision` writes the adapter key to `.local/jube.env` (`JUBE_CASE_ADAPTER_API_KEY`) and the non-secret workflow and status GUIDs to ignored `.local/jube-case-workflow.json`. The adapter refuses to start without them.

| Operation | Jube call (locked source) | Mapping and replay behaviour |
|---|---|---|
| `ensure_case` | `POST /api/Case/CreateFromCaseKeyValue` with case key `CaseId` = Vati `case_id` | Checks the Vati service link, then `GET /api/Case/` by key (closed cases included), then creates in `Received`. Records the `jube`/`case` link through TAX-030 `record_link`. Jube's 409 for an open case with the same key resolves to the existing case. A create that times out is looked up, never resent. Jube 404 (no archived invocation yet) is `invalid_domain_input`/`risk_evaluation_missing`, retryable |
| `get_case` | `GET /api/Case/{id}` | Projects the Jube status to `CaseStatus`; an unmapped Jube status is an error, never a guess |
| `transition` | `PUT /api/Case/` | Target is a Vati status. Already there: no request (replay). `expected_status` differs: `version_conflict`. `RESOLVED` needs a decision UUID and sets Jube closed status 3; a closed case is never reopened here |
| `escalate` | `PUT /api/Case/` | Moves to `Escalated to supervisor` (higher Jube priority), projected as `AWAITING_HUMAN` with `escalated: true` |
| `assign` | `PUT /api/Case/` `LockedUser` | Jube has no assignee field; `LockedUser` (lock flag unchanged) names `vati-officer-<officer UUID>`, a user in the same Jube tenant. `expected_officer_id` guards stale writes |

| Jube status | Vati `CaseStatus` |
|---|---|
| Received | RECEIVED (initial) |
| Validating | VALIDATING |
| Investigating | INVESTIGATING |
| Awaiting evidence | AWAITING_EVIDENCE |
| Awaiting human review | AWAITING_HUMAN |
| Escalated to supervisor | AWAITING_HUMAN, escalated |
| Resolved | RESOLVED (closes the Jube case) |
| Failed processing | FAILED_PROCESSING |

No status sends a Jube notification or webhook. Errors use the contract `ErrorCode` values and never include Jube response bodies or keys. Reads and updates retry at most 3 times on timeouts, 429, 502, 503 and 504, honouring `Retry-After`. Creation is not retried by the adapter. The caller authorises the action (TAX-029 `require_action`) and passes the tenant it resolved; one adapter instance serves exactly one Vati tenant and conceals any other as `not_found`.

Jube behaviour the callers must know (from source, confirmed on the live stack):

- Every case update needs an investigator rating of 1-5, and new cases have rating 0. The first update must therefore carry the officer's `rating`; the adapter does not invent one and answers `jube_rating_required`.
- Jube has no version number or compare-and-set on cases. The adapter reads, compares and then writes. A Jube user editing the same case between those two steps wins.
- A case that a Jube user has locked can only be changed by that user (Jube 409, mapped to `version_conflict`/`locked_in_jube`).
- Jube raises cases only from archived model invocations, so the risk invocation (TAX-035 model, TAX-039 bridge) must precede case creation. Archiving is asynchronous; the first seconds after an invocation give `risk_evaluation_missing`.
- `GET /api/Case/` is unbounded. It is used only for the key lookup during creation, inside a tenant that holds only Vati cases.

### Least privilege (TAX-035 follow-up)

Finding: Jube grants every permission, in every tenant, to any user whose role belongs to a landlord tenant (source `Jube.Data/Security/PermissionValidation.cs` `IsLandlordAsync`; Jube docs `Concepts/MultiTenancy/EnablingMultiTenancyAndSwitching`). The default tenant 1 is the landlord. The first TAX-035 invoker's role had no permissions, but it lived in tenant 1, so its key could list tenants, roles and users and was in effect an installation administrator.

Fix: all Vati identities now live in a separate non-landlord tenant, `Vati synthetic (local)`. Jube scopes models, workflows, cases, roles and users to the tenant, so the adapter cannot see the landlord tenant's data either. The old invoker key is revoked and its user deactivated.

| Identity | Role permissions | Grants |
|---|---|---|
| `vati-case-adapter` | `Read Write Case` (1) only | the Vati workflow and its 8 statuses |
| `vati-risk-invoker` | none | invoke the Vati risk model |
| `vati-officer-<uuid>` (synthetic, no password or key) | `Read Write Case` (1) | the Vati workflow and statuses |

What Jube cannot restrict further:

- Jube has no read-only case permission. `Read Write Case` is needed and allows every case write in the tenant, including notes and files.
- `Read Write Case` also lets the adapter list its tenant's model configuration (`GET /api/EntityAnalysisModel/`) and the tenant's user allocations (`GET /api/UserInTenant/`), because those list routes accept permission 1. Only its own tenant is visible.
- API keys have no scopes or expiry. A key acts with its user's role until it is revoked.

The contract run checks these limits each time and fails if any of them widens.

### Recorded contract result (2026-11-04)

Locked source `01c4afd…`, local stack. Each `contract` run uses a new synthetic case UUID and the SYN-002 synthetic risk payload. All checks passed:

| Check | Observed |
|---|---|
| Risk invocation with the invoker key | 200 |
| Create | new Jube case in `Received` (RECEIVED), rating 0 |
| Replay through the recorded link | same Jube case, no create request |
| Replay with the link lost (crash before recording) | found by key in Jube, same case, no create request |
| Raw duplicate `CreateFromCaseKeyValue` | Jube 409; exactly one Jube case for the `case_id` |
| Update without a rating | `invalid_domain_input`/`jube_rating_required`, nothing sent |
| RECEIVED → VALIDATING (rating 1) | changed in Jube; repeat is a no-op; stale `expected_status` gives `version_conflict` |
| Assign an officer without a Jube user | Jube 400 `LockedUserInTenant` → `officer_not_provisioned` |
| Assign `vati-officer-…` | `LockedUser` set, lock unchanged; repeat no-op; stale expectation gives `version_conflict` |
| Escalate from INVESTIGATING | `Escalated to supervisor`, projected AWAITING_HUMAN, escalated; repeat no-op |
| Resolve without and with a decision UUID | 400 without; with it RESOLVED and Jube closed status 3 |
| Change a closed case | `case_closed`; a later `ensure_case` with no link finds the closed case, no second case |
| Another Vati tenant | `not_found` (`tenant_not_served`) |
| Adapter key | 200 only for `/api/Case/`, `/api/EntityAnalysisModel/` (own model only) and `/api/UserInTenant/`; 403 for user, role, tenant, workflow, status and rule routes |
| Invoker key | 403 on all of those routes |
| Wrong key / retired landlord key | 401 |

`jube_rules.py verify` against the re-homed model: all five scenarios matched the evaluator.
