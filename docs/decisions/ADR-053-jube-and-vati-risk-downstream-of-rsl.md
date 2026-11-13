# ADR-053: Jube and Vati risk output sit downstream of RSL's selection

Status: accepted
Owner and task: Gupta, TAX-131 (implements ADR-043 §5 and §6)
Date: 2026-11-13

Context and smallest prototype need:
ADR-043 says RSL selects, Vati investigates and RSL acts. Three things still read as if Vati
selected cases and scored taxpayers:

- **Who reaches Jube.** Any sent `Vati Submission` reached Jube's tax-risk rules (TAX-035/039).
  That included portal, `api` and `synthetic_seed` sends, not only claims read from RSL. The
  portal claim form is now the fixture-only Online Refunds simulator (TAX-125). Even so, a
  taxpayer or operator could still create and send a portal submission through Desk or REST,
  and it would open a case.
- **How officers saw it.** The auditor app headed Jube's signals "Why this case was selected"
  and labelled them "Signal from Investigation". RSL's own risk sets and refund-model score
  were stored (TAX-123, TAX-114) but never shown on the case.
- **Calibration and baselines.** The TAX-066 calibration and the TAX-067 peer baselines had no
  output aimed at RSL's Risk Unit.

Decision:

1. **Only referred cases are sent.** `submissions.domain.referred_source` makes this one rule:
   - A claim read from RSL's Online Refunds Application (`rsl_online_refunds`) is referred.
   - Any other source counts only where the site runs the Online Refunds simulator
     (`vati_online_refunds_simulator`), which stands in for RSL in fixtures and the demo.

   The `Vati Submission` controller refuses any other send with "File with RSL…". The three
   send hooks (assurance, bridge and pipeline) get a `not_referred` origin from
   `bridge.rules.submission_origin`, so they open no case and never invoke Jube.

   Nothing iterates the taxpayer population into Jube. The RSL ingests read RSL's sources per
   taxpayer but do not score. The TAX-123 referral cases do not reach Jube's rules yet, because
   they carry no declared facts. Showing them in Jube is TAX-134.
2. **The Jube ruleset is unchanged.** Its gateway still admits every payload it is sent; Vati
   now sends only referred cases. The ruleset and its digest are untouched, so the approved
   TAX-066 baseline still holds.
3. **RSL's scores are RSL's; Vati's output is corroboration.**
   - `getCase` and `listCases` fill `selection_reasons` from RSL first:
     - the referral reason and each RSL risk-set score (`source: audit_system`);
     - the RSL refund-model score from the lane inputs (`source: online_refunds`).
   - Each summary says "RSL's score, not a Vati score". An input not owned by RSL is left out
     rather than shown under RSL's name.
   - Referral cases (TAX-123) are not listed by `/api/v1` yet. The `Case` contract requires a
     `submission_id`, and a referral has none, so `_case_rows` leaves them out (ADR-046). The
     projection is ready for them. Making `submission_id` optional is a follow-up contract
     change. Until then, the RSL refund-model score on Online Refunds claims is the RSL input
     officers see.
   - `SelectionReason.source` accepts `RslServiceName`.
   - The auditor app heads the section "Why RSL selected this case". It labels each reason
     "RSL's input · <RSL system>" or "Vati signal from <service>", and says Vati signals
     corroborate or question RSL's selection.
   - Two labels change:
     - a Jube-produced finding is a "Vati rule check", not a "Risk rule";
     - the integrity override control is labelled "Case rating". Its stored key `risk_rating`
       is kept for existing ledger entries.
4. **Calibration and baselines are feedback for RSL's Risk Unit.**
   `ai.evaluation.risk_unit_feedback` builds a `vati.risk-unit-feedback/1` pack under the
   assumed contract `rsl.audit_system.risk_unit_feedback.v0-assumed`, with authority
   `feedback_only` and route WSO2. It contains:
   - the hold-out agreement between Vati's rule signals and officers' reviewed outcomes, how
     often each rule signalled, drift alerts, and proposed Vati rule changes (never active);
   - per-metric counts of TAX-067 comparisons (within or outside the typical range, or
     insufficient evidence).

   The pack is aggregate only. It has no case, taxpayer, tenant or TIN identifiers and no
   amounts. It has no score, selection, status or decision field, and it is digest-sealed;
   `validate_pack` refuses anything else. The CLI writes the pack to a file. Delivery through
   WSO2 waits for RSL's specification.

Alternatives and tradeoff:

- Gate in Jube's gateway rule (a case-origin feature): rejected for now. It changes the
  approved ruleset and its digest and invalidates the TAX-066 baseline. It would also only
  stop the rules, not the case.
- Send the feedback through the TAX-124 outbound contracts: rejected. Those are per case, keyed
  by an RSL reference and a TIN; feedback is aggregate and must carry neither.
- Drop Vati's rule signals entirely: rejected. Officers use them as corroboration, and the
  Risk Unit feedback depends on them.

Official sources and version/date checked:
None from RSL. The Risk Unit and its feedback needs are unverified public research
(docs/reference/RSL_LANDSCAPE.md). The pack shape is an assumption until RSL specifies one.

Verification performed (or named implementation task):
- Frappe-free tests:
  - `submissions/test_domain.py`: only RSL claims are referred without the simulator.
  - `bridge/test_rules.py`: a non-referred send never registers.
  - `api_v1/test_core.py`: RSL reasons are labelled as RSL's, non-RSL scores are left out,
    and the contract limits hold.
  - `tests/ai/test_risk_unit_feedback.py`: the pack matches the TAX-066 report, baselines are
    counted, no identifiers or scores appear, tampering is refused, and the CLI cannot write
    into the rules directory.
- Contract tests: the fixture cases carry RSL reasons with RSL sources. The auditor unit tests
  cover the new labels.
- Bench tests on a disposable site cover:
  - a portal send refused without the simulator;
  - RSL intake claims still evaluated;
  - referral risk sets labelled as RSL's in the case projection.

Consequences and revisit trigger:
- A site without the simulator accepts no Vati-side filing. The demo and fixtures turn the
  simulator on, as before.
- Revisit:
  - when RSL specifies how its Risk Unit receives feedback;
  - when referral cases should reach Jube (TAX-134);
  - if the gateway rule should carry the case origin itself.
