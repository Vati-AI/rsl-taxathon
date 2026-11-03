# Reproducible demo

Use only `data/scenarios/cases.json`; all identities are invented. These are expected outcomes, not a working investigation engine.

1. Kananelo demonstrates submission and a missing-document check.
2. Keletso shows processing stages and shared case/correlation IDs.
3. Leseli opens the auditor case with rule explanation, reconciled and unresolved amounts, and source evidence.
4. Gupta shows the investigation trace and pause at the human checkpoint.
5. An auditor records an outcome. Show the audit trail and taxpayer-safe status.

| Scenario | Required behavior |
|---|---|
| SYN-001 legitimate refund | Records reconcile; recommend clearance; human confirms. No automatic payment. |
| SYN-002 related-party risk | Unsupported fee and refund discrepancy remain explicit; request targeted evidence after officer approval. No automatic fraud label. |
| SYN-003 ambiguous service | Conflicting consultancy/employment evidence; pause and refer to specialist. No forced classification. |

Yellow owns expected outcomes, traceability and replay checks. Jane owns browser flow, empty/error/loading states, mobile layout and accessibility. Repeat a submission and a processing callback to prove idempotency; simulate one unavailable upstream and show a visible processing failure, not a fabricated result.

Record environment versions, fixture revision, duration, passed/failed assertions and limitations in `docs/demo-results.md` when the implementation exists. Reset only disposable demo data. The scaffold itself cannot yet run this product demo.
