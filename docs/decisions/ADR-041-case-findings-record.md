# ADR-041: Case findings record with revisions and append-only officer feedback

Status: accepted
Owner and task: Keletso; TAX-086 (builds on TAX-029, TAX-035, TAX-046, TAX-096)
Date: 2026-11-06

Context and smallest prototype need:
Jube rule results (TAX-035) and playbook output (TAX-049) were produced but never stored per
case, so the officer UI, decisions and the TAX-070 feedback loop had nothing durable to read.
The TAX-096 contract fixes the `Finding`, `AnalysisProvenance` and `FindingFeedback` shapes
but not how records are kept, keyed or authorised in Frappe.

Decision:
- Two new DocTypes, `Vati Case Finding` and `Vati Finding Feedback`, written only by
  `vati_tax.findings.store` with `ignore_permissions` after the engine checks the TAX-029
  action. Both carry `tenant_id` and `case_id` and deliberately no `taxpayer_id`, so the
  wildcard tenant hooks never treat them as taxpayer-owned; taxpayers also have no DocPerm.
- Recording a rule or agent finding uses the existing `draft.record` action (agent output for
  human review: staff or service). An officer finding uses `finding.review`. Reading uses
  `case.read`; feedback uses `finding.review`. No new policy action was added.
- A finding is never edited. Each one belongs to a lineage (tenant, case, producer, rule or
  playbook ID, subject). When a producer reports something different about the same subject,
  the next revision is appended with `supersedes_finding_id`. The unique `revision_key`
  (lineage + revision) stops two writers taking the same revision. Reads return current
  revisions, or every revision on request.
- The idempotency key is scoped to (tenant, case). Rule signals derive it from the rule,
  ruleset and input digest, and playbook items from the playbook, run and item. Same key and
  body returns the stored record; a changed body is `idempotency_conflict`.
- Rule signals and playbook results cite source records (`INV-SYN-002-01`). The caller maps
  each one to a `Vati Evidence` ID, and the stored `EvidenceRef` takes the document hash from
  that record, with the source ID as its locator. An unmapped citation is refused, not dropped.
- `langgraph_run` findings must carry a valid `AnalysisProvenance` and their TAX-061 registry
  stamp (name, version, content digest, registry version). Rule and officer findings carry
  none. Provenance is checked by a dependency-free validator in the app, which a test keeps in
  agreement with `integration/api/contract.py`. The app does not import `integration/`.
- Feedback stores the session user and role, links the same officer's previous feedback, and
  keeps a snapshot of the judged finding. `changes_finding` is always false. A unique
  `chain_key` allows one successor per earlier feedback. `feedback_for_review` returns each
  item with its TAX-070 provenance entry (`rule` or `playbook`).
- Finding text, alternatives and corrected wording refuse the ADR-026 labelling words. A legal
  route can only be recorded `unverified` or `no_verified_route`.

Alternatives and tradeoff:
- One finding per playbook proposal was simpler, but it would mix facts, gaps and
  discrepancies. The contract's `kind` keeps them apart, so each result item is its own
  finding.
- Editing a finding in place would make feedback snapshots and the TAX-070 lineage ambiguous.
  Revisions cost one extra row per change.
- `FeedbackActor.officer_id` is a UUID, but Frappe identifies officers by user name. The record
  stores the user, and TAX-090 maps it to the contract `officer_id` (`engine.contract_feedback`)
  rather than this task inventing an identifier scheme.

Official sources and version/date checked:
Pinned Frappe bench image in `scripts/frappe_dev.py` (the DocType JSON follows the existing
`Vati Decision` and `Vati Screening Result` conventions); `integration/api/openapi.json`
schemas `Finding`, `FindingFeedback`, `FindingSnapshot`, `AnalysisProvenance` (2026-11-06).

Verification performed (or named implementation task):
`vati_tax/findings/test_engine.py` (memory repository), `tests/integration/test_case_findings.py`
(live TAX-035 rows and TAX-049 review for SYN-002, contract checker, TAX-070 parser) and
`vati_tax/findings/test_findings_bench.py` on a disposable site through
`scripts/frappe_dev.py verify`. TAX-090 serves the records over `/api/v1`.

Consequences and revisit trigger:
Revisit when TAX-090 maps officer IDs, when a reviewer workflow verifies legal routes (a
separate reviewed record, not an edit), or when a second playbook needs result translation
beyond TAX-049.
