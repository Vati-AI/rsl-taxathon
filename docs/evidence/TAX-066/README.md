# TAX-066 evidence: risk calibration and drift evaluation

Design: [ADR-030](../../decisions/ADR-030-risk-calibration-drift.md). Code: `ai/evaluation/risk_calibration.py`. Data: `data/synthetic/tax066_reviewed_outcomes.json` (synthetic only). Tests: `tests/ai/test_risk_calibration.py`.

Reproduce:

```sh
python -m ai.evaluation.risk_calibration --check   # report plus baseline gate
python -m ai.evaluation.risk_calibration --json    # full report
python -m unittest discover -s tests/ai -p 'test_risk_calibration*.py' -v
```

## Run of 2026-11-05 (calibration version `tax066-risk-calibration-v1`)

- **Approved ruleset:** `vati-synthetic-tax-risk` 1.0.0, digest `sha256:2218204b…f55f`.
- **Candidate:** 1.1.0, `proposed_calibration`, `auto_activate: false`, digest `sha256:29002a8b…3353`.

| Rule | Approved | Candidate | Version |
|---|---|---|---|
| TR-VAT-REFUND-MISMATCH (unsupported input VAT >) | 0.00 | 100.00 | 1.0.0 → 1.1.0 |
| TR-RELATED-PARTY-FEES (related-party purchases >) | 0.00 | 10000.00 | 1.0.0 → 1.1.0 |
| TR-PERSISTENT-LOSSES (loss quarters >=) | 3 | 3 (unchanged: raising it would miss TAX066-07) | 1.0.0 |
| TR-IMPORTED-SERVICE-GAP (unverified imports >) | 0 | 0 (unchanged: raising it would miss TAX066-08) | 1.0.0 |

**Windows:** calibration covers 2024-Q3 to 2025-Q2 (31 cases); holdout covers 2025-Q3 to 2025-Q4 (16 cases); the label cutoff is 2025-09-15. TAX066-32 is excluded as `label_after_cutoff`, because it was reviewed on 2025-10-02.

| Window | Model | TP | FP | FN | TN | False-positive rate | Recall | False-positive cases |
|---|---|---|---|---|---|---|---|---|
| Calibration | Approved | 14 | 10 | 0 | 7 | 10/17 | 14/14 | 02, 04, 06, 10, 13, 18, 20, 22, 23, 26 |
| Calibration | Candidate | 14 | 3 | 0 | 14 | 3/17 | 14/14 | 06, 22, 23 |
| Holdout | Approved | 7 | 8 | 0 | 1 | 8/9 | 7/7 | 34, 36, 37, 39, 41, 44, 45, 47 |
| Holdout | Candidate | 7 | 5 | 0 | 4 | 5/9 | 7/7 | 37, 39, 44, 45, 47 |

**Holdout comparison:**
- **Legitimate-case regressions:** none.
- **Newly missed review-warranted cases:** none.
- **False positives removed:** TAX066-34, 36 and 41.
- **Still false positives under the candidate** (reported, not hidden):
  - TAX066-37: related-party fees of 18,000 at a documented market rate. A 25,000 threshold would clear it, but the fit stays at the nearest grid value that the calibration data supports.
  - TAX066-44: losses explained.
  - TAX066-39, 45 and 47: imported digital services whose treatment was shown on request.

**Drift (approved rules, calibration window against holdout window):**

| Alert | Value | Threshold | Detail |
|---|---|---|---|
| `feature_psi:imported_service_unverified_count` | 0.4318 | 0.25 | bins [28, 2, 1] → [10, 4, 2] |
| `rule_rate:TR-IMPORTED-SERVICE-GAP` | 0.2782 | 0.20 | fire rate 0.0968 → 0.3750 |

The imported-services rule fires far more often in the holdout window, and most of the new cases were closed with no further action. This is a prompt for supervisors and the rule owner, not a change the calibration makes. Every alert must be acknowledged in the approval record.

**Checks:** ledger verified (head 144); no leakage; minimum calibration (31 ≥ 20), holdout (16 ≥ 10) and holdout-legitimate (9 ≥ 4) cases met; legitimate-case regressions 0 ≤ 0; new misses 0 ≤ 0; holdout false positives 8 → 5; candidate changes 2 rules. **The metric gate passes.**

**Promotion:** none has taken place. The candidate needs an approval record from a `SYN-SUPERVISOR-NN` who is not the proposer. That record must name the candidate, report and parent digests and acknowledge both drift alerts. Activation would then be a separate reviewed PR plus `scripts/jube_rules.py apply`. The approved ruleset file is unchanged, and a test checks this byte for byte.

These are synthetic results that demonstrate the method. They are not accuracy claims about real taxpayers.
