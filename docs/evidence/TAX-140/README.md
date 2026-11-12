# TAX-140 evidence: losses created through intra-group financing

The TAX-049 related-party review now separates financing-driven losses from operating
losses (`ai/tools/financing_losses.py`). All figures are synthetic, and the 3:1 ratio
is the framework's reported figure, unverified.

## What changed

### When the review runs
The review runs when a case record supplies per-period figures (`financial_periods`):
- operating result;
- related-party interest;
- other finance cost;
- optionally, related-party debt and equity.

### Classifying each loss period
A loss period is one where the operating result, less all finance cost, is below zero.
- **Financing-driven:** there is an operating profit after third-party finance cost, and
  related-party interest turns it into a loss.
- **Mixed:** there is an operating loss, and related-party interest is at least half of
  the loss.
- **Operating:** the loss comes mainly from trading.

### Alternative explanations
Each is tested and recorded with its result:
1. **Documented loan terms:** the related-party interest is compared with principal ×
   documented rate. A difference is a neutral discrepancy to ask about.
2. **Third-party finance share:** how much of the finance cost is owed to unrelated
   lenders.
3. **Capital investment:** a related-party loan documented as funding capital investment
   is counter-evidence for a business reason.
4. **Documented loss explanations:** the TAX-049 explanations (start-up, restructuring
   and so on), with evidence.

### Findings
All findings are advisory, with `legal_conclusion: null`.
- **`financing_driven_losses`**
  - Raised when at least two loss periods are financing-driven or mixed and no capital
    investment or loss explanation covers them.
  - Comes with a neutral evidence request: loan agreements, board approvals and the use
    of funds.
  - When explained, the pattern stays an observation with `explained: true` and no
    finding.
- **`related_party_debt_above_reported_ratio`**
  - Raised when related-party debt is above the reported 3:1 debt-to-equity ratio.
  - It carries a scope note: whether the provision is limited to mining is unverified.
- **Loss carry-forward pattern**
  - A growing carried-forward balance alongside financing-driven losses is recorded on the
    financing observation.
  - It includes the related-party interest those periods contain, capped at the balance.

### Routes (TAX-116 register)
- `financing_driven_losses` routes to the unverified candidates `CAND-RP-ALLOCATION`
  (section 113 allocation between associates) and `CAND-DEDUCTION-TEST`.
- `related_party_debt_above_reported_ratio` routes to the unverified `CAND-THIN-CAP`.
- The existing interest-free-loan signal still routes to the `GAP-ARMS-LENGTH` policy
  gap.
- No route is ever eligible for assessment.

### Playbook
- The related-party playbook proposal carries a `financing` block with the findings, the
  loss-period split, the tests and each finding's route.
- Its code is `financing_driven_losses` when that is the main pattern.
- A loan documented as funding capital investment is listed among the alternative
  explanations.

### Playbook registry (ADR-017)
- Registry `SYN-PBR-2026.11.4` re-pins `related_party_review_tool@1.0` and
  `related_party@1.0` to the reviewed source.
- The tool's pinned files now include `ai/tools/financing_losses.py`.
- As in TAX-097 and TAX-110, this PR's review is the approval gate.

## Results

The fixture is `data/synthetic/tax140_financing.json`; full outputs are in
`scenario_results.json`.

| Case | Situation | Loss periods | Tests | Findings |
|---|---|---|---|---|
| FIN-001 | Operating profits turned into losses by 12% interest on a M2.5m group loan; debt 5× equity; losses carried forward | financing-driven 2023–2025 | interest matches terms; mainly related-party finance; no capital or loss explanation | `financing_driven_losses` (carry-forward to M360,000, with M360,000 of related-party interest), `related_party_debt_above_reported_ratio` (5.00 against 3:1) |
| FIN-002 | Trading losses with a small group loan | operating 2023–2025 | mainly third-party finance | none: operating losses are not attributed to financing |
| FIN-003 | Financing-driven losses from a loan documented as funding a new plant | financing-driven 2024–2025 | interest differs from the 10% terms; capital investment documented | none: the pattern is recorded as explained, and the interest difference is a discrepancy to ask about |

## Verification

```bash
python3 -m unittest tests.ai.test_financing_losses
```

```bash
python3 scripts/dev.py check
```

```bash
python3 -m ai.evaluation.agent_eval --check
```

- **`tests/ai/test_financing_losses.py`: 12 tests.**
  - They cover each scenario's expected findings and non-findings, the operating, mixed
    and financing split, all four explanation tests, a documented loss explanation
    covering the pattern, the carry-forward figures, the thin-capitalisation ratio and
    scope note, resolving citations, neutral wording, refused malformed figures, and an
    unchanged TAX-049 review without figures.
  - Two playbook route tests check unverified candidates, never eligible.
  - With the pinned `ai/requirements.lock` installed, all 12 ran and passed. Without
    LangGraph, the 2 playbook tests skip, as in `dev.py check`.
- **Existing related-party tests:** `test_related_party` and `test_related_party_playbook`
  pass (21, with LangGraph).
- **`dev.py check`:** passes; it now runs `test_financing*.py`. Ruff, format and mypy are
  clean.
- **`agent_eval --check`:** gate PASS, with LangGraph installed. It failed with
  `source_digest_mismatch` until the registry was re-pinned.
- **Rule fingerprints:** `rule_register.candidates.json` is one of the TAX-118 and TAX-139
  frozen rule files. Their fingerprints change, which `assurance_live` reports for
  information only; the sealed results are not re-run.
