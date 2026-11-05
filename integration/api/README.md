# Vati integration API contract (TAX-018)

`openapi.json` is the versioned contract (`/api/v1`) for the Vati coordination boundary. It describes Vati routes only; it makes no claims about upstream Jube, Frappe, OpenAleph, yente, Docling or LangGraph endpoints. No server implements it yet.

| Operation | Route |
|---|---|
| listCases | `GET /api/v1/cases` |
| createCase | `POST /api/v1/cases` |
| getCase | `GET /api/v1/cases/{case_id}` |
| getTaxpayer | `GET /api/v1/taxpayers/{taxpayer_id}` |
| getManagementSummary | `GET /api/v1/management/summary` |
| startInvestigation | `POST /api/v1/cases/{case_id}/investigate` |
| listEvidence | `GET /api/v1/cases/{case_id}/evidence` |
| registerDocument | `POST /api/v1/cases/{case_id}/documents` |
| listEntities | `GET /api/v1/cases/{case_id}/entities` |
| listFindings | `GET /api/v1/cases/{case_id}/findings` |
| requestEvidence | `POST /api/v1/cases/{case_id}/request-evidence` |
| getCaseReview | `GET /api/v1/cases/{case_id}/review` |
| proposeDecision | `POST /api/v1/cases/{case_id}/decisions` |
| reviewDecision | `POST /api/v1/cases/{case_id}/decisions/{decision_id}/review` |

Rules every implementation must keep (see [ADR-003](../../docs/decisions/ADR-003-vati-api-contract.md)):

- `X-Vati-Tenant-ID` is required and checked against the authenticated principal's scope. Missing: `400 missing_tenant_context`. Out of scope: `403 tenant_scope_denied`. A missing or malformed `case_id` gives `400 invalid_case_id`.
- Mutations require `Idempotency-Key`. Same key with the same body replays the original response (`Idempotent-Replayed: true`); a changed body gives `422 idempotency_conflict`; a retry while the original is still running gives `409 idempotency_in_progress`; a missing key gives `400 invalid_request`. The checker enforces each error code's status. State updates send `expected_version`; a stale version gives `409 version_conflict`.
- Errors use `{"error": {code, message, retryable, correlation_id}}` and echo `X-Correlation-ID`. Upstream failures return `503` and are never replaced by fixture data.
- `service_links` maps a case, evidence item or entity to upstream IDs with sync state. The case status is a Jube projection.
- Money and scores are decimal strings. Findings separate facts, inference and evidence gaps; legal routes stay `unverified` until a reviewer cites authority; proposed actions always require human approval.
- `EventEnvelope` (in `components/schemas`) is the internal outbox event format.

`listCases`, `getTaxpayer`, `selection_reasons` and the embedded taxpayer summary were added for the auditor queue (TAX-040, [ADR-005](../../docs/decisions/ADR-005-case-queue-contract.md)). Selection reasons are routing signals, never findings.

`getCaseReview`, `proposeDecision` and `reviewDecision` were added for officer decisions, approvals and specialist referrals (TAX-042, [ADR-018](../../docs/decisions/ADR-018-officer-review-contract.md)). They project the Frappe decision records of [ADR-013](../../docs/decisions/ADR-013-decisions-approvals-evidence-requests.md). Refusals use `version_conflict` (409) or `invalid_domain_input` (422) with the reason in `field_errors[].code`, e.g. `stale_case_version`, `decision_pending`, `proposal_expired`, `case_state_changed`, `citation_required`.

`getManagementSummary` (TAX-060, [ADR-020](../../docs/decisions/ADR-020-management-summary-contract.md)) gives supervisors measured workload and assurance counts. Anyone else gets `403 forbidden`. Every ratio carries its denominator, gaps are listed in `missing_data`, and review signals are counted by kind only.

## Fixture server

`python3 scripts/dev.py serve` serves `listCases`, `getCase` and `getTaxpayer` from the synthetic `fixtures/auditor_queue.json`, and `listEvidence` and `listEntities` (TAX-041) from `fixtures/case_records.json` (`scripts/fixture_api.py`). Requests are checked with `contract.check_request`; the fixture principal sees one tenant (any other is `403 tenant_scope_denied`). Responses carry `X-Vati-Mode: fixture`. A case with no evidence or entities yet returns an empty page; an unknown case is `404`. Findings come only from `listFindings` below, and every citation in them names evidence served here with the same SHA-256. It also measures `getManagementSummary` (TAX-060) from the queue cases plus `fixtures/management.json` (closed cases, outcomes, evidence requests, findings and ledger events, the ledger read with the TAX-059 functions). Only the `supervisor` fixture officer (`X-Vati-Fixture-Role`, below) may read it; the default `auditor` gets `403 forbidden`. Mutations and any other operation return `404` rather than invented data.

It also serves `getCaseReview`, `listFindings`, `proposeDecision`, `reviewDecision` and `requestEvidence` from `fixtures/case_review.json` (`scripts/fixture_review.py`, TAX-042). These keep in-memory state for the life of the server process, so a reviewer can propose, approve and see the result; restarting resets them. They apply the same permission, separation-of-duties, stale-version, expiry, one-open-decision and idempotency rules as TAX-046. An approval records a fixture Jube result and nothing leaves the process. The fixture officer is chosen with the fixture-only `X-Vati-Fixture-Role` header (`auditor` by default, `specialist`, `supervisor`); a real server ignores it.

## Examples and checks

`examples/http/` holds synthetic request/response pairs for all five scenarios (SYN-001 to SYN-005) and the required error contracts. `examples/events/` holds event envelopes. Examples marked `"request_conformant": false` must be rejected by the request rules, with the matching error code.

```sh
python3 integration/api/contract.py               # lint contract, validate every example
python3 -m unittest discover -s tests/contracts   # behaviour tests for the rules above
```

Both run inside `python3 scripts/dev.py check` and need no third-party packages. `contract.check_request()` can be reused by the future server and adapter tests. The validator supports only the JSON Schema keywords listed in `contract.py` and rejects anything else. If you need another keyword, add support and a test first.
