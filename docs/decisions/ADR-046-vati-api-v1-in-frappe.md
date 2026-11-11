# ADR-046: The Vati /api/v1 service in Frappe for the auditor app

Status: accepted
Owner and task: Gupta, TAX-090
Date: 2026-11-11

Context and smallest prototype need:
The auditor app calls the `/api/v1` contract (`integration/api/openapi.json`, ADR-003). Until now
only the fixture server (`scripts/dev.py serve`) answered those calls, from synthetic files. The
Frappe app already holds the records behind every call: case identities and service links
(TAX-030), the Jube status projection (TAX-039), evidence (TAX-032), FtM entities indexed in
OpenAleph and yente screening results (TAX-087), findings and finding feedback (TAX-086),
decisions, evidence requests and the review trail (TAX-046), the audit ledger (TAX-059), the
integrity review (TAX-077) and lane proposals (TAX-121). The missing link was a service that
serves the contract from those records, with sign-in, tenant scope and roles enforced on the
server.

Decision:

1. **One whitelisted method serves the contract path.** Frappe v16.54.0 routes every
   `/api/v1/*` URL to its own `method` and `resource` endpoints and gives apps no hook to add
   routes there. So `vati_tax.api_v1.api.route` takes the contract path in `path`:
   `GET /api/method/vati_tax.api_v1.api.route?path=/api/v1/cases&status=AWAITING_HUMAN`. It
   returns the exact contract response (status, headers, JSON body), not Frappe's
   `{"message": ...}` wrapper. A reverse proxy (TAX-091, TAX-117) or the app's API client can map
   `/api/v1/...` onto it one to one. The task criterion allows these "whitelisted equivalents with
   the same contract shapes".
2. **The router is Frappe-free and reuses the contract's own rules.** `api_v1/core.py` matches the
   operation and checks the tenant header, IDs, query, Idempotency-Key and body with
   `contract.match_operation` and `contract.check_request`. The fixture server uses the same
   checks, so both reject the same input with the same code. Errors use the ADR-003 envelope and
   echo `X-Correlation-ID`. Unexpected failures become `503 upstream_unavailable` and are logged
   by kind only, never echoed. A success body that names another tenant, case or taxpayer than the
   request is never sent (`503`, logged as `scope_mismatch`). Responses carry `X-Vati-Mode: live`.
3. **Sign-in and roles.** Sign-in is Frappe's session (or API key). Session POSTs need Frappe's CSRF
   token like every other session POST. Guests get `401 unauthenticated`. The tenant comes from
   `X-Vati-Tenant-ID` checked against the user's memberships (TAX-029). Reads use the TAX-029
   matrix: `case.read` for the queue, cases, taxpayers, entities and briefings, and `ledger.read`
   (supervisors) for the management summary. Refusals are kept in the TAX-059 ledger. Mutations
   go through the existing domain stores, which keep their own authorisation, separation of
   duties, idempotency and stale-version rules. The fixture-only `X-Vati-Fixture-Role` header is
   ignored. A refused or failed request rolls back anything it wrote. Refusals the ledger must
   keep are committed where they are recorded.
4. **Served operations** (`api_v1/operations.py`): `listCases`, `getCase`, `getTaxpayer`,
   `listEvidence`, `listEntities`, `listFindings`, `recordFindingFeedback`, `getCaseReview`,
   `proposeDecision`, `reviewDecision`, `requestEvidence`, `getCaseBriefing`,
   `getManagementSummary` and `getIntegrityReview`. These are the auditor app's calls. Other
   contract operations return `404 not_found` instead of invented data:
   - `createCase` and `registerDocument`: cases come from sent submissions (TAX-039), and
     documents come through the portal and pipeline.
   - `startInvestigation`: TAX-088.
   - Taxpayer assistance: TAX-100.
5. **Where each field comes from** (`api_v1/backend.py`, pure projections in
   `api_v1/projections.py`):
   - Case `status` is the `Vati Case Sync` projection of Jube. A case Jube has not reported on
     yet is `RECEIVED`, and its Jube service link shows whether Jube has acknowledged it.
   - `version` is the decision trail's case version (TAX-046).
   - `refund_claim` comes from `Vati Refund Claim`, or else the latest lane proposal's claimed
     amount.
   - `refund_lane` comes from the latest `Vati Lane Proposal`. The TAX-114 lane `investigation`
     is the contract's `targeted`, the third of the TAX-048 lanes.
   - Entities are the TAX-087 FtM mapping of the case, limited to the entities OpenAleph has
     indexed and the schemata the contract lists (documents are evidence). Each entity carries
     its latest yente candidates.
   - The management summary and the fixture server now share one Frappe-free rule module
     (`api_v1/management.py`).
6. **Identifier mappings the contract needs.**
   - **Officer IDs.** Officers are contract UUIDs, and Frappe users are logins (ADR-013 left
     this mapping to the API layer). Each login maps to `uuid5(5a7e0090-…-0000000000a1,
     lower(login))`: stable, identical in every tenant, and never reversed by the API.
   - **Entity IDs.** FtM entity IDs are SHA-256 hex, and the contract wants UUIDs. The entity ID
     is `uuid5(5a7e0090-…-0000000000e1, tenant + FtM ID)`. The FtM ID stays visible as the
     entity's OpenAleph service link.
7. **No invented values.** Frappe holds no copy of Jube's officer assignment, so
   `assigned_officer_id` is null and `assigned_to=me` returns no cases. The management summary
   lists this under `missing_data`. Frappe holds no RSL selection reasons, so
   `selection_reasons` is empty. A case without a taxpayer or submission (none today) cannot be
   represented by the contract `Case` and is left out of the queue. No briefing store exists
   until TAX-088. `getCaseBriefing` asks the readers registered in the `vati_briefing_reader`
   hook, and with none registered every case returns `404`, as the contract specifies for a case
   without a briefing.

Alternatives and tradeoff:

- A `before_request` hook that answers `/api/v1/cases...` directly: rejected. It runs before
  Frappe's API-key authentication. It can only answer by raising, which rolls back the request's
  writes, and it depends on Frappe internals.
- One whitelisted method per operation: possible, but the client would need a second routing
  table. One method keyed by the contract path keeps the contract as the only routing table.
- A separate API server next to Frappe: rejected for the pilot. Frappe already owns sign-in,
  roles, CSRF and the records. A second server would need its own session handling.
- Offset cursors (as in the fixture server): replaced by opaque keyset cursors over a stable sort
  with an ID tie-breaker (API_STANDARD.md), so a page boundary does not move when cases are added.

Official sources and version/date checked:
Frappe v16.54.0 (commit `2fcb9397`, `integration/frappe-framework.lock.json`):
`frappe/api/__init__.py` (`API_URL_MAP` mounts `v1` and `v2` rules under `/api`, `/api/v1` and
`/api/v2`, with no app hook) and `frappe/app.py` (`before_request` hooks run inside `init_request`,
before `validate_auth`). Both were read on 2026-11-11 from github.com/frappe/frappe at tag
v16.54.0.

Verification performed (or named implementation task):
- `backend/vati_tax/vati_tax/api_v1/test_core.py` (Frappe-free, in `scripts/dev.py check`) runs
  all 30 contract examples for the served operations through the router. Each success body,
  request-rule refusal and handler error must pass `contract.check_example` with the example's
  status and code. The tests also cover guest access, unserved and unknown routes, invalid JSON,
  correlation IDs, unhandled errors, the cross-tenant guard, keyset paging and every projection
  against its contract schema.
- `test_api_v1_bench.py` runs every served operation on a disposable site through the
  whitelisted method. It covers each role and tenant isolation, a proposal with replay and key
  reuse, stale-version and separation-of-duties refusals, approval to the fake Jube, evidence,
  findings, feedback, evidence requests, the supervisor summary and integrity review, and ledger
  refusals. Every response is checked with `contract.check_example`.
- The fixture contract tests (`tests/contracts`) pass unchanged with the shared summary module.

Consequences and revisit trigger:
- TAX-091 hosts the auditor app in Frappe. Its API client (or the proxy in front of Frappe) maps
  `/api/v1/...` to `route?path=...` and sends the CSRF token on POST.
- TAX-088 registers its briefing reader in `vati_briefing_reader`.
- TAX-123 and TAX-131 add RSL selection reasons and referral cases. A referral case without a
  Vati submission needs `Case.submission_id` to become nullable first, through a contract change.
- Revisit officer IDs if officers get a stored UUID, and `assigned_officer_id` once Jube
  assignment is mirrored.
- Revisit the routing if Frappe adds an app hook for `/api/v1` routes.
