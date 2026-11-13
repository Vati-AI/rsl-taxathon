# Service integration plan and API boundaries

The following endpoints describe upstream contracts or proposed Vati contracts; they are not implemented adapters. Verify against the deployed release/OpenAPI before calling. `integration/services.json` records all connection states as not_connected until tested.

| Service | Interface | Responsibility / acceptance |
|---|---|---|
| Jube | Deployment Swagger `/swagger`; model endpoint configured from its generated API | Send canonical transaction facts with case/event IDs. Normalize rule signals, rule IDs and rule version: Vati corroboration of RSL's selection, never a competing risk score (ADR-053). Only RSL-referred cases are sent. Never assume a generic `/score` endpoint exists. Verify one configured tax scenario through the actual model API. |
| OpenAleph | REST API documented by deployed instance and official docs | Create tenant-scoped collection, ingest document/entity, preserve source identity, retrieve evidence. Resolve endpoint/auth from selected release; do not guess an Aleph-compatible path. |
| FollowTheMoney | Python library, schema-validated entity objects | Map Taxpayer → Company/Person, ownership → Ownership, evidence → Document as supported by the chosen schema. Preserve IDs and evidence links. Validate before indexing. |
| yente | `POST /match/{dataset}`; query entities with FtM schema/properties | Return candidates, scores and dataset version. Review ambiguous matches; never equate similarity with identity or wrongdoing. Use a synthetic/local dataset first. |
| Docling Serve | `POST /v1/convert/file`, multipart files; `X-Api-Key` when configured | Extract uploaded documents, retain original SHA-256 and page locators. Restrict formats/size, handle async jobs for long documents. Disable arbitrary URL fetching in Vati. |
| LangGraph | Embedded graph in AI worker; Vati job API to be implemented | Use persistent checkpointer, `thread_id` derived from tenant/case/run, and `interrupt()` at human approval. Authenticated resume must validate reviewer, case version and exact proposed action. |
| Frappe | `/api/resource/{DocType}`; `Authorization: token api_key:api_secret` | Frappe owns submissions, permissions, evidence requests and approved decisions; operational case state is projected from Jube. Use least-privilege service user, explicit correlation mapping and origin marker to prevent webhook loops. |

## Proposed internal event envelope

```json
{
  "schema_version": "1",
  "event_id": "uuid",
  "tenant_id": "uuid",
  "case_id": "uuid",
  "correlation_id": "uuid",
  "type": "evidence.received",
  "occurred_at": "2026-11-03T10:00:00Z",
  "idempotency_key": "tenant:source:event",
  "payload": {"evidence_id": "uuid"}
}
```

Bodies reference restricted evidence IDs rather than embedding complete documents. Keep provider-specific IDs behind adapter interfaces. Return normalized errors: service, code, retryable, correlation_id; redact secrets and content. Non-idempotent upstream calls require a persisted deduplication record before retries. Treat timeouts as unknown/failed processing, never success.

## Deploying optional services

The root Compose stack starts only shared local infrastructure. Jube, OpenAleph, yente and Frappe require their own vendor-supported stacks; a single guessed image is insufficient. Clone them outside the product source into ignored `external/` if needed, follow their official instructions and set base URLs in `.env`. Never reuse the root database as their internal database without an explicit migration/ownership plan.

- Jube: upstream Compose builds from source and supplies its database/cache. Generate all required signing/encryption/password secrets and change initial admin credentials before network exposure. Select an unused local port.
- OpenAleph: use the selected release's documented deployment and its search/worker/storage services. Confirm collection permissions before loading anything beyond synthetic records.
- yente: use its documented index/database dependencies and a custom synthetic dataset manifest. Code and data licensing are separate; do not assume OpenSanctions datasets are unrestricted.
- Docling: optional `docker compose -f docker-compose.docling.yml up -d` after setting `DOCLING_API_KEY` in `.env`. First use downloads models; allow sufficient RAM/disk. The root project never sends documents to it automatically.
- Frappe: use frappe_docker and provision a site plus custom DocTypes. It is a required application/portal component. See ADR-001 and SERVICE_SETUP.md.
- LangGraph / FtM: add reviewed, locked versions in the AI/adapter environments when implementing TAX-007/012.

Services running in containers cannot reach host services through `localhost`; use the appropriate private network hostname or `host.docker.internal` on Docker Desktop. Only loopback ports are suitable for this unauthenticated dev environment. Production requires private networking, TLS, secret management and access controls.

## Upstream policy

No fork is needed for current contracts. If an upstream bug requires modification, open a task explaining the patch, license implications, maintenance owner and upstream PR strategy before creating a fork. Do not change upstream licenses or copy upstream source into this private monorepo. Record tested image digests, commits and data licenses in each adapter README when the integration passes.

Official references: [Jube](https://www.jube.io/docs/GettingStarted/DeployingWithDocker/), [OpenAleph](https://openaleph.org/docs/), [FtM](https://followthemoney.tech/), [yente](https://yente.followthemoney.tech/), [Docling Serve](https://github.com/docling-project/docling-serve/blob/main/docs/usage.md), [Frappe REST](https://docs.frappe.io/framework/user/en/api/rest), [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts).
