# Vati integration API contract (TAX-018)

`openapi.json` is the versioned contract (`/api/v1`) for the Vati coordination boundary. It describes Vati routes only; it makes no claims about upstream Jube, Frappe, OpenAleph, yente, Docling or LangGraph endpoints. No server implements it yet.

| Operation | Route |
|---|---|
| createCase | `POST /api/v1/cases` |
| getCase | `GET /api/v1/cases/{case_id}` |
| startInvestigation | `POST /api/v1/cases/{case_id}/investigate` |
| listEvidence | `GET /api/v1/cases/{case_id}/evidence` |
| registerDocument | `POST /api/v1/cases/{case_id}/documents` |
| listEntities | `GET /api/v1/cases/{case_id}/entities` |
| listFindings | `GET /api/v1/cases/{case_id}/findings` |
| requestEvidence | `POST /api/v1/cases/{case_id}/request-evidence` |

Rules every implementation must keep (see [ADR-003](../../docs/decisions/ADR-003-vati-api-contract.md)):

- `X-Vati-Tenant-ID` is required and checked against the authenticated principal's scope. Missing: `400 missing_tenant_context`. Out of scope: `403 tenant_scope_denied`. A missing or malformed `case_id` gives `400 invalid_case_id`.
- Mutations require `Idempotency-Key`. Same key with the same body replays the original response (`Idempotent-Replayed: true`); a changed body gives `409 idempotency_conflict`. State updates send `expected_version`; a stale version gives `409 version_conflict`.
- Errors use `{"error": {code, message, retryable, correlation_id}}` and echo `X-Correlation-ID`. Upstream failures return `503` and are never replaced by fixture data.
- `service_links` maps a case, evidence item or entity to upstream IDs with sync state. The case status is a Jube projection.
- Money and scores are decimal strings. Findings separate facts, inference and evidence gaps; legal routes stay `unverified` until a reviewer cites authority; proposed actions always require human approval.
- `EventEnvelope` (in `components/schemas`) is the internal outbox event format.

## Examples and checks

`examples/http/` holds synthetic request/response pairs for all five scenarios (SYN-001 to SYN-005) and the required error contracts. `examples/events/` holds event envelopes. Examples marked `"request_conformant": false` must be rejected by the request rules, with the matching error code.

```sh
python3 integration/api/contract.py               # lint contract, validate every example
python3 -m unittest discover -s tests/contracts   # behaviour tests for the rules above
```

Both run inside `python3 scripts/dev.py check` and need no third-party packages. `contract.check_request()` can be reused by the future server and adapter tests. The validator supports only the JSON Schema keywords listed in `contract.py` and rejects anything else. If you need another keyword, add support and a test first.
