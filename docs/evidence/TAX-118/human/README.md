# TAX-118 human checks (synthetic data only)

## Manual baseline (`manual_baseline/`)

Twenty test-split taxpayers (ten with planted issues, ten clean, in mixed order). Each
`CASE-NN` folder holds the raw records an officer would assemble by hand: refund claims and
their invoice schedules, IDMS invoices, ASYCUDA entries, e-Tax returns, withholding lines,
the taxpayer's ledger and the foreign bank file. The answer key is **not** included.

Ask an accountant to work each case in a spreadsheet as RSL would today, and fill in
`timing_sheet.csv`: start and end time, issues found, the lane they would give each claim,
and the amount they would question. Do not show them the system output first.

Each case covers 24 months. If time is short, work the three most recent refund claims
plus the foreign payments and withholding lines, and write in `notes` exactly what was
covered, so the comparison with the system is like for like.

## Outside reviewer (`reviewer_packs/`)

Twenty system case packs for the same taxpayers. Ask an outside accountant or tax
practitioner to score each pack in `scoring_form.csv` on clarity (1-5), correctness (1-5)
and whether they would act on it. They should not see the answer key.

Send both sheets back; TAX-118 results will add them as human-measured numbers.
