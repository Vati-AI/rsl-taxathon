# ADR-022: Pre-submission reconciliation reuses the TAX-048 calculation

Status: proposed (code-owner review requested on the TAX-044 PR)
Owner and task: Kananelo, TAX-044
Date: 2026-11-05

Context and smallest prototype need:
- TAX-044 must show explained amounts, missing invoices and correction prompts **before** a VAT refund claim is sent. Before submission the portal has only what the taxpayer typed and uploaded; extraction runs later, and TAX-032 deliberately doesn't let taxpayers read extraction results.
- The TAX-048 reconciliation (`ai/tools/refund_reconciliation.py`, owned by Gupta) is deterministic and uses only the standard library (`re`, `decimal`, `typing`). It needs structured figures: the return's input and output VAT, and each invoice's net, VAT and gross amounts, with optional contracts and payments.
- The file is pinned by SHA-256 in four approved playbook entries (ADR-017). Moving or editing it would invalidate them.
- Kananelo decided: the taxpayer types in the invoice schedule; extend the TAX-031 claim records rather than duplicate them; reuse the TAX-048 logic without coupling the portal to the AI runtime.

Decision:

**Data (extends TAX-031, all optional so existing claims stay valid)**
- `Vati Refund Claim` gains `period_output_vat`, `period_input_vat` and a `Vati Refund Claim Invoice` child table:
  - required: invoice number, date, supplier, amount excluding VAT, VAT, total, and the uploaded invoice document
  - optional: supplier TIN, amount paid, contract document
- It also gains server-computed `invoice_net_total`, `invoice_vat_total` and `invoice_gross_total`.
- Rows are validated by `vati_tax.submissions.invoices` (pure, shared by the DocType and the portal):
  - net + VAT = total, compared exactly
  - dates not in the future and not after the period
  - required fields present
  - no duplicate supplier and invoice number
  - at most 200 rows
- Row documents must be the taxpayer's own, using the same rule as TAX-043. Declared figures must match a linked VAT return.
- No VAT rate or tax-law rule is assumed, because there is no authoritative RSL source for one in this repository.
- The taxpayer portal requires the figures and at least one invoice.

**Reuse without the AI runtime**
- `vati_tax.portal.presubmission` is the only Frappe module that imports from `ai`, and it imports only `ai.tools.refund_reconciliation`. The import is lazy, so the portal keeps working, and the review answers 503, if the module is absent.
- The file is used unchanged, so the playbook digests remain valid.
- In the developer stack, `docker-compose.frappe.yml` mounts `ai/` read-only at `/workspace/vati_shared/ai`, and `frappe_dev.py init` adds a `vati_shared.pth` path file to the bench environment. **No AI dependency is installed**: verified that `langgraph` is not importable in the bench while `ai.tools.refund_reconciliation` is.
- Guard tests (`portal/test_presubmission.py`) fail if the shared file imports anything outside the standard library, if any other Frappe module imports `ai`, or if the adapter calls `propose_refund_lane`.

**What the taxpayer sees** (`review_refund_claim`, which writes nothing)
- Claimed amount, VAT on the listed invoices, the amount supported by the documents given, and the rest.
- Correction prompts from a fixed allowlist of reconciliation codes, worded as guidance: missing invoices, period arithmetic, missing payment, payment different from total, and an optional contract.
- **Indicators and the advisory lane are never computed for or shown to taxpayers. Prompts never block sending.**

Alternatives and tradeoff:
- **`pip install --no-deps -e ai`** (the mechanism first proposed on issue #45): rejected after inspection. Setuptools' flat-layout discovery of `ai/` would install `tools` and `workflows` as top-level packages, not `ai.tools`. The path file gives the same "no dependencies" guarantee and keeps the `ai` package name.
- **A portal copy of the calculation:** rejected by the task owner, because two implementations would drift.
- **A Vati service endpoint** (`POST /api/v1/refund-reconciliation/preview`): the cleanest runtime boundary, but it needs an `integration/api/openapi.json` contract change and a service process that doesn't exist yet. Revisit for production.
- **Changing TAX-046/048 code or the playbook registry:** not needed.

Official sources and version/date checked:
- Frappe 16.54.0 child tables (`istable`) and `Currency` fields in the pinned bench, 2026-11-05.
- Python `site` module `.pth` path configuration (Python 3.14 bench interpreter).
- ADR-017 digest rules in `ai/workflows/playbook_registry.py`.

Verification performed:
- **Frappe-free:** `submissions/test_invoices.py` (10) and `portal/test_presubmission.py` (8, including the guard tests and the acceptance check that incomplete evidence yields guidance, not accusations); `portal/test_claims.py` extended.
- **Site-backed:** `portal/test_refund_claim_bench.py`, 18 tests (7 new). The TAX-031 `submissions/test_submission_bench.py` still passes all 13.
- **Browser:** `tests/frontend/claim.spec.ts`, including the new schedule and pre-submission check tests.

Consequences and revisit trigger:
- Officers can read the schedule on the claim.
- A production deployment must make `ai/tools/refund_reconciliation.py` importable to the Frappe app without its AI dependencies (as here), or use the service endpoint above.
- Revisit when the Vati service exists, when extracted invoice data can pre-fill the schedule, or if RSL provides authoritative rate rules.
