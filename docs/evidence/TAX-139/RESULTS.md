# TAX-139 results: persistent VAT credit positions

**Everything here is system output on synthetic data**, scored automatically against a hidden answer key. Thresholds are prototype assumptions, not RSL policy.

- Population: 300 synthetic taxpayers, seed 20261111, with 24 planted persistent-credit taxpayers and 24 capital-spending look-alikes ({'persistent_credit': {'planted': 24, 'capex_lookalikes': 24}}).
- Development split 100, test split 200 (run once, reported here).
- Sealed 2026-11-11T18:28:26+00:00: dataset `3e5c4c4f7ab0a86c...`, answer key `f56551eb55a8a1a4...`.
- Rules frozen 2026-11-11T18:28:36+00:00: fingerprint `75847baf190c2531...`; test run 2026-11-11T18:28:43+00:00.
- Regenerating from the seed reproduces the sealed data: True.

## Flagged for an unexplained persistent credit (test split)

Rows other than the planted group are false positives.

| Group | System (explanations first) | Naive rule: credit in half the periods |
|---|---|---|
| Exporters (zero-rated share at or above 30%) | 0.0% (0/92) | 100.0% (92/92) |
| Capital-spending look-alikes | 0.0% (0/15) | 100.0% (15/15) |
| Other clean taxpayers | 0.0% (0/44) | 45.5% (20/44) |
| Taxpayers with another planted issue | 0.0% (0/33) | 87.9% (29/33) |
| **Planted persistent credit** | 100.0% (16/16) | 100.0% (16/16) |

## Ablation: drop one structural test (test split, false positives)

| Test dropped | Exporters | Capital look-alikes | Other clean | Other issue | Planted found |
|---|---|---|---|---|---|
| zero_rated_sales | 100.0% (92/92) | 6.7% (1/15) | 15.9% (7/44) | 75.8% (25/33) | 100.0% (16/16) |
| import_vat_paid_at_border | 25.0% (23/92) | 40.0% (6/15) | 34.1% (15/44) | 45.5% (15/33) | 100.0% (16/16) |
| capital_spending | 0.0% (0/92) | 100.0% (15/15) | 0.0% (0/44) | 0.0% (0/33) | 100.0% (16/16) |

Non-planted taxpayers with a persistent credit whose credit each test helped explain: zero_rated_sales 156, import_vat_paid_at_border 60, capital_spending 15.
Every credit period records all three tests: True.

## Lanes and amounts (test split)

- Exporters' claims whose policy-correct lane is fast that got fast: 94.9% (1727/1819)
- Planted taxpayers' claims kept out of the fast lane: 100.0% (353/353)
- Output VAT suppressed in planted returns: M46,853,553; unexplained credit the review computed for them: M30,949,429 (the remainder after the structural tests, not an assessment).
- Any persistent-credit finding eligible for assessment: False (by design).

## Whole assurance system on this seed (test split)

| Issue | Detected | False positives |
|---|---|---|
| fake_invoices | 100.0% (5/5) | 0.0% (0/195) |
| untaxed_imported_services | 87.5% (7/8) | 2.1% (4/192) |
| disguised_employment | 62.5% (5/8) | 0.5% (1/192) |
| related_party_loss | 100.0% (9/9) | 1.1% (2/191) |
| policy_gap | 100.0% (8/8) | 0.0% (0/192) |
| persistent_credit | 100.0% (16/16) | 0.0% (0/184) |

- Clean taxpayers flagged for anything: 4.6% (7/151)
- Lane matches the policy-correct lane: 96.9% (3927/4051)
- Correct findings routed to an expected candidate route: 100.0% (57/57)

## Errors (test split)

No persistent-credit misses or false positives.

11 errors on the other five issues are listed in `errors.csv`; their causes are the ones reported for TAX-118.

## Limits

- The planted mechanism is one pattern (standard-rated sales under-declared in both the return and the books). A taxpayer whose structural explanation is itself false (fake exports, fictitious assets) is not detected here: capital spending comes from the taxpayer's books, so the review asks the officer to verify the assets.
- Thresholds were set on debugging seeds by the same agent that wrote the generator.
