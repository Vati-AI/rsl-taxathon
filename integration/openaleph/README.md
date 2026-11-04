# openaleph

Evidence search and entity graph. Owner: Keletso; LangGraph implementation is owned by Gupta.

- Upstream: https://github.com/openaleph/openaleph (kept external, never copied here)
- Official documentation: https://openaleph.org/docs/
- Configuration: `OPENALEPH_BASE_URL`
- Status: isolated development stack running and smoke-tested (TAX-026). Replay-safe entity ingest for the FtM tax mapping (TAX-036), tested against a fake of the locked `_bulk` route; it has not been run against the live stack yet.

## Run it (TAX-026)

```sh
python3 scripts/upstreams.py fetch openaleph   # locked source (reference only; images are prebuilt)
python3 scripts/openaleph_dev.py up            # generates .local/openaleph.env once, pulls, upgrades DB, starts
python3 scripts/openaleph_dev.py smoke         # two synthetic users, restricted collection, ingest, access checks
python3 scripts/openaleph_dev.py status
python3 scripts/openaleph_dev.py stop          # stops containers; data volumes are kept
```

- UI and API: http://127.0.0.1:55580/ (loopback only). The UI's nginx proxies `/api/` to the API container, which has no host port.
- Two synthetic, non-admin users (`synthetic-officer-a@vati.invalid`, `synthetic-officer-b@vati.invalid`) are created by `smoke`. Their API keys are stored only in the ignored `.local/openaleph.env`. API calls use `Authorization: ApiKey <key>`.
- Compose project `rsl-taxathon-openaleph` (`integration/openaleph/docker-compose.openaleph.yml`), volumes `oa-postgres`, `oa-elasticsearch`, `oa-redis` and `oa-archive`.
- Memory: about 4 GB in total while idle (see below). Stop Jube (`python3 scripts/jube_dev.py stop`) first on an 8 GB Docker VM.
- Reset disposable synthetic data only on purpose: `docker compose -f integration/openaleph/docker-compose.openaleph.yml --env-file .local/openaleph.env down -v`, then remove the `OA_USER_*` and `OA_COLLECTION_A_ID` lines from `.local/openaleph.env`.

## Deliberate differences from upstream `docker-compose.example.yml` and `contrib/start-local.sh`

| Upstream | Here | Why |
|---|---|---|
| `start-local.sh` sets `ALEPH_SINGLE_USER=true` (no login) | `ALEPH_SINGLE_USER=false`, password login, separate users | TAX-026 must prove that one user cannot read another's collection |
| Postgres `aleph`/`aleph` | Random password; all of `OPENALEPH_DB_URI`, `PROCRASTINATE_DB_URI`, `FTM_FRAGMENTS_URI`, `ALEPH_DATABASE_URI` set explicitly | The image bakes default `OPENALEPH_DB_URI`/`PROCRASTINATE_DB_URI` values that override `ALEPH_DATABASE_URI`. Without them, `aleph upgrade` fails with a password error |
| All images `:latest` | Version tags plus digests (table below) | Reproducible |
| `ingest-file:${ALEPH_TAG}` (5.4.0-rc1) | `ingest-file:5.4.0-rc4` | The rc1 image (built 2026-10-01) crashes on every job from the locked API (`AttributeError: 'str' object has no attribute 'get'` in `followthemoney` `from_dict`). rc4 (built 2026-11-02) contains the same `openaleph-procrastinate` commit `a9c0ef12` and `followthemoney` 4.11.2 as the locked `openaleph` image, verified inside both images |
| API image starts 6 gunicorn workers | 2 workers, 1.5 GB limit | 6 workers were OOM-killed under 1 GB; 2 serve one developer |
| UI on `0.0.0.0:8080` | `127.0.0.1:55580` | Loopback only |
| No limits or healthchecks | Healthchecks for Postgres/Elasticsearch/Redis; memory limits; Elasticsearch heap 512 MB | Shared developer machine |

`ftm-analyze` is only published for amd64 and runs under emulation on Apple silicon. It is slower but worked in the smoke test.

## Recorded smoke result (2026-11-03)

| Item | Observed |
|---|---|
| Source lock | `f89ff18d83fcfebdadc6b2db83fb9e2fbb5397c2` (2026-11-02), version `5.4.0-rc1`. This is a **release candidate** |
| `openaleph` image | `ghcr.io/openaleph/openaleph:5.4.0-rc1@sha256:a7530362c4f63c86792f5a3144e46a7ea722e9be1a3e2b7234966edbb6c0982c`; its OCI revision label equals the locked commit |
| Other images | `ingest-file:5.4.0-rc4@sha256:daa60370d9ece3333bbfc87869b6813bc2cef2f7e7941ddb2498335adce07dc2`; `ftm-analyze@sha256:6cd6b5ad8dba24f04730a3f92a497aaa14fd6058d5de5ca950ea9eed9c8720af`; `elasticsearch@sha256:30ad573a1288604f520db4e1023036f259307201a6aac24694da1059be41f472`; `aleph-ui:5.4.0-rc1@sha256:9f11457659ef696dd4037c5e97085ff99e865eb290b4b923fc6024d5fee91378`; `postgres:17@sha256:3b0cda93be7c8dba2ed484d19393d93845dc3dafb88973e248ba8e6d99d784e1`; `redis:7-alpine` |
| Metadata | `GET /api/2/metadata` 200, app version `5.4.0-rc1` |
| Restricted collection | User A created collection 1 (`tax026_synthetic_restricted_a`); private to its creator by default |
| Ingest and retrieve | Synthetic text invoice uploaded (`POST /api/2/collections/1/ingest` returned 201). It was searchable after about 21 s through ingest, analyze and index. User A retrieved the `PlainText` entity (200) with `contentHash` and the marker text |
| Denied access | User B: collection 403, entity 403, search 0 hits. Anonymous: collection 403, search 0 hits, create collection 403. Invalid API key on the collection: 403 |
| Repeatability | Second smoke run reused users, collection and document, and returned the same results |
| Memory (idle) | api 1.0 GiB, elasticsearch 0.96 GiB, worker 0.77 GiB, analyze 0.57 GiB, ingest 0.51 GiB, postgres 73 MiB, redis 8 MiB, ui 4 MiB (about 3.9 GiB in total) |

## API surface used (from the locked source)

| Purpose | Route |
|---|---|
| Health/metadata | `GET /api/2/metadata` |
| Collections | `GET/POST /api/2/collections`, `GET /api/2/collections/{id}`, `GET /api/2/collections/{id}/status` |
| Ingest a document | `POST /api/2/collections/{id}/ingest` (multipart: `file`, `meta` JSON) |
| Search and entities | `GET /api/2/search?q=…&filter:collection_id=…`, `GET /api/2/entities/{id}` |

The full OpenAPI description is served by the API at `/api/openapi.json`. Check it there before adding routes to the adapter (TAX-036). Collections map to Vati tenant/case scope in that task; never use a public collection for taxpayer evidence.

## Entity ingest (TAX-036)

`entity_ingest.py` writes entities from `integration/followthemoney/tax_mapping.py` with `POST /api/2/collections/{id}/_bulk` (locked source `aleph/views/collections_api.py`; 204 on success). Use a non-admin key with WRITE on only that tenant's restricted collection. OpenAleph then forces `safe` and `clean`, which strips checksum properties, so the evidence SHA-256 travels in the Vati extension, not in `contentHash`.

- The caller keeps a ledger of `entity_id -> digest` per collection. A repeated ingest sends nothing, and new records are sent alone.
- A changed entity is refused as `idempotency_conflict` without sending anything. Bulk writes go through FtM's fragment aggregator, so the adapter does not rely on a rewrite replacing old values. Re-index the collection after review instead.
- Errors use contract codes: 401/403 `forbidden`, 429 `rate_limited` and 5xx `upstream_unavailable` (both retryable), other 4xx `invalid_request`. The ledger grows only after a batch is accepted.
