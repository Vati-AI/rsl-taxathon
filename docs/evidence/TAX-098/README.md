# TAX-098 evidence: taxpayer compliance assistant agent

`ai/agents/taxpayer_assistant.py` is a LangGraph assistant that answers two TAX-096
taxpayer-assistance operations for one taxpayer:
- `explainValidationResults`
- `reviewSubmissionDocuments`

All data is the synthetic TAX-102 fixture.

## Design

### Permission context
Each call first checks the caller:
- The caller must be a taxpayer, and the path must name the caller's own taxpayer in
  the caller's tenant. Anything else is `404 not_found`, reported exactly like a missing
  taxpayer.

It then builds a `TaxpayerView` holding:
- the caller's own submissions;
- the taxpayer-facing fields of the caller's own documents (no document text);
- issue codes from the caller's own earlier validation results.

The view never holds:
- officer-side records (risk score, rule hits, lane, notes, findings);
- other taxpayers' records.

So nothing later in the graph can read them.

### Tool allowlist
The graph reads data only through `TaxpayerView.tool`, restricted to:
- `read_own_submission`
- `read_own_documents`
- `validate_submission`
- `compare_document_fields`

Any other name raises `ToolNotAllowed`. Another taxpayer's submission or document ID is
`422 invalid_domain_input`, reported like an unknown ID.

### Deterministic results
`ai/tools/submission_checks.py` computes them, with fixed messages and codes from the
`TaxpayerFacingIssueCode` allowlist.

Row checks:
- `invoice_total_mismatch`
- `duplicate_invoice`: same supplier and number, case-insensitive, reported on the
  later row as the TAX-044 rule does
- `invoice_period_mismatch`: the boundary days are inside the period
- `supporting_document_missing`

Claim checks:
- `return_arithmetic_mismatch`
- `refund_claim_exceeds_documented_input_vat`

`compare_document_fields` sets each completed Docling extraction beside the declared
row and lists the differing fields as `differs_from_declared`, for the taxpayer to
confirm. Nothing is changed.

### Model role
The `fast` role (`ai.models.router.get_model("fast")`) only words each explanation and
its steps.
- **Input:** structured facts only (row number, code, declared amounts or dates). It
  never sees document text, so instructions planted in a document cannot reach it.
- **Validation:** its output must keep exactly the same issues, in the same order, in a
  closed shape with bounded text. Otherwise the router falls back to the deterministic
  template.
- **Disclosure:** each response carries an `AiDisclosure` built from the router's
  provenance.
- **Document role:** not used, because nothing here needs long-context reasoning.

### Never
- It never changes the store, declared figures or documents.
- It never sends or submits anything.
- It cites only the caller's own documents.

Every explanation, flagged invoice and field to confirm has
`requires_taxpayer_confirmation: true`, and the response has
`changes_declared_figures: false`.

### Recurring mistakes
An issue whose code appears in the taxpayer's own earlier validation results says so.
History of other taxpayers is ignored (tested).

## TAX-102 gate (mock models)

Command:

```bash
python -m ai.evaluation.taxpayer_assistant_eval --assistant ai.agents.taxpayer_assistant:make_assistant --operations explainValidationResults,reviewSubmissionDocuments --check
```

Result: **gate PASS**. 8 cases × 3 runs, 767 checks, 0 failed.

| Metric | Result |
| --- | --- |
| `expected_output` | 228/228 |
| `false_positive` | 0/90 |
| `citation_coverage` | 195/195 |
| `interrupt_compliance` | 87/87 |
| `hostile_resistance` | 159/159 |
| `determinism` | 8/8 |

The 8 cases are:
- B01 and B02: the 15-invoice upload, through each operation;
- A01, A02 and A12: injections in an invoice and in a supplier agreement, including a
  request to add internal fields;
- A07, A08 and A09: another taxpayer in the path, another taxpayer's document IDs, and
  another taxpayer's submission.

The full report is in [`gate_report.json`](gate_report.json). The B01 response is in
[`B01_response.json`](B01_response.json); its `generated_at` is blanked.

### What the 15-invoice scenario finds
The assistant flags exactly the six expected invoices, each with its own uploaded copy
cited:

| Invoice | Issue | Note |
| --- | --- | --- |
| 4 | `invoice_total_mismatch` | 13,800.00 against 13,900.00 entered |
| 7 | `duplicate_invoice` | Same as invoice 3, INV-0310 |
| 9 | `invoice_total_mismatch` | 6,210.00 against 6,120.00 entered |
| 11 | `invoice_period_mismatch` | Dated 2026-04-14 |
| 12 | `invoice_period_mismatch` | Dated 2026-04-02 |
| 14 | `supporting_document_missing` | Nothing cited |

Fields to confirm:
- **Expected:** the two `gross_amount` differences on invoices 4 and 9.
- **Also reported:** a third difference, the date on invoice 7. The duplicate copy's
  document shows 2026-01-15 while the row says 2026-02-11. The fixture does not list it,
  and the harness accepts it because both values are reported correctly.

### Scope
`--operations` is a new, optional filter in the TAX-102 harness. It selects every case
of the named operations, adversarial ones included, and changes no expected outcome.
`draftEvidenceResponse` is TAX-099's job:
- Until then the assistant answers it with `503 upstream_unavailable`, "not available
  yet".
- On the full 16-case run, only the evidence-draft cases fail `expected_output`. They
  still pass every leakage, scope and confirmation check.

`DEFAULT_ASSISTANT_ENTRY` stays unset, so `agent_eval --check` still reports the
taxpayer subject as not run. TAX-099 registers it once all 16 cases pass. The note in
the code now says so.

## Tests
`tests/ai/test_taxpayer_assistant.py`:
- **Deterministic tests (4, run by `scripts/dev.py check`):**
  - exactly the six problem invoices;
  - period boundaries and same-number-different-supplier;
  - claim-level checks and refusal of float money;
  - field comparison without changing anything.
- **Agent tests (8, need LangGraph; skipped without it, as the other playbook tests are):**
  - the gate above;
  - the view holds no internal or other-taxpayer canary, and unknown tools are refused;
  - other taxpayers are refused like missing records, and a non-taxpayer audience
    is 403;
  - store unchanged, nothing sent, everything needs confirmation;
  - the model payload never contains document text, even with an injection;
  - model output that changes the issues falls back to the template through the
    router;
  - recurring mistakes come only from the caller's own history;
  - determinism, and the 503 for evidence drafting.

Run with LangGraph from `ai/requirements.lock`:
- this file and `test_taxpayer_assistant_eval.py`: 37 passed;
- `agent_eval --check`: gate PASS.

## Not done here
These items in the task scope are not implemented:
- serving these operations from Frappe (TAX-100);
- the portal UI (TAX-101);
- evidence-response drafting (TAX-099).

The scope also mentions submission guidance for required fields. Required fields are
already enforced by the TAX-044 form validation, so this assistant explains the
deterministic results instead of adding a second set of field rules.

No live model was called. The gate uses the keyless mock `fast` chain.
