# API and fixture contract

Production route names below are proposed Vati routes to implement in TAX-018. They are not claims about upstream APIs.

- POST /cases; GET /cases/{case_id}; POST /cases/{case_id}/investigate
- GET /cases/{case_id}/evidence, /entities, /findings
- POST /cases/{case_id}/documents; POST /cases/{case_id}/request-evidence
- Frappe portal submissions and responses map to these services through authenticated server calls.

Every request resolves tenant/user permissions; case IDs alone never authorize access. Every mutation carries idempotency key and expected version. Event envelope: schema_version, event_id, tenant_id, case_id, correlation_id, type, occurred_at, idempotency_key, payload. Monetary values are decimal strings with currency.

Findings distinguish facts, inference, missing/unverified/contradictory evidence, legal-route verification and proposed action. Evidence references include source record/document SHA-256 and page/record locator. Errors identify service, retryable code and correlation ID without leaking document content or credentials.

## Executable fixture interface

`python3 scripts/dev.py serve` exposes GET /api/health, /api/scenarios and /api/cases on localhost:58080. Each returns `mode: fixture` and `synthetic: true`. GET /api/cases/{id} retrieves a fixture or 404. It has no mutations, authentication or live-service fallback. Use it for UI development only. `/auditor/` and `/taxpayer/` show starter interfaces; they are not completed product flows.

The same server also serves the contract-shaped `listCases`, `getCase` and `getTaxpayer` operations (TAX-040) and `listEvidence` and `listEntities` (TAX-041) under `/api/v1` from synthetic fixtures, applying the contract's tenant and request rules (see integration/api/README.md). The auditor app uses these. The officer review operations (`getCaseReview`, `listFindings`, `proposeDecision`, `reviewDecision`, `requestEvidence`; TAX-042) are served too and accept POST; their changes are in-memory fixture state only, reset when the server restarts. `listFindings` is the one source of findings; its citations name evidence served by `listEvidence`. The supervisor-only `getManagementSummary` (TAX-060, ADR-020) is measured from synthetic records.

The versioned contract is [integration/api/openapi.json](../integration/api/openapi.json) with synthetic examples and a dependency-free checker; see [integration/api/README.md](../integration/api/README.md) and [ADR-003](decisions/ADR-003-vati-api-contract.md). Real adapter contracts follow in the service tasks. Tests must verify denied access, duplicate mutations, stale state, missing evidence and upstream timeout in addition to success.
