# Service source and development setup

## Source checkout

```sh
python3 scripts/upstreams.py list
python3 scripts/upstreams.py fetch --all
python3 scripts/upstreams.py check --all
```

This fetches official source at immutable commits from `integration/upstreams.lock.json` into ignored `external/`. It does not create forks, start services or install proprietary datasets. The recorded commits are source baselines, not a claim of release certification; OpenAleph's selected source identifies itself as a release candidate. Review/upgrade locks through a PR after testing. Dirty source checkouts are never overwritten.

| Name | Source purpose | Local entrypoint / next step |
|---|---|---|
| jube | Risk and operational case engine | `python3 scripts/jube_dev.py up` (isolated compose in integration/jube, not upstream's file); runbook integration/jube/README.md; TAX-034/035 next |
| openaleph | Evidence search/graph | external/openaleph/docker-compose.example.yml and contrib/start-local.sh; TAX-026 |
| yente | Matching API | external/yente/docker-compose.yml; replace commercial manifest with synthetic data in TAX-027 |
| docling | Extraction server | docker-compose.docling.yml; TAX-028 |
| frappe | Vendor development tooling | docker-compose.frappe.yml and external/frappe/docs/05-development; TAX-019 |
| followthemoney | Entity library source | Validate selected schema mappings in TAX-036 |
| langgraph | Workflow library source | AI package dependencies and TAX-022/047 |

Do not run all vendor default Compose files blindly: Jube uses fixed container names/ports, a monitoring sidecar with a Docker socket mount and optional external loaders; yente's example selects a commercial dataset and a 4GB search heap. TAX-025/027 must isolate names/ports, turn off unnecessary outbound loaders, avoid host socket access and select synthetic datasets. Our root database/cache do not replace vendor internal stores.

## Immediate fixture work

`python3 scripts/dev.py serve` starts both UI shells and five scenario summaries without any vendor dependency. It exposes only explicit UI/API routes, never .env or source directories. This is sufficient to begin independent frontend, contract, data and deterministic workflow tasks. Fixture mode cannot prove live integration.

## Vati database/cache

```sh
python3 scripts/dev_env.py
docker compose config --quiet
docker compose up -d --wait
```

PostgreSQL localhost:55433; Redis localhost:56380. Already smoke-tested on the setup machine. These ports avoid existing Lekuka containers. Do not stop or modify other projects.

## Frappe development container

```sh
python3 scripts/frappe_dev.py init
python3 scripts/frappe_dev.py start
```

This provisions the pinned framework, isolated MariaDB/Redis, `taxathon.localhost` site and editable Vati app. Installation, migration, local login and access-controlled health checks passed on the setup machine. Read [FRAPPE_DEV.md](FRAPPE_DEV.md) for credentials, repeat setup, logs and runtime pinning limitations. App ports are loopback 58000/59000. TAX-019 continues with domain application behavior; no existing Vati Frappe sites are reused.

## Docling

`docker compose -f docker-compose.docling.yml up -d` exposes localhost:55001, requires generated DOCLING_API_KEY and disables remote model services. Initial models can require substantial disk/RAM. Record authenticated extraction and denied-access tests in TAX-028 before marking connected.

## AI environment

```sh
python3 -m venv .venv-ai
.venv-ai/bin/python -m pip install -r ai/requirements.lock
.venv-ai/bin/python ai/smoke.py
```

Locked Python dependencies support a model-free LangGraph smoke test. Model credentials are intentionally absent; add them only through an approved local/secret-store configuration when implementing model-backed tools. No external taxpayer data is sent by setup.

## Readiness states

Source fetched, container configured, container running, site initialized, adapter implemented and end-to-end verified are distinct states. Update `integration/services.json` with actual evidence as tasks progress. Do not call the entire platform deployed because Docker started a database.
