# ADR-019: Taxpayer responses to evidence requests

Status: proposed
Owner and task: Kananelo, TAX-045
Date: 2026-11-04

Context and smallest prototype need: TAX-046 records officer-authorised evidence requests in `Vati Evidence Request`. Taxpayers have no DocType permission on it, and it holds internal fields: case ID, finding IDs, authoriser, case version and keys. ADR-013 leaves sending and responses to TAX-045 (`recorded` -> `sent` -> `responded`). The taxpayer portal must let a taxpayer answer the exact items an officer asked for. It must show only approved taxpayer-facing statuses, link each attachment to the right request, and never expose internal notes or case data.

Decision:
- **What the taxpayer sees.** The portal reads requests only through `vati_tax.responses.store`, which filters by the caller's own (tenant, taxpayer) memberships. It selects only the columns of the taxpayer view, and `rules.taxpayer_view` builds the view from an allowlist: request ID, status, due date, the officer's `message_to_taxpayer` and the requested items.
  - Internal statuses map to taxpayer statuses: `recorded`/`sent` -> `awaiting_response`, `responded` -> `response_sent`, `withdrawn` -> `no_longer_needed`. Any other status is not shown.
  - A request outside the caller's scope is reported as not found. The DocType permissions of `Vati Evidence Request` are unchanged.
- **What a response is.** A response is one immutable `Vati Evidence Response` per request (new DocType, read-only for every role, written only by the store).
  - It answers every requested item exactly once, by `item_index`, with at least one document or a reason it cannot be provided.
  - Every attached evidence must be the caller's own: missing and foreign IDs are refused identically.
  - Each document is attached to one item and one request only. Evidence already attached to another request's response is refused.
  - The response holds no case ID, so a taxpayer reading it cannot learn the internal case linkage.
- **What happens on send.** In one transaction, the request moves `recorded` -> `sent` -> `responded` using the transitions TAX-046's controller already allows, and a `write` entry `evidence_request.responded` is appended to the TAX-059 ledger.
- **Retries.** Idempotent per taxpayer: the same key with the same answers replays the stored response; changed answers return `idempotency_conflict`. A second response to an answered request is `already_responded`; a withdrawn request is `request_withdrawn`.

Alternatives and tradeoff:
- **Grant taxpayers read access on `Vati Evidence Request`.** Rejected: it would expose internal columns through `/api/resource` and change TAX-046's permission model.
- **Attach evidence with `reference_doctype = Vati Evidence Request`.** Not possible: TAX-032 requires the caller to be able to read the referenced record. The response holds the request-to-item-to-evidence link instead.
- **Mark a request `sent` when it is listed.** Rejected: a read would change state. `sent` is recorded when the taxpayer responds. A delivery acknowledgement or an outbox notification can set it earlier later on.

Official sources and version/date checked: Frappe 16.54.0 DocType permissions and `has_permission` hooks, as used by TAX-029 (ADR-007), checked in the pinned bench on 2026-11-04. Idempotency semantics follow docs/API_STANDARD.md.

Verification performed: `vati_tax/responses/test_rules.py` (12 Frappe-free tests). `vati_tax/responses/test_responses_bench.py` (9 site-backed tests) covers:
- a structured response with audit
- the status and field allowlist
- no direct DocType access
- replay and conflict
- answered and withdrawn requests
- foreign, cross-tenant and reused evidence
- unknown items
- concealment of other scopes
- staff refusal and immutability

Browser tests: `tests/frontend/responses.spec.ts` at 1440/1024/390 px.

Consequences and revisit trigger: officers can read responses (read permission) and see `responded` on the request. An officer-facing response view belongs to the auditor workspace. Revisit if requests need several rounds of responses, partial answers, delivery receipts or notifications.
