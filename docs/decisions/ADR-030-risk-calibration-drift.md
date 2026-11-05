# ADR-030: Risk calibration and drift evaluation on reviewed outcomes

Status: proposed (TAX-066, pending review)
Owner and task: Leseli (owner, AI evaluation), TAX-066; implemented by leseli-claude-20261105-066. Review: Gupta (AI area); Edga for the TAX-059 ledger use.
Date: 2026-11-05

Context and smallest prototype need: TAX-066 asks for an evaluation of reviewed outcomes with a temporal holdout, false-positive metrics and drift reporting, and requires that a new calibration cannot replace the approved model without review and that legitimate-case regressions are measured. docs/ARCHITECTURE.md requires supervisor review, holdout evaluation and rollback before a new risk version is activated, and docs/AI_STANDARD.md forbids automatic learning or model and risk promotion. The approved risk model in this prototype is the reviewed TAX-035 ruleset (`integration/jube/rules/tax_risk_rules.v1.json`), from which Jube's activation rules are rendered. Officers' reviewed decisions are recorded by TAX-046 and sealed in the TAX-059 ledger. ADR-029 (TAX-056) already gates agent behaviour; nothing yet measured the risk rules against what officers actually decided, or stopped a recalibration from silently replacing the approved rules.

Decision:

- `ai/evaluation/risk_calibration.py` evaluates the approved ruleset with the TAX-035 evaluator itself (`tax_risk.evaluate_record`), so the calibration and Jube cannot disagree about what a rule does.
- **Labels come only from the ledger.** Input is TAX-059 entries in the `entry_view` shape (the supervisor-only `get_ledger` read) plus a `verify_ledger()` report. The report must be intact, and no supplied entry may lie beyond the verified head. As in TAX-064, the approved TAX-046 outcome arrives as the joined `detail` field. A case's label is its latest decision that meets all of these conditions:
  - proposed by one officer;
  - approved by a different supervisor;
  - outcome on the allowlist;
  - executed without a failure;
  - not overturned on a TAX-069 challenge.

  Every other decision is excluded with a reason (`sod_violation`, `approver_not_supervisor`, `not_executed`, `overturned_on_challenge`, `unknown_outcome`, `missing_proposal`) and listed in the report. Entries from another tenant and conflicting chain positions are refused.
- **Legitimate versus review warranted.** `close_no_further_action` is legitimate. The other allowlisted outcomes are review warranted: `close_after_correction`, `request_further_evidence`, `continue_investigation`, `escalate_to_supervisor` and `refer_to_specialist`. A flagged legitimate case is a false positive; an unflagged review-warranted case is a miss.
- **Temporal holdout, no leakage.** The calibration periods must all precede the holdout periods and may not overlap them. A candidate is fitted only on calibration-period cases whose label was approved before `label_cutoff`. A calibration-period case reviewed later is excluded as `label_after_cutoff`. A test flips every holdout label and shows the candidate is unchanged.
- **Calibration.** A reviewed grid of integral thresholds per rule (Jube thresholds stay integral). The fit is lexicographic on the calibration set: fewest misses, then fewest false positives, then fewest changed rules, then the smallest move from the approved values. The candidate's ruleset and changed rules get a minor version bump, `status: proposed_calibration` and `auto_activate: false`, and the parent digest is recorded. The candidate must still pass `validate_ruleset` and render Jube scripts.
- **Metrics and gate.** The calibration and holdout windows each get TP, FP, FN and TN counts with exact fractions for false-positive rate, recall and precision. Every false-positive and missed case is listed by ID. The metric gate blocks promotion on any of these:
  - legitimate-case regressions: holdout cases flagged by the candidate but not the approved rules, where the officer closed with no further action;
  - newly missed review-warranted holdout cases;
  - more holdout false positives than the approved rules;
  - too few calibration, holdout or holdout-legitimate cases;
  - a candidate that changes nothing.

  The thresholds live in the fixture: 0 regressions and 0 new misses.
- **Drift.** Calibration window against holdout window, plus a per-period table. It covers rule fire rates (alert above an absolute change of 0.20), the share of legitimate labels, and the Population Stability Index per feature with fixed reviewed bins (alert above 0.25). Alerts are listed with their values.
- **Promotion needs review, and approval cannot override the gate.** `promote` returns an approval record only when the approval meets all of these:
  - its decision is `approve`;
  - the approver matches `SYN-SUPERVISOR-NN` (as in ADR-017) and is not the proposer;
  - it gives a written rationale;
  - it names the exact candidate digest, report digest and current approved-ruleset digest (otherwise `stale_parent`);
  - it acknowledges every drift alert.

  A blocked metric gate is refused even with a valid approval. The record states that activation is a separate reviewed PR that replaces the ruleset file and runs `scripts/jube_rules.py apply`. The module has no code path that writes the approved ruleset, and `write_candidate` refuses the rules directory.
- **CI.** `python -m ai.evaluation.risk_calibration --check` compares the report with the reviewed baseline in `data/synthetic/tax066_reviewed_outcomes.json`. That covers the included and excluded cases, the candidate changes, the confusion counts, regressions, new misses, the gate and the drift alert IDs. Any change must be reviewed in the fixture, as with ADR-029's known failures.

Alternatives and tradeoff:

- **Learn thresholds by optimising a single score** (for example, F1). This hides the asymmetric cost of burdening legitimate taxpayers. A lexicographic fit with an explicit legitimate-regression gate keeps that cost visible.
- **A random train/test split.** This leaks later review practice into calibration, which a temporal split with a label cutoff does not.
- **Read labels from Frappe decision records directly.** Those records are not sealed. The TAX-059 ledger is tamper-evident and already the supervisor read path, and its verification report is required.
- **Let an approved candidate overwrite the ruleset file.** That would make the module an activation path. Activation stays a reviewed PR plus a Jube apply, and ADR-017's rule that code review is the approval gate is kept.
- **Calibrate Jube directly.** That needs a live stack and Jube's maker-checker. The reviewed JSON stays the source of truth, as in TAX-035.

Official sources and version/date checked (2026-11-05): repository sources only, with no new dependency. These are docs/ARCHITECTURE.md (activation needs supervisor review, holdout evaluation and rollback), docs/AI_STANDARD.md (no automatic promotion), docs/REQUIREMENTS.md (controlled calibration and drift), `integration/jube/tax_risk.py` and `tax_risk_rules.v1.json` (TAX-035), `vati_tax.decisions.rules.OUTCOMES` (TAX-046), `vati_tax.audit.ledger.entry_view` and `vati_tax.audit.api` (TAX-059), the TAX-064 `detail` convention, TAX-069 overturned outcomes, ADR-017 and ADR-029.

Verification performed: `python -m ai.evaluation.risk_calibration --check` on the shipped synthetic data (48 cases over 2024-Q3 to 2025-Q4; one excluded as `label_after_cutoff`). It produced candidate 1.1.0, raising `TR-VAT-REFUND-MISMATCH` to 100.00 and `TR-RELATED-PARTY-FEES` to 10000.00.

| Window | Approved rules | Candidate |
|---|---|---|
| Calibration (31 cases) | TP 14, FP 10, FN 0, TN 7 (false-positive rate 10/17) | TP 14, FP 3, FN 0, TN 14 (3/17) |
| Holdout (16 cases) | TP 7, FP 8, FN 0, TN 1 (8/9) | TP 7, FP 5, FN 0, TN 4 (5/9) |

- No legitimate-case regressions and no new misses; the metric gate passes.
- Drift alerts on imported services: feature PSI 0.4318, and the rule fire rate rose from 0.0968 to 0.3750.
- `tests/ai/test_risk_calibration.py` has 27 tests covering:
  - the baseline;
  - leakage: flipped holdout labels, the label cutoff changing the fit, overlapping and non-temporal windows;
  - a seeded legitimate-case regression and a newly missed case, both blocking promotion even with approval;
  - every promotion refusal;
  - a refused stale parent and a refused tampered report;
  - the protected path;
  - ledger refusals and exclusions;
  - determinism;
  - PSI and drift with no alerts.

Consequences and revisit trigger:

- The synthetic numbers show the method; they are not accuracy claims. 47 synthetic cases cannot calibrate a national system.
- A candidate approved here still needs the activation PR, a Jube apply with a separate checker (TAX-035), and a rollback to the previous version.
- The drift alert on imported services is a prompt for supervisors and the rule owner (Keletso), not an automatic change.
- Revisit when:
  - real reviewed outcomes exist (bins and thresholds then need evidence);
  - TAX-067 adds peer-group baselines;
  - TAX-070 or TAX-065 provide a supervisor approval workflow that should issue these approval records;
  - Jube rules gain non-threshold changes that a grid cannot express.
