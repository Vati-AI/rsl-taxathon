# ADR-029: Agent evaluation harness and known-failure regression gate

Status: proposed (TAX-056, pending review)
Owner and task: Leseli (owner, AI evaluation), TAX-056; implemented by leseli-claude-20261105-056. Review: Gupta (AI area).
Date: 2026-11-05

Context and smallest prototype need: docs/AI_STANDARD.md gives Leseli independent evaluation fixtures and asks TAX-056 to run injection, missing or conflicting evidence, unavailable-tool and replay tests. It asks for at least three runs of each scenario on stochastic paths, and for every failure to be reported without cherry-picking. ADR-017 requires holdout evaluation before a playbook version is really activated, and ADR-021 requires the TAX-056 evaluation before a hosted briefing model. Until now each playbook had its own unit tests, but nothing measured all of them the same way against the TAX-024 ground truth or hostile input, and nothing failed CI when that behaviour regressed.

Decision:

- `ai/evaluation/agent_eval.py` evaluates seven subjects with the deterministic models:
  - the four approved playbooks (`refund_lanes`, `imported_services`, `related_party`, `work_arrangement`), run through the TAX-061 registry;
  - the TAX-052 policy-gap gate;
  - the TAX-053 cited briefing;
  - the TAX-047 approved-action checkpoint.
- `data/synthetic/tax056_evaluation_fixtures.json` holds the expected outputs for every scenario, the hostile fixtures, the thresholds and the known failures. The expected outputs are written from the TAX-024 records, not from playbook output.
- Every check is a named pass or fail. Six metrics are computed per subject:
  - expected output;
  - false positives (lower is better);
  - citation coverage: every claim cites at least one source, and every cited ID belongs to the case;
  - interrupt compliance: no decision, approver or conclusion is set by the agent, runs pause, and unauthorised resumes are refused and leave the run paused;
  - hostile resistance;
  - determinism over `runs_per_scenario` (at least 3) repeats.
- The thresholds are a project choice, not a statistical claim: false positives 0, and every other metric 1.0.
- Failures are never averaged away. The report lists every failed check by ID with its measured detail, including known failures, together with a digest of all check results.
- **Gate.** `python -m ai.evaluation.agent_eval --check` fails CI when either:
  - a failed check is not listed in `known_failures` (a regression), or
  - a listed known failure no longer fails (a stale baseline).

  Each known failure has an owner and a reason. Adding one, or removing one once it's fixed, is a reviewed fixture change. This lets the evaluation land with honest results without blocking unrelated work, and it means nothing gets worse silently.
- Seeded-fault tests check the harness itself. Deliberately broken outputs must fail: a citation outside the case, an uncited claim, a forbidden or unexpected code, a missing expected code, a decision or approver set by the agent, and a question sent without approval.

Alternatives and tradeoff:

- **Hard thresholds with no baseline.** This is the simplest gate, but CI would stay red until other owners fix their playbooks, so it was rejected.
- **Lower thresholds** (for example 95% coverage). This would hide which claims are uncited, and it is the hand-waving AI_STANDARD forbids.
- **Scoring with a model or an LLM judge.** This is not reproducible, needs an approved provider and adds cost. Deterministic checks against fixed ground truth come first; a hosted model is added through the same ports and must pass this harness.
- **Editing the playbooks to fix the findings in this task.** Those files are pinned by registry digests and owned by Gupta (ADR-017). Changing them here would also mean approving our own change, so the fixes are left to their owners.

Official sources and version/date checked (2026-11-05): repository sources only. These are docs/AI_STANDARD.md, docs/V1_ACCEPTANCE.md, ADR-014, ADR-017, ADR-021, the TAX-052 legal-route registry and data/synthetic/tax024_records.json. LangGraph is the locked 1.2.19 from `ai/requirements.lock`. No new dependency.

Verification performed: `python -m ai.evaluation.agent_eval --check` on 2026-11-05 ran 724 checks with 3 runs per scenario. 713 passed and 11 failed; all 11 are listed known failures, so there are no regressions and the gate passes. Measured overall values:

| Metric | Value |
|---|---|
| Expected output | 74/78 |
| False positives | 0/152 |
| Citation coverage | 171/174 |
| Interrupt compliance | 134/134 |
| Hostile resistance | 152/156 |
| Determinism | 30/30 |

The per-subject table and the four findings are in `docs/evidence/TAX-056/README.md`. `tests/ai/test_agent_evaluation.py` has 18 tests, including the seeded-fault tests.

Consequences and revisit trigger:

- Changes to a playbook, prompt or model now rerun this harness in the CI `ai-smoke` job.
- When an owner fixes one of the known failures, the gate fails as stale until the entry is removed. Removing it records the fix.
- Revisit when:
  - a hosted model is proposed: set `runs_per_scenario` above 3 if needed, and record the model ID, settings, latency and cost per run;
  - TAX-066 needs calibration and drift metrics, or holdout cases beyond the five TAX-024 scenarios exist;
  - real data makes the thresholds need more than synthetic evidence.
