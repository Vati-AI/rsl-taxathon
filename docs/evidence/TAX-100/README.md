# TAX-100 evidence: taxpayer-scoped assistant service in Frappe

The Frappe backend now serves the five taxpayer-assistance operations defined by
TAX-096, through the `/api/v1` route:

| Operation | What it does |
| --- | --- |
| `explainValidationResults` | Explains the deterministic validation of the taxpayer's own draft claim |
| `reviewSubmissionDocuments` | Reviews the claim's documents against what was declared |
| `draftEvidenceResponse` | Drafts an answer to an open evidence request and stores it |
| `getEvidenceResponseDraft` | Returns one of the taxpayer's own stored drafts |
| `confirmEvidenceResponseDraft` | Sends a confirmed draft through TAX-045 |

All data in the tests is synthetic.

## Design

### Service core
`backend/vati_tax/vati_tax/assistance/service.py` is Frappe-free. It works through
ports, so tests can supply fakes.

### Permission context
- **Who may call:** only a caller whose sole role in the header tenant is `taxpayer`.
  Staff, services and mixed-role callers get 403, recorded in the ledger as denied.
- **Which taxpayer:** only the one named in the path, through the caller's own
  membership. Any other taxpayer is 404, the same as a missing one.
- **Policy:** the new TAX-029 action `assistance.use` is taxpayer-only and
  taxpayer-owned.

### What the assistant sees
The `records` port (`assistance/store.py`) reads only the taxpayer's own records:
- submissions, with their refund claim and invoice rows;
- documents, with their latest extraction status and extracted fields;
- evidence requests, in their taxpayer-facing columns.

Each query filters by tenant and taxpayer and selects named columns. Officer-side
DocTypes are never queried. The service filters the port's output again, so a careless
port still cannot widen the view (tested).

### Checks on every assistant answer
Before an answer is sent, the service checks:
- the declared success status;
- the closed contract schema;
- no field name the taxpayer contract forbids;
- the caller's own tenant, taxpayer, submission and request IDs;
- that the taxpayer's records did not change while the assistant answered.

An answer that fails becomes `503 upstream_unavailable` and is reported; its content
is never sent. An assistant that raises is also 503, and nothing it said is echoed.
With no runtime configured the answer is 503, never invented data.

### Drafts
Drafts are stored in a new DocType, `Vati Assistance Draft`:
- Each draft keeps its taxpayer-facing body and its `AiDisclosure`.
- Only System Manager can read it in Desk. Taxpayers reach their own drafts only
  through the service.
- Creation is idempotent per Idempotency-Key; a changed body is
  `idempotency_conflict`.
- A request that has since closed shows the draft as `request_closed`, and the draft
  cannot then be confirmed.

### Confirmation
Confirmation is the only step that sends anything. It is separate, audited and
idempotent:
- It needs `expected_version` and `declaration: true`.
- Replaying the same key and body returns the stored result. The same key with a
  different body is `idempotency_conflict`. A stale version, or confirming again under
  another key, is `409 version_conflict`.
- The answers go through the unchanged TAX-045 `responses.store.respond`, so the usual
  rules apply: the caller's own request and evidence, no reuse across requests,
  idempotency, the request moving to `responded`, and the ledger entry.
- TAX-045 refusals come back as 422, on the contract's 1-based item.
- Unconfirmed drafts never reach TAX-045 (tested). Assistance never changes a
  submission: the explain and review operations store nothing.

### Ledger
TAX-059 entries use a new `assistance` subject type, for answers, saved drafts,
confirmations, refusals and denials. Denials and refusals are committed at once, as
`audit_store.guarded` does, because the route rolls back a refused request's other
writes.

### Runtime
`assistance/runtime.py` is the only module in the package that reaches `ai`; it was
added to the boundary test's adapter list. It loads the `vati_taxpayer_assistant` hook,
which defaults to the TAX-098 assistant. If that cannot be imported it returns nothing,
and the AI operations answer 503.

Until TAX-099, `draftEvidenceResponse` answers 503 with the real runtime, because the
TAX-098 assistant does not draft yet.

## Tests

### Frappe-free: `assistance/test_service.py`
17 tests, run by `scripts/dev.py check`:
- **Permission context:**
  - the taxpayer is served for its own editable submission;
  - staff, services and mixed roles get 403;
  - another or unknown taxpayer is 404;
  - a foreign submission is 422 `not_found`;
  - a sent submission is 422 `submission_not_editable`;
  - the assistant's store holds no internal or other-taxpayer canary, even when the
    records port returns everything.
- **Answer checks:**
  - no runtime gives 503;
  - each of these is refused with 503 and reported: a forbidden field, another tenant,
    changed records, an unexpected status;
  - a raising assistant gives 503 and is not echoed.
- **Drafts and confirmation:**
  - the draft is stored and nothing is sent;
  - draft creation is idempotent;
  - a closed request cannot be drafted or confirmed;
  - confirmation sends once through TAX-045 (0-based items, a valid derived key);
  - replay, conflicts and 409 behave as described above;
  - a stale version or missing declaration sends nothing;
  - TAX-045 refusals map to 1-based item fields;
  - another taxpayer's draft is 404.
- **The TAX-102 gate, run through the service:**
  - With the harness's reference runtime: all **16 cases pass**, including drafts and
    every refusal.
  - With the real TAX-098 assistant (needs LangGraph): its **8 cases pass**.
  - This found and fixed a contract bug: `field_errors` entries may carry only `field`
    and `code`.

### Bench: `assistance/test_assistance_bench.py`
Three tests use the TAX-045 fixtures and a stand-in runtime:
- draft then confirm enters TAX-045 once; the request ends `responded`; replay is
  idempotent; ledger entries are present;
- only the caller's own records are reachable (404, 422, staff 403);
- taxpayers have no direct DocType access.

**Not run.** Docker is not available in this session, so there is no Frappe site. They
need `scripts/frappe_dev.py verify`, which also installs the new DocType and the
audit-event option.

## Checks run
- `python3 scripts/dev.py check` passes, including the boundary test with the new
  adapter.
- `ruff check` and `mypy` pass (346 files).
- `integration/api/contract.py` and `tests/contracts` pass.
- `test_service.py` with LangGraph passes, 17 of 17.

## Not done here
- **TAX-099:** drafting evidence responses.
- **TAX-101:** the portal UI.
- **TAX-039 submissions:** confirmed submissions entering the TAX-039 flow does not
  arise here, because no assistance operation edits or sends a submission. The contract
  has no submission-confirmation operation, and explain and review store nothing.
