# TAX-118 results: synthetic benchmark of the refund and MNE assurance checks

**Everything here is system output on synthetic data**, scored automatically against a hidden answer key. Manual-baseline times are stated assumptions until a person times real cases. Nothing here is real RSL performance.

- Population: 300 synthetic FMCG taxpayers x 24 months, 6055 refund claims; seed 20261106.
- Development split 100 taxpayers (tuning), test split 200 taxpayers (run once, reported here).
- Sealed 2026-11-06T11:53:12+00:00: dataset `cb6c792d3712aa7e...`, answer key `65da23c193e94012...`.
- Rules frozen 2026-11-06T11:55:43+00:00: fingerprint `56d17eedb745300d...`; test run 2026-11-06T11:56:12+00:00.
- Regenerating from the seed reproduces the sealed data: True.
- Lekuka coverage 0.15; synthetic rates VAT 0.15, WHT 0.10, treaty WHT 0.05 (assumptions, not Lesotho law).

## Detection by planted issue (test split)

| Issue | Detected | False positives (taxpayers without that issue) |
|---|---|---|
| fake_invoices | 100.0% (8/8) | 0.0% (0/192) |
| untaxed_imported_services | 50.0% (4/8) | 2.6% (5/192) |
| disguised_employment | 81.8% (9/11) | 1.1% (2/189) |
| related_party_loss | 100.0% (8/8) | 3.1% (6/192) |
| policy_gap | 100.0% (8/8) | 0.0% (0/192) |

- Planted issue instances found: 86.1% (37/43)
- Taxpayers with an issue that got at least one correct flag: 86.5% (32/37)
- Mixed taxpayers with both issues found: 83.3% (5/6)
- **Clean taxpayers flagged (false-positive rate): 8.0% (13/163)**

## Refund lanes (test split)

- Lane matches the policy-correct lane: 94.0% (3935/4185)
- Valid claims proposed for the fast lane: 62.6% (2590/4135)
- Claims whose policy-correct lane is fast that got fast: 93.8% (2517/2682)
- Claims containing fake invoices kept out of the fast lane: 96.0% (48/50)
- Claims containing fake invoices sent to investigation: 74.0% (37/50)

Confusion (policy-correct -> system): fast->fast: 2517, fast->standard: 165, investigation->fast: 2, investigation->investigation: 36, investigation->standard: 9, standard->fast: 73, standard->investigation: 1, standard->standard: 1382

## Amounts (test split)

- Valid refund value recommended as undisputed (releasable while the rest is reviewed): M769,155,709 of M799,688,541 (96.2%)
- Fake VAT that leaked into an undisputed portion: M2,040,229
- Fake-invoice VAT planted: M8,785,566; in taxpayers the system flagged: M8,785,566
- Imported-services gap planted: M10,546,233; computed by the three-way match for those taxpayers: M4,351,499
- Valid claims whose undisputed amount is within M1 of the valid refund: 83.7% (3459/4135)

## Reconciliation and legal routes

- Claims with fakes left out of the books, caught by declared-vs-recomputed: 100.0% (37/37)
- Valid claims with an invoice not found in the taxpayer's books: 0.0% (0/4135)
- Clean taxpayer periods with a recompute finding: 0.0% (0/3912)
- Correct findings routed to the expected candidate provision or policy gap: 100.0% (41/41) (routes are unverified candidates; this measures routing, not legal correctness)
- Any finding eligible for assessment: False (by design)

## Time

- System processing: 0.0118 s per taxpayer (mean), 0.0141 s (p95), for 24 months of records. This is machine time to assemble the case, not officer review time.
- Manual assembly, **assumed**: 1026.1 minutes per taxpayer from the model {'per_claim': 20, 'per_claim_line': 3, 'per_foreign_counterparty': 15, 'cit_review': 30, 'per_agreement': 20}. Replace with the timed manual baseline (human/manual_baseline).

## Baselines (test split)

| Approach | Taxpayers with an issue flagged | Clean taxpayers flagged | Valid claims paid without audit / fast | Claims with fakes held |
|---|---|---|---|---|
| Vati checks | 86.5% (32/37) | 8.0% (13/163) | 62.6% (2590/4135) | 96.0% (48/50) |
| Vati checks without RSL score | 86.5% (32/37) | 8.0% (13/163) | 63.7% (2635/4135) | 80.0% (40/50) |
| Flag every refund above M100,000 | 91.9% (34/37) | 81.6% (133/163) | 31.1% (1284/4135) | 98.0% (49/50) |
| Flag every refund above M500,000 | 24.3% (9/37) | 9.2% (15/163) | 97.3% (4022/4135) | 26.0% (13/50) |
| Flag every refund above M1,000,000 | 0.0% (0/37) | 0.6% (1/163) | 99.8% (4128/4135) | 0.0% (0/50) |
| Manual (every refund audited) | not measured | not measured | 0% (all audited) | not measured |

The simple rule cannot say *which* issue it found or why; refund size says nothing about imported services, employment or related-party issues.

## Lekuka coverage (test split, same population)

| Coverage | Fake invoices detected | Clean flagged | Lane accuracy | Valid claims fast | Claims with fakes not fast |
|---|---|---|---|---|---|
| 0.15 (main) | 100.0% (8/8) | 8.0% (13/163) | 94.0% (3935/4185) | 62.6% (2590/4135) | 96.0% (48/50) |
| 0.10 | 87.5% (7/8) | 8.0% (13/163) | 94.0% (3933/4185) | 62.7% (2591/4135) | 96.0% (48/50) |
| 0.50 | 100.0% (8/8) | 8.0% (13/163) | 94.0% (3935/4185) | 64.5% (2666/4135) | 100.0% (50/50) |
| 0.90 | 100.0% (8/8) | 8.0% (13/163) | 93.8% (3925/4185) | 66.3% (2743/4135) | 100.0% (50/50) |

## Errors (every miss and false positive has a reason in errors.csv)

- **false_positive: disguised_employment** (2): A genuine consultant stayed 183 days or more, so permit presence conflicts with the fixed-term contract and the case is referred to a specialist. [2]
- **false_positive: related_party_loss** (6): Genuine losses with rising related-party fees but no explanation document supplied: the trigger fires and a human review is the intended outcome. [6]
- **false_positive: untaxed_imported_services** (5): Withholding was declared under a different payee reference, so it did not join to the counterparty; an officer would resolve it with one question. [5]
- **miss: disguised_employment** (2): No permit record for the individual, so presence could not be compared with the consultancy terms; no conflicting fact, no referral. [2]
- **miss: untaxed_imported_services** (4): Foreign fees were booked as general operating expenses, so the books do not show them as services and the three-way match did not test them. [4]

## How to read this honestly

- The generator, the answer key and the rules were written by the same AI coding agent. The dataset and key hashes were committed before tuning, rules were tuned on the development split only and frozen before one test run. This limits, but does not remove, the advantage of knowing how issues were planted.
- The data is synthetic and the noise levels are our assumptions. There are no real RSL outcomes. Legal routes are unverified candidates. Real Lekuka, bank and RSL fraud-model access has not been tested; the RSL score is a stand-in whose quality we set.
- Not tested: Docling extraction accuracy on real documents (copies are supplied as already-extracted fields), the manual baseline (time model is assumed), officer usability, and the outside reviewer's scoring of case packs.
- For a blind check, a team member can choose a private seed and run `python3 -m ai.evaluation.assurance_benchmark rerun --seed <private>`.
