# yente

Candidate entity matching. Owner: Keletso; LangGraph implementation is owned by Gupta.

- Upstream: https://github.com/opensanctions/yente (code MIT; OpenSanctions **data** is separately licensed and is not used here)
- Official documentation: https://yente.followthemoney.tech/
- Configuration: `YENTE_BASE_URL`
- Status: isolated development stack running with a synthetic-only dataset and smoke-tested (TAX-027). No Vati candidate-review adapter yet (TAX-037).

## Run it (TAX-027)

```sh
python3 scripts/yente_dev.py up      # checks the manifest is local-only, generates .local/yente.env, starts
python3 scripts/yente_dev.py smoke   # catalog, alias match, ambiguous candidates, negative checks
python3 scripts/yente_dev.py status
python3 scripts/yente_dev.py stop    # stops containers; the index volume is kept
```

- API: http://127.0.0.1:55800/ (loopback only); interactive docs at `/docs`. yente itself has **no authentication**, so it must stay on a private network behind the Vati adapter.
- Compose project `rsl-taxathon-yente` (`integration/yente/docker-compose.yente.yml`). Elasticsearch is not published to the host. Index volume `yente-index`.
- Data: `integration/yente/data/vati_synthetic.ftm.json` (5 synthetic FollowTheMoney entities, each labelled as synthetic). Manifest: `integration/yente/manifest.yml` with `catalogs: []` and one local `path` dataset. Bump its `version` when the data changes.
- `up` refuses to start if the manifest's active lines contain a URL, `entities_url`, `auth_token` or a delivery token, so licensed datasets cannot be downloaded silently.

## Deliberate differences from upstream `docker-compose.yml`

| Upstream | Here | Why |
|---|---|---|
| `YENTE_MANIFEST: /app/manifests/commercial.yml` + `OPENSANCTIONS_DELIVERY_TOKEN` | Local manifest, `catalogs: []`, no token | Synthetic data only; no licensed downloads (TAX-027) |
| `container_name: index`; Elasticsearch on `127.0.0.1:9200` | Compose-scoped names; Elasticsearch unpublished | Avoids clashes; nothing needs host access to the index |
| Elasticsearch heap 4 GB, `memory_lock` | 768 MB heap, 1.5 GB limit | Five entities; shared laptop |
| yente on `127.0.0.1:8000` | `127.0.0.1:55800` | Avoids a common port; the Frappe dev stack uses 58000 |
| `YENTE_UPDATE_TOKEN` default `unsafe-default` | Random token in `.local/yente.env` | `/updatez` cannot be triggered without it |
| Tags only | Tags plus digests | Reproducible |

## Recorded smoke result (2026-11-03)

| Item | Observed |
|---|---|
| Source lock | `6580a2aae2b662559d75301f06820bb0b8f7a122` (2026-10-27), `pyproject.toml` version 5.5.0 |
| Images | `ghcr.io/opensanctions/yente:5.5.0@sha256:4e46bda3fc0552725b695e5f4691c168e4241f14766405fe3c1b8d959315c137`; `elasticsearch:9.4.2@sha256:be5f49784ff5ec8a5b5d7ba17f944d9d6b10c067f596ee93e6b6cb82d2dd874c` (the version upstream's compose pins) |
| Health | `/healthz` 200, `/readyz` 200 |
| Catalog | Only `vati_synthetic`, version `2026110301`. Logs show `catalog=None`, "Indexed 5 entities", and no requests to `data.opensanctions.org` or `delivery.opensanctions.com` |
| Alias match | Query name "SBR Trading" (an alias) returned `vati-syn-co-border-retail` "Synthetic Border Retail (Pty) Ltd" as the top result, score 1.000, `match: true` |
| Ambiguous candidates | Query "Synthetic Highland Imports" returned **two** candidates: `vati-syn-co-highland-imports` (0.943, match) and `vati-syn-co-highland-import-services` (0.880, match), both from dataset `vati_synthetic` version `2026110301`. Both clear the match threshold, so a human must choose; similarity is not identity |
| Negative checks | Unrelated name "Quartz Lantern Fisheries": no matches. `POST /match/opensanctions`: 404 (not loaded). `POST /updatez` without the token: 403 |
| Memory (idle) | yente 402 MiB of 768 MiB; Elasticsearch 1.17 GiB of 1.5 GiB |

## API used

| Purpose | Route |
|---|---|
| Health | `GET /healthz`, `GET /readyz` |
| Loaded datasets and versions | `GET /catalog` |
| Matching | `POST /match/{dataset}` with `{"queries": {"<key>": {"schema": "Company", "properties": {"name": [...]}}}}`; results contain `id`, `caption`, `score`, `match`, `datasets` |
| Forced re-index | `POST /updatez` with the update token |

For TAX-037: the adapter must record dataset name and version with every candidate (from `/catalog`), mark every candidate `unreviewed` until an officer confirms or rejects it, and never treat `match: true` as identity or wrongdoing. This follows `MatchCandidate` in `integration/api/openapi.json`.
