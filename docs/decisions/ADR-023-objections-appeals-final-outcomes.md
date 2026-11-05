# ADR-023: Objections, appeals and reviewed final outcomes

Status: proposed (TAX-069, pending review)
Owner and task: Edga (owner), TAX-069; implemented by edga-claude-code-20261105-069
Date: 2026-11-05

(ADR-016 to ADR-022 are taken on main or open branches, so this takes ADR-023.)

Context and smallest prototype need: PROPOSAL.md section 20 describes the learning loop **investigation → decision → objection → appeal → final outcome → learning**. TAX-046 (ADR-013) records officer decisions approved by a different supervisor. TAX-059 (ADR-015) seals every action into a per-tenant hash chain and raises review-only signals. Nothing yet records that a taxpayer disputed a decision, how the dispute was reviewed, or that a decision was overturned. TAX-070 (outcome and appeal feedback loop) and TAX-077 (integrity views) need that record. The acceptance requires overturned findings to stay traceable and raise review signals *without rewriting the original evidence*.

Decision:

- **One record type for both stages.** `Vati Decision Challenge` holds one objection (against an approved decision) or one appeal (against the reviewed outcome of that decision's objection, `parent_challenge_id`). It carries `tenant_id`, `taxpayer_id`, `case_id` and `decision_id`, so the TAX-029 wildcard hooks narrow it. There is at most one objection per decision (`objection_key`) and one appeal per objection (`appeal_key`), and a new challenge waits while another is open. Only an `approved` decision can be challenged, and only an `upheld` or `partially_overturned` objection can be appealed.
- **Who acts:**
  - `challenge.lodge`: the decision's own taxpayer, or an auditor or supervisor recording it on the taxpayer's behalf. Never a service or specialist.
  - `challenge.review`: supervisors only. The reviewer must not be the decision's proposer or approver, the person who lodged the challenge, or (for an appeal) the objection's reviewer. All refusals are recorded and count towards the existing separation-of-duties signal.
  - `challenge.read`: the taxpayer for its own decisions, and staff and service in the tenant.

  Lodging and reviewing are human-only. Cited evidence must already belong to the decision's taxpayer in the tenant. New documents arrive through the TAX-032 upload, never by changing what the decision cited.
- **Outcomes** come from a fixed list: `upheld`, `partially_overturned`, `overturned`, `withdrawn`. None of them assesses tax, pays or denies a refund, or accuses anyone. A review needs a 20 to 4,000-character reason and the current `expected_version`.
- **No rewriting.** Nothing in this module writes `Vati Decision`, `Vati Evidence` or earlier ledger entries. Each challenge stores `decision_digest`, a SHA-256 over the decided fields (proposal and approval; Jube sync bookkeeping is excluded because retries may legitimately change it). Review is refused with `decision_changed` if the decision no longer matches, and the history view reports `original_decision_unchanged`. An overturned outcome does not move the case in Jube (ADR-001). The challenge view says `follow_up: new_decision_required`, and a new decision goes through TAX-046 like any other.
- **Traceability and signals.** Every lodge, review, read and refusal is sealed into the TAX-059 ledger under subject type `challenge` (`challenge.lodged` write, `challenge.reviewed` approval with the outcome and `target_actor` = the decision's proposer, `challenge.read`, `challenge.*.denied`). `audit.ledger.integrity_signals` adds a `decision_overturned` signal for each overturned or partly overturned review. It is `review_only`, carries the existing note that it is not a finding against any officer, and is withheld from the original decider (`withheld_about_viewer`). `get_challenge_history` returns the decision, every challenge in order, `overturned` IDs and the `final_outcome` (the last review when nothing is pending).
- **No legal claims.** RSL has not verified any objection or appeal deadline, statutory route or legal effect. None is enforced or computed. Every view carries `legal_route_verified: false` and a note. A human may record a free-text `route_reference` (up to 200 characters). The TAX-052 registry holds only synthetic placeholder authorities and is not used to imply a real route.

Alternatives and tradeoff:

- Separate Objection and Appeal DocTypes: clearer names, but two near-identical schemas and two sets of keys. One record with a `stage` keeps the chain query simple.
- Mark the original decision `overturned`: convenient for queries, but it would rewrite an approved, audited record that TAX-046 freezes, and the acceptance forbids rewriting. The digest plus the challenge chain gives the same answer without touching the decision.
- Enforce lodging deadlines: needed for real use, but no verified period exists. A guessed one would wrongly refuse or allow taxpayers.

Official sources and version/date checked (2026-11-05): this repository's PROPOSAL.md section 20, PROJECT_SCOPE.md (objections and appeals are expansion scope; no automatic assessment or refund action), REQUIREMENTS.md, ADR-001, ADR-007, ADR-013, ADR-015, and `ai/workflows/legal_routes.py` (TAX-052, synthetic placeholders only); Frappe Framework v16.54.0 at the commit in `integration/frappe-framework.lock.json`. No RSL legal source was consulted and none is claimed.

Verification performed: see the task evidence and the PR. Frappe-free tests are in `challenges/test_service.py`, and site-backed tests in `challenges/test_challenges_bench.py`, run with `scripts/frappe_dev.py verify`.

Consequences and revisit trigger: TAX-070 reads `get_challenge_history` and `decision_overturned` signals to learn from outcomes. TAX-077 can show them to restricted management review. Revisit when RSL confirms objection and appeal routes, deadlines or external tribunals (then add verified route data and deadline checks), or if a decision must be formally superseded by the new one (link it explicitly rather than editing the original).
