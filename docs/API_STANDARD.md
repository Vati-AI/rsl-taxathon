# V1 API and delivery conventions

TAX-018 owns the executable OpenAPI contract and examples. This document fixes common choices now so teams do not invent separate conventions. OpenAPI describes Vati routes, not imagined vendor endpoints.

- Use a versioned `/api/v1` Vati boundary for new product routes; `/api/cases` remains explicitly fixture-only. Frappe native endpoints retain their native paths behind adapters.
- UUID identifiers; UTC ISO-8601 timestamps; decimal-string amounts plus ISO currency; snake_case JSON keys. Missing, null and zero have distinct documented meanings.
- JSON success: `data` plus optional `meta`; lists use `data: []`, `meta.next_cursor` and `meta.has_more`. Default limit 25, maximum 100; stable sort with an ID tie-breaker. Do not expose unbounded queries.
- JSON error: `error: {code, message, retryable, correlation_id}` with optional field errors. Use 400 malformed input, 401 missing authentication, 403 denied operation, 404 missing or concealed resource, 409 version/idempotency conflict, 422 invalid domain input, 429 rate limit and 503 unavailable dependency. Never send stack traces or sensitive upstream response bodies.
- Authorize the actor and resource on every product route. Tenant context comes from the authenticated session/service mapping; a supplied tenant_id must match permitted scope. Browser code never holds vendor API secrets.
- Mutations carry Idempotency-Key and expected_version when updating state. Reusing a key with a changed request digest returns conflict. Same-key retries cannot duplicate cases or outward actions. TAX-033 implements persistent delivery, not in-memory-only deduplication.
- Event fields: schema_version, event_id, tenant_id, case_id, correlation_id, type, occurred_at, idempotency_key, payload. Version the schema and add consumer contract tests. Additive optional fields are preferred; breaking changes require coordinated migration.
- Initial adapter defaults: 5-second connect timeout, 30-second response timeout, at most 3 total attempts with bounded exponential backoff/jitter. Retry transient transport/429/503 failures only when the operation is safe or idempotent; honor Retry-After. Extraction/AI work uses asynchronous jobs with a documented finite deadline rather than an endless HTTP request. Validate these defaults in service tasks.
- Cross-service logs propagate correlation_id. Health/readiness must distinguish running, configured and connected. Start with local structured logs; hosted tracing infrastructure is optional.

TAX-018 must validate success/error examples plus denied tenant, stale version, duplicate request and upstream timeout contracts. These conventions do not imply endpoints are already implemented.
