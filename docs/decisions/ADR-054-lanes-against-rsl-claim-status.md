# ADR-054: Refund lanes are recommendations against RSL's claim status

Status: accepted
Owner and task: Gupta, TAX-132 (implements ADR-043 §1; refines ADR-042 and ADR-048)
Date: 2026-11-13

Context and smallest prototype need:
- **How lanes are proposed today.** The TAX-121 assurance step proposes a TAX-114 lane for every
  claim read from RSL's Online Refunds Application. It reads RSL's claim record but ignores the
  status RSL reports. That is one of the TAX-111 assumed codes: `processing`, `pending`,
  `audit_in_progress`, `processed`, `rejected` or `cancelled`.
- **How lanes reach RSL.** The TAX-124 outbound step sends the lane to RSL as a recommendation
  once an officer approves the decision. It does not look again at what RSL has done since.
- **What ADR-043 requires.** RSL keeps refund status, release and payment. A lane must
  therefore say which RSL status it was made against. It must also not be made for a claim
  RSL has already decided.

Decision:

1. **Recorded status.** `assurance.rules.rsl_claim_status` records RSL's status, as RSL's
   (`owner: RSL`), in every lane-proposal body (`rsl_claim_status`). The body records whether
   a status was reported and whether the claim is in RSL audit. `Vati Lane Proposal` gets a
   read-only `rsl_claim_status` column, which is `not_reported` when RSL's record has none.
   An unknown code is never shown raw.
2. **No lane for decided claims.** A claim RSL has `processed`, `rejected` or `cancelled` gets
   no lane. The checks and their findings still run, and the run summary says why
   (`lane_withheld: rsl_claim_<status>`). `validate_lane` refuses such a lane, and it refuses
   any `undisputed_status` other than `recommendation_for_refund_auditor`.
3. **Status at proposal sent with the recommendation.** The `lane_recommendation` contract
   (`rsl.online_refunds.lane_recommendation.v0-assumed`) gains
   `rsl_claim_status_at_proposal`. It is context for RSL, never a status Vati sets.
4. **Status re-read before queuing.** When the decision is approved,
   `rsl_outbound.store.rsl_claim_status_now` reads the status again. If RSL has decided the
   claim by then, only the findings pack is queued. An unread or unknown status withholds
   nothing.
5. **Wording.** The wording stays "recommendation" throughout. No Vati record or message has
   a status, release or payment field: the outbound `FORBIDDEN_FIELDS` and `validate_lane`
   refuse them.

Alternatives and tradeoff:

- Propose lanes only for `audit_in_progress`: rejected for now. RSL's `pending` and
  `processing` claims can still use a fast-lane recommendation, and the recorded status lets
  RSL filter them.
- Refuse delivery at the outbox when the status changed: rejected. It would show as a failed
  delivery. Not queuing the item is clearer, and it is logged.

Official sources and version/date checked:
None from RSL. The status codes are the TAX-111 assumptions (docs/reference/RSL_LANDSCAPE.md,
unverified).

Verification performed (or named implementation task):
- Frappe-free tests:
  - `assurance/test_rules.py` covers every status value, unknown codes, lanes refused for
    decided claims, and no status, release or payment field.
  - `rsl_outbound/test_rules.py` covers the recommendation withheld for decided claims and
    the status sent at proposal.
  - `tests/integration/test_rsl_sinks.py` covers the new contract field and status-setting
    fields refused.
- `rsl_intake/test_rsl_intake_bench.py` runs on a disposable site, across all six statuses
  and a missing one. It checks:
  - lanes recorded against `audit_in_progress`, `pending`, `processing` and an unreported
    status;
  - no lane for `processed`, `rejected` or `cancelled`;
  - the status re-read before queuing.

Consequences and revisit trigger:
- Revisit when RSL publishes its real claim statuses, or says which statuses it wants
  recommendations for.
