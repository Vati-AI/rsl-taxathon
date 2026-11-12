# TAX-141 evidence: imported services booked as expenses

TAX-118's sealed blind test missed 4 of 8 taxpayers planted with untaxed imported
services. All four had booked their foreign management or consultancy fees to general
operating expenses. The TAX-115 three-way match only compares bank payments with
*service* bookings. For these payees it saw money out and no service cost, and raised
only a `payments_and_books_differ` question, so the imported-services gap was never
measured.

All figures are synthetic, and the rates are the synthetic assumptions TAX-115 uses.

## What changed

### Two independent descriptions of each payment
Suppose a non-resident payee receives payments but has no service or interest booking.
The match (`ai/tools/foreign_payments.py`) now checks two independent descriptions of
what the payments were for:

1. **Central Bank purpose.** This is the Central Bank cross-border report's
   balance-of-payments `purpose`, where the report exists and carries one.
   - It is now an optional `code` field on the `central_bank.cross_border_payment`
     contract.
   - A report counts when it matches a payment by country and amount, within 5 days.
   - CMA payments (South Africa and the other Common Monetary Area countries) are often
     not reported, and the match records that.
2. **Bank narration.** This is the narration in the taxpayer's own bank export. The
   pipeline now passes it through; it was already parsed.

### The question
If either description says services, the match raises
`foreign_service_payments_outside_service_accounts`. This is a **question, not a
conclusion**.

The reason gives the amounts and where they were booked, and ends with: *"Ask the
taxpayer what the payments were for before treating them as services."* The question
`explain_foreign_service_payments` cites:
- the bank payments;
- the general-expense ledger lines;
- the Central Bank reports.

### Amount
The amount is any withholding not declared on the payments, plus the imported-services
VAT on them.

If the year's declared imported-services VAT exceeds what booked services explain, the
surplus is set against this VAT first. It is recorded as
`vat_declared_beyond_booked_services`. A payment fully covered by the surplus is not
asked about.

### Explanations tested
These are recorded on the match row for every such payee, flagged or not:
- `central_bank_purpose_services`, `_other` or `_not_reported`;
- `bank_narration_describes_services` or `_not_services`;
- `cma_payment_not_in_central_bank_data`;
- `withholding_declared_for_payee`.

### Not asked
- payees marked service-exempt;
- payments whose descriptions say goods or a loan;
- imported goods (ledger lines with the `IMPORT` tax code, which customs entries evidence).

### Routes
The code is added to `CAND-IMPORTED-SERVICES-VAT` and `CAND-WHT-FOREIGN` in the
candidate rule register. Both routes stay `candidate_unverified` and never eligible for
assessment.

In the backend (`vati_tax.assurance.rules`), the finding becomes an `imported_services`
finding with `request_evidence`, and it needs human approval.

### Benchmark generator
The generator has a new opt-in parameter, `central_bank_purpose`, which writes the
purpose on Central Bank reports. With the parameter off, the population is unchanged.

The TAX-118 sealed world and answer-key hashes still reproduce from the seed:
- world `cb6c792d3712…`
- answer key `65da23c193e9…`

## Re-measurement

Command:

```bash
python -m ai.evaluation.imported_services_remeasure
```

Results are in [`remeasure.json`](remeasure.json). They cover `untaxed_imported_services`
only; the other issue types are unchanged.

**Not a blind result.** The TAX-118 test split was read before these rules were written,
so the "after" rows on seed 20261106 are a re-run, not a fresh blind test. Seed 20261112
was generated after the rules were fixed and is the better guide to false positives.

| Run | Detected | False positives | Gap computed / planted (M) |
| --- | --- | --- | --- |
| **Before**: TAX-118 sealed test, seed 20261106 (blind) | 4 / 8 | 5 / 192 | 4.35m / 10.55m |
| After: seed 20261106 test split | 8 / 8 | 5 / 192 | 10.55m / 10.55m |
| After: same, Central Bank purpose on | 8 / 8 | 5 / 192 | 10.55m / 10.55m |
| After: same, narration withheld | 4 / 8 | 5 / 192 | 4.35m / 10.55m |
| After: same, narration withheld, Central Bank purpose on | 5 / 8 | 5 / 192 | 5.68m / 10.55m |
| After: fresh seed 20261112, all 300 taxpayers | 14 / 14 | 6 / 286 | 17.30m / 17.30m |
| After: same, Central Bank purpose on | 14 / 14 | 6 / 286 | 17.30m / 17.30m |
| After: same, narration withheld | 10 / 14 | 6 / 286 | 11.03m / 17.30m |
| After: same, narration withheld, Central Bank purpose on | 12 / 14 | 6 / 286 | 14.11m / 17.30m |

### What the differences mean
1. **Detection.** All four expenses-booked misses on the sealed split are now found, and
   so are all four on the fresh seed. Two of the sealed misses had declared withholding
   but no imported-services VAT, so the question measures the VAT that was not declared,
   not only the withholding.
2. **False positives are unchanged.**
   - On both seeds, the new question was raised only for taxpayers with the planted issue
     (`new_question_raised.without_planted_issue` is 0 in every run).
   - The false positives that remain are the known TAX-118 cause: withholding declared
     under a different payee reference. That is out of scope here.
3. **The narration does most of the work.** In this synthetic population the bank
   narration says "Management services" or "Consulting", so the Central Bank purpose adds
   nothing while the narration is present.
4. **The Central Bank purpose has limits.** With the narration withheld, the purpose
   recovers only the non-CMA payees (1 of 4 and 2 of 4). Payments to South African
   parents are usually not in Central Bank data, so a CMA payee with a vague narration
   is still missed. That limitation is recorded rather than worked around.
5. **The synthetic population has limits.** It plants no decoys that would look like
   this pattern, such as foreign goods suppliers booked to expenses with a service-like
   narration. False-positive behaviour for those cases is covered by unit tests, not by
   the benchmark.

## Tests
- `tests/ai/test_imported_services_disguise.py` (run by `scripts/dev.py check`) covers:
  - a narration-only question for a CMA payee;
  - a question from the Central Bank purpose alone;
  - no question when nothing describes services (purpose goods, absent, or not matching);
  - goods, exempt payees and `IMPORT` lines;
  - service bookings keep the existing match;
  - declared withholding and VAT surplus reduce or remove the question;
  - neutral wording and unverified routes;
  - the contract's optional `purpose`.
- `backend/vati_tax/vati_tax/assurance/test_rules.py` checks that the new finding becomes
  a valid `imported_services` evidence request.
- `tests/ai/test_assurance_*.py` (including the live pipeline and the TAX-118 `--check`
  self-test) still pass.
