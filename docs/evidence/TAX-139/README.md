# TAX-139 evidence: persistent VAT credit positions

Challenge 2 names persistent VAT credit positions as a risk area and says legitimate
refunds must keep moving. TAX-139 adds a multi-period review that tests structural
explanations first, a seventh synthetic scenario (SYN-007) and a sealed measurement on a
new seed. All data is synthetic, and all thresholds are prototype assumptions, not RSL
policy.

## What was built

- **`ai/tools/persistent_credit.py`**
  - Policy is in `persistent_credit_policy.json`. The review works period by period over
    the declared e-Tax VAT returns, where credit = input VAT claimed − output VAT.
  - For every credit period it runs three structural tests in a fixed order and records
    each one with the amount explained, the remainder, the basis and the evidence IDs:
    1. **Zero-rated sales**: zero-rated sales × rate, which is the output VAT the taxpayer
       did not charge. The basis is the return.
    2. **Import VAT paid at the border**: ASYCUDA entries registered to the taxpayer in the
       period. This is RSL's own customs record. If ASYCUDA does not answer, the test is
       recorded as `not_tested` and the credit stays unexplained.
    3. **Capital spending**: VAT on purchases booked to asset accounts. This comes from the
       taxpayer's books only, so it is marked `books_only` and the officer is asked to
       verify that the assets exist.
  - A credit is *persistent* when it appears in at least 6 periods and at least half of
    the window.
  - The finding `persistent_vat_credit_unexplained` is raised only when the *unexplained
    remainder* also persists, above M1,000 in at least 6 periods and at least half of the
    window. The finding lists those periods, the unexplained total and the questions to put
    to the taxpayer.
  - The review never decides anything (`decision: null`).
- **Pipeline (`assurance_pipeline.assess_taxpayer`, version `tax139-pipeline-v2`)**
  - The review runs after the related-party checks.
  - A finding is a taxpayer-level open item, so the taxpayer's claims stay out of the
    fast lane, with the reason shown.
  - The finding routes to the unverified candidates `CAND-RECORDS` and `CAND-REFUNDS` and
    is never eligible for assessment.
- **Live path (TAX-121)**
  - `vati_tax.assurance.rules` records the finding as a `refund_reconciliation` /
    `missing_evidence` finding.
  - The proposed action is `request_evidence`, which needs human approval. The finding
    cites the returns of the unexplained periods and lists the three tests as the
    alternatives tested.
  - A new check, `persistent_credit`, is degraded when ASYCUDA is unavailable.
- **Seventh scenario: `data/synthetic/tax139_syn007.json` (SYN-007)**
  - Three synthetic taxpayers over 12 monthly returns:

    | TIN | Taxpayer | Credit | Explained by | Finding | Last claim lane |
    |---|---|---|---|---|---|
    | 510000071 | Exporter | 12 of 12 periods | Zero-rated sales | None | **fast** |
    | 510000072 | Equipment programme | 8 of 12 periods | Capital spending (asset check asked) | None | standard (not an exporter) |
    | 510000073 | Declared standard-rated sales fall to 55% from month four | 9 periods | Nothing | `persistent_vat_credit_unexplained` | standard |

  - `tests/ai/test_persistent_credit.py` runs the scenario through `assess_taxpayer` and
    checks each expected outcome.
- **Generator (`assurance_population.generate`)**
  - The `persistent_credit` parameter plants under-declared standard-rated sales and adds
    capital-spending look-alikes (clean non-exporters with a 12–16-month equipment
    programme).
  - Both use their own random streams. Without the parameter, the sealed TAX-118
    population regenerates byte-for-byte, and a test checks this against
    `docs/evidence/TAX-118/manifest.json`.

## Measurement

The protocol is the same as TAX-118:

1. Thresholds were set on debugging seeds 777 and 778.
2. Seed 20261111 was sealed, with only its hashes committed.
3. The development split was run; it needed no tuning.
4. The rules were frozen.
5. The test split was run once.

Each step is its own commit. Results are in [RESULTS.md](RESULTS.md), with data in
`results.json`, `errors.csv`, `dev_summary.json` and `manifest.json`.

The headline numbers below are from the test split (200 taxpayers).

- **Planted persistent credits found:** 16 of 16.
- **False positives:** 0 of 184 taxpayers without the issue:
  - 0 of 92 exporters;
  - 0 of 15 capital-spending look-alikes;
  - 0 of 44 other clean taxpayers;
  - 0 of 33 taxpayers with other planted issues.
- **The naive rule** ("credit in at least half the periods") flags:
  - 100% of exporters;
  - 100% of capital-spending look-alikes;
  - 45% of other clean taxpayers;
  - 88% of taxpayers with other issues.

  Testing explanations first is what removes these false positives.
- **Ablation:** each test is needed. Without the zero-rated test, every exporter is
  flagged. Without the customs test, 25% of exporters and 34% of other clean taxpayers are
  flagged. Without the capital test, every capital-spending look-alike is flagged.
- **Fast lane for legitimate structural credits:** no exporter is flagged, so the review
  moves no exporter's claim. Of the exporters' claims whose policy-correct lane is fast,
  94.9% got fast. The other 5.1% (92 claims) are held by checks that already existed:
  - an open item from another check on the same taxpayer: `wht_declared_below_expected`
    on 58 claims (withholding declared under a variant payee reference, a known TAX-118
    false positive) and `related_party_fees_with_persistent_losses` on 22 claims;
  - the remaining claims, by RSL scores at review level.
- **Planted taxpayers' claims kept out of the fast lane:** 353 of 353.
- **Whole system on this seed:** the other five issues behave as in TAX-118. Detection and
  false-positive rates are in RESULTS.md, and the 11 errors fall into the causes TAX-118
  already reports. 100% of correct findings route to an expected candidate.

## Limits, stated plainly

- **The planted pattern is strong.** Declared standard-rated sales fall to 45–70% of the
  real figure, every month from a start month. A small or intermittent suppression would
  leave a remainder in fewer than half the periods, and this review would not flag it. The
  100% detection rate measures this pattern only.
- **A false structural explanation is not detected:**
  - fake exports (zero-rated sales that did not happen) are not detected;
  - fictitious assets are not detected either. Capital spending is read from the
    taxpayer's books, so the review asks the officer to verify the assets rather than
    trusting them.
- **The same agent wrote everything:** the generator change, the answer key and the
  review. Only the hashes were committed before the development run. A team member can
  seal a private seed in a new evidence directory for a blind check.
- **Legal routes are unverified candidates.** The finding asks for evidence; it is never
  an assessment and never a refund decision.

## Commands

```bash
python3 -m unittest tests.ai.test_persistent_credit
```

```bash
python3 -m ai.evaluation.persistent_credit_benchmark --check
```

```bash
python3 -m ai.evaluation.persistent_credit_benchmark test
```

The `test` command refuses to run again; the sealed run is the one reported here.
