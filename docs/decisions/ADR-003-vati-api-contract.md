# ADR-003: Vati integration API contract format and request rules

Status: proposed (TAX-018, pending human review)
Owner and task: Keletso, TAX-018
Date: 2026-11-03

Context and smallest prototype need: Frontend, Frappe, AI and adapter tasks need one executable contract for cases, evidence, entities, findings, investigations and evidence requests before any Vati API code exists. The contract must express tenant context, case mapping, idempotency and errors, and CI must validate it without network access or new runtime dependencies.

Decision:

- `integration/api/openapi.json` is OpenAPI 3.1.1 in JSON (no YAML parser needed). All product routes are under `/api/v1`, following API_STANDARD.md.
- Tenant selection uses the required `X-Vati-Tenant-ID` header on every operation. The server resolves the authenticated principal first; a header outside the permitted scope returns `403 tenant_scope_denied`, and a missing or malformed header returns `400 missing_tenant_context`. A missing or malformed `case_id` returns `400 invalid_case_id`. Response bodies carry `tenant_id`/`case_id`, and examples are checked for consistency with the request.
- Case mapping is a `service_links` array (service, external type, external ID, sync state). The case `status` is a projection with `status_authority: "jube"`, not a second lifecycle (ADR-001).
- Every mutation requires `Idempotency-Key`. Same key with the same digest replays the original response with `Idempotent-Replayed: true`. Same key with a changed digest returns `409 idempotency_conflict`; a still-running original returns `409 idempotency_in_progress`. State updates also carry `expected_version`, and a stale version returns `409 version_conflict` with `current_version`.
- Money is a decimal string plus ISO currency; the contract linter forbids JSON `number` everywhere. Match scores are decimal strings too.
- `integration/api/contract.py` is a dependency-free validator for a strict subset of JSON Schema. Keywords it does not implement fail validation instead of being ignored. It also lints contract rules (tenant header, idempotency, error responses, correlation header, resolvable refs).

Alternatives and tradeoff:

- OpenAPI 3.2.1 (latest, 2026-09-10): newer, but generators and validators support 3.1 more widely today. Revisit when the API implementation framework is chosen.
- `openapi-spec-validator`/`jsonschema` packages: they give full meta-schema validation but add about six pinned transitive dependencies, and the dependency-free `validate` CI job could not run them. The subset validator covers only the keywords the contract uses and refuses anything else. It does not prove compliance with the full OpenAPI meta-schema; the implementation task should add a full validator when its framework brings one.
- Tenant derived only from the session with no header: simpler for single-tenant users, but service principals (Frappe server, workers) act across tenants and need an explicit, audited selection. The header never grants access by itself.
- IETF draft-ietf-httpapi-idempotency-key-header-08 (2026-10-22) recommends 422 for key reuse with a different payload and 409 for a concurrent in-progress request. This contract follows the repository API_STANDARD.md (409 for both conflict kinds) to keep one convention across teams. Reviewers may change this to 422 before the API is implemented; it changes one example and one error mapping.

Official sources and version/date checked (2026-11-03):

- OpenAPI Specification 3.1.1, https://spec.openapis.org/oas/v3.1.1.html (released 2024-10-24; path parameters must set `required: true`).
- OpenAPI Specification latest, https://spec.openapis.org/oas/latest.html (3.2.1, 2026-09-10).
- The Idempotency-Key HTTP Header Field, https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/ (revision 08, 2026-10-22).

Verification performed: `python3 integration/api/contract.py` validated the contract, 16 synthetic HTTP request/response pairs (10 success, including all five scenarios and all eight operations; 6 error contracts) and 2 events. `python3 -m unittest discover -s tests/contracts` ran 25 behaviour tests covering missing/malformed tenant and case IDs on every operation, missing idempotency key or expected version, float money, cross-tenant or cross-case response data, human-approval bypass and linter regressions. No API server is implemented; runtime enforcement is for the implementation, permission (TAX-029) and delivery (TAX-033) tasks.

Consequences and revisit trigger: Adapters and UIs code against these schemas, and breaking changes need a coordinated version bump. Revisit when the Vati API server framework is selected, if the team adopts the IETF 422 convention, or when a consumer needs a schema keyword the validator does not support. In that case, add the keyword with a test rather than bypassing the validator.
