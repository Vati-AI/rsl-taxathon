# ADR-005: Case queue, selection reasons and taxpayer context in the Vati API

Status: proposed (TAX-040; contract owner Keletso to review)
Owner and task: Leseli (auditor-frontend), TAX-040. Contract changes made with the agreement of Keletso, owner of the TAX-018 contract.
Date: 2026-11-03

Context and smallest prototype need: TAX-040 needs an officer's assigned case queue with selection reasons, taxpayer context and processing/error state. The TAX-018 contract (ADR-003) had only `getCase` by ID: no way to list cases, no field explaining why a case was selected, and only a `taxpayer_id`. A queue could not be built without guessing routes.

Decision:

- `listCases` (`GET /api/v1/cases`) with optional `assigned_to=me|unassigned`, `status`, `refund_lane`, `limit` and `cursor`. `me` is resolved from the authenticated principal, never from client input. Filters only narrow the set the principal may already see (TAX-029 enforces visibility). An empty page is `200`, not an error.
- `Case` gains two required fields: `selection_reasons` (array of `{code, summary, source}`, at most 10, empty until risk evaluation runs) and `taxpayer` (`{taxpayer_id, display_name, taxpayer_type}`, matching the case's `taxpayer_id`). Selection reasons are documented as routing signals, never findings, assessments or decisions. Embedding a summary avoids one request per queue row.
- `getTaxpayer` (`GET /api/v1/taxpayers/{taxpayer_id}`): read-only projection of the Frappe taxpayer record (Frappe stays the authority) with a masked TIN, registration date, sector and the open cases the principal may see. It returns `404` when the principal can see none of the taxpayer's cases, so existence is not disclosed. No documents or unmasked identifiers.
- Every new operation follows ADR-003: required `X-Vati-Tenant-ID`, `ErrorResponse` on all errors, echoed `X-Correlation-ID`, decimal-string money, synthetic examples checked by `contract.py`.
- `scripts/fixture_api.py` serves these three operations for UI development from `integration/api/fixtures/auditor_queue.json`, reusing `contract.check_request`, and marks responses `X-Vati-Mode: fixture`. Other `/api/v1` operations return `404` with "This operation has no fixture data." rather than invented data.

Alternatives and tradeoff:

- A separate `CaseQueueItem` schema: lighter list payloads, but two case shapes to keep in sync. One `Case` shape was chosen; revisit if payload size matters.
- Taxpayer context only through `getTaxpayer`: no duplication, but N+1 requests for a queue. The embedded summary is three fields.
- `assigned_to=<officer uuid>`: useful for supervisors, but needs role rules that TAX-029 has not defined. Deferred.
- Adding required fields to `Case` is a breaking change. No server or adapter implements the contract yet (integration/api/README.md), so the version stays `1.0.0-draft`; the three affected examples were updated in the same change.

Official sources and version/date checked (2026-11-03): OpenAPI Specification 3.1.1 (https://spec.openapis.org/oas/v3.1.1.html) for query and path parameter rules, as used by ADR-003.

Verification performed: `python3 integration/api/contract.py` validates the contract with 20 HTTP examples (3 new: assigned queue, taxpayer profile, empty queue) and 2 events. `tests/contracts/test_fixture_api.py` (16 tests) checks that every fixture case validates against `Case`, that `assigned_to=me` excludes other officers' and unassigned cases, empty pages, cursor paging, rejected query values, `400 missing_tenant_context`, `403 tenant_scope_denied` without data, `404` for unknown or undisclosed records, and that fixture responses conform to the operation schemas.

Consequences and revisit trigger: TAX-034 (Jube adapter) and the future Vati API must populate `selection_reasons` from approved risk outputs and the taxpayer summary from Frappe. Revisit when TAX-029 defines supervisor visibility, or if consumers need a lighter list shape.
