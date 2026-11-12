# ADR-051: Check before you file reads RSL's data and gives guidance only

Status: accepted
Owner and task: Gupta, TAX-129 (implements ADR-043 §3)
Date: 2026-11-12

Context and smallest prototype need:
ADR-043 retired Vati's claim form: taxpayers file refund claims in RSL's Online Refunds
Application. The TAX-044/048 pre-submission checks were worth keeping, but they worked on
figures the taxpayer typed into Vati's form. ADR-043 §3 keeps them as a service that reads
Lekuka and e-Tax data, shows reconciliation issues and sends the taxpayer to RSL to file.
RSL's own front door may call the same service through WSO2.

Decision:

1. **Inputs are RSL's data, not typed figures.** For one taxpayer and VAT period,
   `vati_tax.prefiling` reads through the site's RSL source (the TAX-111 fixture until RSL
   supplies an interface):
   - the latest VAT return filed in e-Tax for the period;
   - Lekuka purchase invoices where the taxpayer is the buyer, issued in the period;
   - ASYCUDA customs entries where the taxpayer is the importer, registered in the period.

   The taxpayer only chooses the period.
2. **Guidance, from fixed wording**:
   - `return_arithmetic_mismatch`, from the TAX-048 reconciliation, run through the existing
     `portal.presubmission` adapter (still the only portal module that reaches into `ai/`).
     TAX-048's other amounts need payment and contract evidence that RSL does not hold, so
     they are not used;
   - `input_vat_above_rsl_records`: input VAT in the return above the VAT on the Lekuka
     invoices and ASYCUDA entries, with the difference, and a note that invoices from
     suppliers not yet on Lekuka are uploaded when filing;
   - `supplier_not_declared`: Lekuka invoices the supplier has not declared yet;
   - `no_vat_return` and `source_not_checked` when something could not be read.
3. **Never files, blocks or scores.** The result has a status (`no_issues_found`,
   `check_these`, `incomplete`), the figures RSL holds, the prompts and a note. There is no
   lane, score, pass or fail, or eligibility. The portal page has no submit action. Its
   only action is a link to RSL's Online Refunds Application (site key `vati_rsl_links`,
   TAX-125). A source that does not answer makes the result `incomplete`, never
   `no_issues_found`. Nothing is stored.
4. **Two callers, the same result**:
   - the taxpayer portal: `portal.api.check_before_filing`, for the caller's own taxpayer,
     shown on the new *Check before you file* page (`/taxpayer/check`);
   - RSL's front door through WSO2: `prefiling.api.check_before_filing(tin, period)`, for a
     Vati service identity only (`X-Vati-Tenant-ID`). Staff use the auditor app instead.

Alternatives and tradeoff:

- Keep the typed form as the input: rejected by ADR-043. It asks for data RSL already holds.
- Use the TAX-112 line match: deferred. It needs a filed claim's lines; before filing there
  is only the return and RSL's records.
- Use every TAX-048 amount: rejected. Without payment or contract evidence they would report
  every invoice as unsupported and mislead the taxpayer.

Official sources and version/date checked:
None from RSL; e-Tax, Lekuka and ASYCUDA shapes are the TAX-111 assumptions (ADR-040,
docs/reference/RSL_LANDSCAPE.md, unverified).

Verification performed (or named implementation task):
- `prefiling/test_rules.py` (Frappe-free) covers:
  - every filed period of the TAX-118 synthetic taxpayers, read through the TAX-111 adapter
    and reconciled with the real TAX-048 module: figures equal RSL's records, the result is
    guidance only, and each taxpayer with fake invoices gets guidance;
  - unit cases for each prompt, missing sources and returns, period selection and
    validation.
- `prefiling/test_prefiling_bench.py` on a disposable site covers:
  - portal guidance from the RSL fixture, with nothing stored;
  - each taxpayer reading only its own records;
  - an unavailable source reported as incomplete;
  - a future period refused and a missing return reported;
  - the service-identity front door, and staff and taxpayers refused there.
- `tests/frontend/prefiling.spec.ts` at 1440/1024/390 px covers the figures, the guidance,
  the link to RSL, that only the period is sent, the absence of a file action and of
  decision words, the error states, sidebar navigation, keyboard use, and axe and reflow.
  Screenshots are in docs/evidence/TAX-129.

Consequences and revisit trigger:
- The TAX-125 simulator's pre-submission review stays for fixtures and the demo.
- Revisit when RSL publishes e-Tax and Lekuka read APIs through WSO2, or wants the check
  embedded in the Online Refunds Application itself.
