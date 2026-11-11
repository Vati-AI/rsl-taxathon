# ADR-047: RSL audit referrals open Vati cases

Status: accepted
Owner and task: Gupta, TAX-123 (implements ADR-043 §1 and §6 for audit referrals)
Date: 2026-11-11

Context and smallest prototype need:
ADR-043 decided that RSL selects and Vati investigates. Cases start from an RSL source: an
Online Refunds claim (TAX-122) or a referral from RSL's risk-based audit system on PSRM
(TAX-123). RSL's risk sets and scores are RSL's inputs, never a competing Vati score. We have
no RSL access or specification for referrals. The prototype needs an explicit assumed
contract, a fixture that says it is one, and a way for a referral to open exactly one Vati
case.

Decision:

1. **Assumed contract `rsl.audit_system.referral.v0-assumed`** in the TAX-111 contracts
   (`integration/rsl_sources/contracts.py`). It has these fields:
   - `audit_case_ref`: RSL's audit case reference, the record ID;
   - `tin`, `referred_on` and `selection_reason` (an RSL code);
   - `period` (optional, the VAT period under audit);
   - `risk_sets`: each with `risk_set_id`, `risk_set_version`, `score` and `owner`, and
     `owner` must be `RSL`.

   Like every TAX-111 contract, it claims no real host, path or field name. The contract
   refuses unexpected fields, floats and a score of any other owner.
2. **Synthetic fixture** `integration/rsl_sources/fixtures/audit_referrals.json` in the site
   fixture format (`vati.rsl_sources_fixture.v1`, `fixture: true`, `synthetic: true`, with a
   description saying it is assumed). It holds three referrals for two taxpayers: one with two
   risk sets, one with none (a team leader's selection) and one with a period. The TAX-111
   adapter serves it in envelopes that carry `fixture: true` and the contract notice.
3. **One case per audit case reference.** `vati_tax.rsl_referrals` reads each Vati taxpayer's
   referrals through the site's RSL source (`factories.rsl_sources`) and runs as the pipeline
   service identity. For each new referral it registers a case with the scoped key
   `rsl:audit-referral:<audit_case_ref>` (`ids.mapping.register_case`, TAX-030) and records one
   append-only `Vati RSL Referral` linked to it.
   - Reading the same record again replays and writes nothing.
   - A different record under the same reference is a conflict and changes nothing. This
     follows TAX-122.
   - RSL not answering is `unavailable`, never "no referrals".
   - An operator runs the intake (`api.ingest_audit_referrals`, or `bench execute
     vati_tax.rsl_referrals.api.run`).
4. **RSL scores stay RSL's.** `Vati RSL Referral.rsl_inputs` holds the risk sets with
   `owner: RSL`, their IDs and versions and the selection reason, plus a note that they are
   not a Vati score, finding or decision. Both the rules and the DocType controller refuse
   inputs of any other owner. A referral writes no finding, lane, score or decision.
5. **References, not copies.** Vati keeps the audit case reference, the contract, the record
   SHA-256, the retrieval time and the adapter. Officers read the referral behind a case with
   `case_referrals` (`case.read`). Taxpayers have no permission on the DocType.

Alternatives and tradeoff:

- Store the referral on `Vati Case Identity`: rejected. The case identity is the TAX-030
  mapping record and has no place for RSL inputs. A separate append-only record keeps the
  source reference and its hash together.
- Record RSL scores as findings or a lane: rejected by ADR-043 §6 and ADR-042. A score is RSL's
  input, not a Vati result.
- Update a referral when RSL changes the record: deferred. Like TAX-122, a changed record is a
  reported conflict until RSL defines how referrals change.

Official sources and version/date checked:
None from RSL; see ADR-043 and docs/reference/RSL_LANDSCAPE.md (unverified public
descriptions of the risk-based audit system on PSRM, 2026-11-07). Every field is an
assumption.

Verification performed (or named implementation task):
- `tests/integration/test_rsl_sources.py` covers the contract: RSL-owned scores, refused
  owners, scores, fields and periods.
- `backend/vati_tax/vati_tax/rsl_referrals/test_rules.py` (Frappe-free) reads the fixture
  file through the TAX-111 adapter and checks it against the contract. It also covers the
  plan, refusal reasons, replay and conflict.
- `test_rsl_referrals_bench.py` runs on a disposable site. It checks one linked case per
  reference, idempotent replay that reuses the case, conflict, unavailable and unconfigured
  sources, officer reads, tenant and taxpayer refusals, append-only records and the
  operator-only intake.

Consequences and revisit trigger:
- A referral case has a taxpayer but no Vati submission. The live API (TAX-090, ADR-046) lists
  only cases the contract `Case` can represent, so referral cases appear in the auditor queue
  once `Case.submission_id` is made nullable. That needs a contract change. The selection
  reason can then become a `selection_reasons` entry labelled as RSL's (TAX-131, TAX-138).
- Jube is not told about a referral case yet. Creating the Jube investigation sub-case for a
  referral belongs to TAX-131 and TAX-134.
- Revisit the contract when RSL supplies a referral format (ADR-043 open question 3).
