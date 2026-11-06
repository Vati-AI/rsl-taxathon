# ADR-042: Run the assurance checks as a pipeline step and record their results

Status: accepted
Owner and task: Keletso; TAX-121 (builds on TAX-033, TAX-039, TAX-086, TAX-087, TAX-111 to TAX-118)
Date: 2026-11-06

Context and smallest prototype need:
- TAX-118 measured the TAX-112-116 checks by calling `assess_taxpayer` directly, outside the
  services. Nothing in the platform ran them, stored their results or proposed a lane.
- TAX-086 stores findings, but only for a Jube rule, a LangGraph run or an officer. The checks
  are none of these: they are deterministic Vati rules over RSL source records.
- The checks cite RSL source records (e-Tax returns, Online Refunds Application claims and
  lines), which are not `Vati Evidence` documents, and the taxpayer's own books, bank file,
  agreements and permits, which are.
- TAX-114 lanes (lane, service target and due date, undisputed amount) had no record at all.
- Jube, OpenAleph and yente are often not running in development. TAX-033 delivers a case's
  events strictly in order, so a step queued behind `case.registered` waits for Jube.

Decision:

**Producer `vati_check`.** A finding from these checks has producer `vati_check`, added to the
contract `Finding.producer` and `FindingSnapshot.producer` enums, `findings.rules.PRODUCERS`,
`REVIEWABLE_PRODUCERS` and the `Vati Case Finding` options. It is neither a Jube rule nor an
agent run. Like a Jube rule it names its check in `rule`:
- `rule_id`: the finding code;
- `rule_version`: the tool's version;
- `ruleset_id`: `vati.assurance`;
- `ruleset_version`: the TAX-118 pipeline version;
- `input_digest`: a digest of what it read.

So officer feedback reaches the TAX-070 loop as a `rule` component. It carries no
`AnalysisProvenance`.

**Two optional Finding fields** (contract, rules, DocType):
- `source_refs`: `RslSourceRef` items (system, record ID, contract ID, record SHA-256, optional
  line locator). They are ADR-040 references, never copies.
- `candidate_route`: the TAX-116 `route_for` result (route status, candidate rule IDs and
  instruments, or policy-gap IDs, register version). `eligible_for_assessment` is always false.

The record refuses a policy-gap route unless `legal_route.status` is `no_verified_route`, and
refuses any other route unless it is `unverified`. `legal_route.citation` stays null.
- The contract reserves the citation for an authoritative citation from a qualified reviewer.
- TAX-086 refuses one at recording.
- So the candidate instruments go in `candidate_route`, not in the citation text.

Taxpayer records are cited as `EvidenceRef`s to the stored records document, with the record ID
as locator.

**Mapping** (`assurance/rules.py`, pure):

| TAX-118 issue | Category | Kind | Proposed action |
|---|---|---|---|
| fake_invoices | refund_reconciliation | missing_evidence | request_evidence |
| untaxed_imported_services | imported_services | unverified_evidence | request_evidence |
| disguised_employment | worker_classification | contradictory_evidence | specialist_review |
| related_party_loss | related_party | observed_fact | specialist_review |
| policy_gap | policy_gap | observed_fact | policy_gap_review |

- The explanation is the check's own reason. If that reason uses an ADR-026 labelling word, a
  neutral template replaces it.
- The unresolved amount is the amount the check computed. Alternatives are the explanations
  the check reports it tested.
- Every action needs human approval.
- A deterministic check over 24 months cites more records than a playbook item, so the
  per-finding cap on evidence references is 500 (was 100).

**Lane proposals: a new append-only `Vati Lane Proposal`.** There is one per case and
Online Refunds Application claim. It holds:
- the lane, reasons and service target with its due date;
- the claimed, undisputed and disputed amounts, as decimal strings;
- the inputs used, the line matches and the authenticity indicator.

The same claim, check version and inputs are a replay. Changed inputs add a revision that
supersedes the previous one.

Findings records were not used for lanes:
- a lane is not a finding (it has no kind or legal route);
- the scorer and officers need it per claim.

A proposal records no decision, and an officer reviews every lane.

**Run record: a new `Vati Assurance Run`** per outbox event. It holds:
- the status: `completed`, `degraded`, `not_run` or `superseded`, with a reason code;
- the status of each check;
- which services were `live`, `recorded` or `unavailable`, and what the step did not use;
- the counts the checks computed, the IDs it recorded and the time taken.

**Trigger: `assurance.requested`.** This is a new contract `EventEnvelope.type`, so it departs
from ADR-037's "no new types": no existing type means "run the checks".
- `assurance.store.on_submission_sent` is first in the `Vati Submission.on_update` list. When a
  taxpayer sends a refund claim, it registers the case or replays its registration, using the
  TAX-039 bridge's own registration key.
- It then enqueues the event as the case's first event.
- It passes its correlation ID to the bridge through `doc.flags`, so `case.registered` (and the
  TAX-087 evidence events) share one ID.
- The checks therefore never wait for Jube.
- They never call an upstream that can hang, so they hold `case.registered` back only while
  they run. The exception is a missing service identity, which retries.
- A resend after `correction_needed` queues its checks behind the case's earlier steps (TAX-033
  order). With Jube down, a re-check waits for the first Jube step. The dependency is explicit
  here and in the bench test.

**Handler inputs:**
- *RSL sources:* site key `vati_rsl_sources` (`fixture_path`, `vati_tenant_id`, optional
  `unavailable`) loads a synthetic `vati.rsl_sources_fixture.v1` file into the TAX-111
  `FixtureRslSources` (`integrations.config.rsl_sources`). It is its own key because
  `frappe_dev.py init` rewrites `vati_services`. The mode is always `recorded`, since there is
  no live RSL system.
- *Taxpayer records:* one CSV `Vati Evidence` document, `financial_statement`, referencing the
  `Vati Taxpayer`. It is re-hashed before it is parsed.
  - It holds the taxpayer's books, bank file, agreements, permits, certificates, loss
    explanations and invoice copies, one JSON record per row.
  - It is the TAX-032 upload path, because evidence accepts no JSON.
  - It is not linked to the cases, so no Docling extraction runs for it.
- *Claim:* the Frappe claim is matched to its Online Refunds Application record by TIN and
  period. The claimed amounts must agree.

**What is never computed from missing data.** Each of these records `not_run` with a reason
code and delivers:
- missing or unusable fixture configuration (`upstream_unavailable`);
- e-Tax or the Online Refunds Application not answering;
- a missing records document;
- no matching claim;
- an amount mismatch;
- missing check code.

An optional system that does not answer (IDMS, the RSL score, ASYCUDA, Central Bank, registry)
lets the checks run and marks each check that read it `degraded_<system>_unavailable`.

**Identity and boundary:**
- Findings and lanes are recorded as the TAX-087 pipeline service identity (`draft.record`).
- Reads use `case.read`, so taxpayers never see them.
- `assurance/checks.py` is the step's only import from `ai/`, like `portal/presubmission.py`
  for TAX-044. The portal boundary test lists both.
- Nothing decides, pays, denies or assesses.

**Live benchmark mode:** `python3 -m ai.evaluation.assurance_live run` (host).
1. It regenerates the sealed TAX-118 world and key with `_load_sealed()`.
2. It writes the RSL records (no submissions) as the fixture file, and a seed of the test
   taxpayers' own records and claims. The answer key is not written.
3. It creates a disposable `tax121-live-<UTC stamp>.localhost` site and runs
   `vati_tax.assurance.live_bench.run` on it. That seeds, drains the outbox and reads every
   case back as an auditor.
4. It scores the read-back with the unchanged TAX-118 scorer, next to a standalone run of the
   same taxpayers, and writes `docs/evidence/TAX-121/`.
5. It drops the site.

Alternatives and tradeoff:
- **Producer `jube_rule`:** rejected. Jube does not run these checks, and the label would
  mislead the TAX-070 loop.
- **Producer `langgraph_run`:** rejected. No model or graph runs, and it would need fake
  provenance.
- **Run the checks after `case.registered`, or from its handler:** this would make every
  result depend on Jube being up. It was rejected for the first send and kept only for
  resends.
- **A separate ordering stream per step in the outbox:** this would change TAX-033's
  guarantees and the outbox DocType. The ordering above keeps them.
- **Lanes as findings:** rejected (see above).
- **Copying RSL records into Frappe:** this breaks ADR-040. References and hashes suffice.
- **Candidate instruments as citation text:** this contradicts the contract's meaning of
  `citation` and TAX-086's refusal.

Official sources and version/date checked:
None external. This repository's contracts and code (2026-11-06):
- `integration/api/openapi.json` (`Finding`, `LegalRoute`, `EventEnvelope`);
- `integration/rsl_sources` (TAX-111 fixture adapter);
- `ai/evaluation/assurance_pipeline.py` and `assurance_benchmark.py`, unchanged (rule
  fingerprint equal to the TAX-118 freeze);
- the pinned Frappe bench (`doc_events` lists run in order).

Verification performed (or named implementation task):
- `vati_tax/assurance/test_rules.py`: Frappe-free, memory repositories.
- `tests/ai/test_assurance_live.py`: the handler's steps over memory repositories and the real
  fixture factory. The platform read-back scores exactly like the standalone run on a small
  population, and replays and unavailable sources are covered.
- `vati_tax/assurance/test_assurance_bench.py`: on a disposable site.
- The live benchmark (`docs/evidence/TAX-121/RESULTS.md`): 200 test taxpayers and 4185 claims went through a disposable site. Every case recorded a `completed` run and a lane proposal. Scored with the TAX-118 scorer, the read-back gives the same numbers as the standalone run and the committed TAX-118 results, with 0 per-claim differences; only the time differs, for the reasons stated there.

Consequences and revisit trigger:
- Every sent refund claim now has an `assurance.requested` event before `case.registered`. The
  TAX-039 bench tests count it.
- Sites that should assess claims need `vati_rsl_sources` and the pipeline service user.
  Without them, every case records `not_run`.
- Revisit when:
  - RSL supplies real source APIs (replace the fixture factory; the mode becomes `live`);
  - a reviewer workflow verifies routes;
  - resend re-checks must not wait for Jube.
