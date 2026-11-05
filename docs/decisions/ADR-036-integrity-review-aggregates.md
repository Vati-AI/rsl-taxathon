# ADR-036: Restricted policy-gap and integrity aggregates for management review

Status: proposed (TAX-077, pending review)
Owner and task: Leseli (owner, auditor frontend), TAX-077; implemented by leseli-claude-20261105-077. Review: Edga (TAX-059 ledger use and the Frappe endpoint); Kananelo or Keletso (screens).
Date: 2026-11-05

Context and smallest prototype need: TAX-077 asks for verified policy gaps and unusual override patterns, aggregated for restricted management review, with policy exposure kept apart from noncompliance, signals kept apart from accusations, and validated aggregation privacy. TAX-052 marks findings that have no verified legal route (a policy gap). TAX-059 (ADR-015) records overrides with a reason in a hash-chained ledger and derives review-only integrity signals, withholding signals about the viewing supervisor. TAX-069 adds overturned decisions. TAX-060 (ADR-020) shows management figures but stops at counts. Nothing yet answered "where do the rules not cover what we see, and how are controls used" without exposing individual officers or taxpayers.

Decision:

- **One Frappe-free rule set.** `backend/vati_tax/vati_tax/integrity/aggregate.py` builds the review body. The Vati service (`integrity/service.py`, bound to Frappe in `integrity/api.py`) and the fixture API (`scripts/fixture_integrity.py`) both call it, so the fixture cannot show more than the service would.
- **Two separate subjects, worded so they never imply wrongdoing.**
  - *Policy exposure* counts reviewer-confirmed findings with `no_verified_route`, by policy area. Its note says it describes the rules and is not a finding of noncompliance. Findings still pending review are only counted, never broken down.
  - *Integrity patterns* count overrides by control, and review signals by kind only. The notes say the counts describe how controls are used and are not a measure of any officer.
  - Tests check that no accusatory term (fraud, violation, misconduct, suspicious and similar) appears in the body or on screen.
- **Privacy rules (aggregates only):**
  - Every cell is checked against the people it covers: distinct taxpayers for policy exposure; distinct recording supervisors *and* distinct affected officers for overrides.
  - A cell covering fewer than `min_group_size` (3) people is not shown on its own. Small cells are combined into one row; if that row is still too small, the next smallest visible cell joins it, so no small cell can be derived by subtracting shown rows from the total.
  - If the whole section covers fewer than `min_group_size` people, the section is `insufficient_volume` and carries no counts.
  - No officer or taxpayer identifier, login or name is returned. Signals keep only their kind and count; subjects and event IDs are dropped. Signals about the viewer are withheld and only counted (the TAX-059 rule).
  - There is no drill-down from the aggregates. Individual review stays in the TAX-059 supervisor signal review, which is itself recorded.
- **Server enforcement.** Only supervisors of the selected tenant may read the review (the TAX-029 `ledger.read` action). Every other role is refused before anything is computed, and the refusal is recorded in the ledger as `ledger.read.denied`. Each successful read is recorded as a `ledger.read` entry with `detail.view = integrity_review`.
- **Honest gaps.** Reviewed policy-gap findings have no store in Frappe until the case findings record (TAX-086) lands, so the Frappe endpoint reports `policy_exposure` as `unavailable` with a `missing_data` note, never as zero. An unreadable ledger is reported the same way.
- **Contract.** `GET /api/v1/management/integrity` (`getIntegrityReview`) with `window_days` 7, 30 or 90. Schemas `IntegrityReview`, `PolicyExposure`, `IntegrityRow`, `IntegrityCombinedRow` and `IntegrityUnavailable`; examples 46 and 47.
- **Client and screen.** `apps/auditor-web/src/api/integrity.ts` re-checks the privacy contract: it refuses non-synthetic data, totals that do not add up, a combined row disguised as an ordinary row, any login-like text and any signal that names a subject. `IntegrityView.vue` (`/auditor/management/integrity`) is in the supervisor-only "Supervision" sidebar section and linked from the management dashboard. Every chart bar is decorative; the table beside it carries the figures and is the accessible fallback. Bars are hidden below the `sm` breakpoint so the counts stay visible at 390 px.

Alternatives and tradeoff:

- *Showing per-officer override counts* (as the TAX-059 signal review does) was rejected for this management view: it would turn an aggregate into a ranking of people.
- *Plain suppression without the complementary rule* was rejected: with one hidden cell, its value could be worked out from the total.
- *Differential privacy noise* was rejected for the prototype: the volumes are small, the audience is restricted, and exact figures are needed to reconcile with the ledger. Revisit for wider audiences.
- *Reporting zero policy gaps until TAX-086* was rejected: it would look like full coverage.

Official sources and version/date checked (2026-11-05): from this repository, ADR-015 (ledger, overrides and signals), ADR-020 (management summary), ADR-007 (roles and the `ledger.read` action), docs/DESIGN_STANDARD.md and docs/API_STANDARD.md, the TAX-052 legal-route registry, and the TAX-069 `challenge.reviewed` outcomes in `vati_tax.audit.ledger`. No external source was needed.

Verification performed:

- `backend/vati_tax/vati_tax/integrity/test_aggregate.py` (22 tests): small-cell combining, the complementary rule, insufficient volume, no identities, signals about the viewer withheld, wording without accusatory terms, policy exposure only for `no_verified_route`.
- `integrity/test_service.py` (7 tests): supervisors read and the read is recorded; auditor, specialist, service and taxpayer are refused before computing and the refusal is recorded; another tenant sees nothing; unavailable sources are not zero.
- `tests/contracts/test_integrity_review.py` (16 tests), and `contract.py` (47 examples).
- `apps/auditor-web/src/api/integrity.test.ts` (11 tests), and `e2e/integrity.spec.ts` (11 Edge tests: supervisor view, denied auditor and specialist, nav entry for supervisors only, insufficient volume, unavailable sources, a leaked login refused, keyboard focus, axe and no page-wide scroll at 1440, 1024 and 390).
- The TAX-023 harness includes the page at all three widths.
- Not run: a Frappe site verify (no DocType or permission changed; the endpoint reuses the TAX-059 store and policy), and live services.

Consequences and revisit trigger: TAX-086 should give reviewed findings a `gap_review` state and `reviewed_at` so the Frappe endpoint can pass them in. Revisit `min_group_size` with real volumes, if the audience widens beyond supervisors, or if a policy area or control has so few cases that it is always combined.
