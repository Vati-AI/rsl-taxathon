# ADR-023: Learned-workflow approval, activation history and rollback

Status: proposed (TAX-065, pending review)
Owner and task: Edga (owner), TAX-065; implemented by edga-claude-code-20261105-065
Date: 2026-11-05

Context and smallest prototype need: TAX-064 (`ai/workflows/workflow_observation.py`) proposes versioned workflows mined from approved officer actions, always `status: proposed` and `auto_activate: false`, and its `activate()` refuses: "activation needs supervisor `playbook.approve`, a reviewed TAX-061 registry entry and a rollback plan". ADR-017 says learned versions "must enter as proposed and can never self-approve", and that a later task can source approvals from `playbook.approve` records. docs/AI_STANDARD.md and ARCHITECTURE.md require supervisor review, holdout evaluation and rollback before a new playbook is activated. Under ADR-001, Frappe owns approved human decision records; LangGraph executes. Nothing yet stored those decisions.

Decision:

- New package `vati_tax.workflows` (Frappe-free `rules.py` and `service.py`, Frappe `store.py`, whitelisted `api.py`), with two DocTypes. `Vati Workflow Version` holds one version of a named workflow (`workflow`@`version`, semver; `version_key` unique per tenant). `Vati Workflow Activation` is append-only history (`activation_key` unique per tenant, workflow and sequence).
- **Intake.** `propose_workflow` takes a TAX-064 `workflow_proposal` unchanged. It must still be unapproved and inactive (`status: proposed`, `auto_activate: false`, no `approved_by`). Its steps must be 2 to 6 from the set TAX-064 can emit: `request_evidence` plus the TAX-046 decision outcomes. It must carry its held-out evaluation. The version keeps the proposal ID, observer version, the SHA-256 of the whole proposal and the evaluation as submitted. The new `workflow.propose` action allows staff and the observer's service identity, because recording a proposal approves nothing. Re-sending the same content replays; different content for the same version is `idempotency_conflict`.
- **Supervisor decision.** Approval and rejection use the existing human-only `playbook.approve`, now in `SEPARATION_OF_DUTIES`: whoever recorded a version cannot decide it. Both need a reason (20 to 2,000 characters) and `expected_version`. Approval also needs `holdout_reviewed: true`, an explicit statement that the held-out comparison was reviewed. Status moves only `proposed → approved | rejected`.
- **Activation history and rollback references.** The new human-only `workflow.activate` (supervisor) puts an approved version in force by appending an entry. It records `replaced_version` (what was in force) and `rollback_to` (the approved version a rollback of this entry restores). `expected_seq` must match the current head, so two supervisors cannot both act on the same view. `rollback_workflow` appends a `rollback` entry that restores `rollback_to`, provided that version is still approved. It carries forward that version's own `rollback_to`, so repeated rollbacks walk back through history (v3 → v2 → v1) instead of flipping between two versions. Nothing is ever edited or deleted: the version that was rolled back stays approved and visible.
- **Execution gate.** `check_execution` (new `workflow.read`, staff and service) permits only the version in force. A proposed or rejected version (`unapproved_version`), an approved version not in force (`not_active`) and an unknown version (`unknown_version`) are refused. Every refusal is recorded. A permitted check returns the steps and states that each step still needs its own officer approval: a learned workflow orders approved actions and never performs them.
- **Audit.** Every step and refusal is sealed into the TAX-059 ledger under the new subject type `workflow` (also added to `Vati Audit Event`'s Select options). `workflow.proposed` is a write; `workflow.approved`, `workflow.rejected`, `workflow.activated` and `workflow.rolled_back` are approvals; refusals are `<action>.denied`. Ledger subject IDs are deterministic UUIDv5s of (tenant, workflow[, version]), because the ledger column is UUID-sized. Refused self-approvals (`playbook.approve.denied`) now count towards the existing `repeated_separation_of_duties_attempts` signal. These new actions are not TAX-064 steps, so the observer never learns from its own approvals.

Alternatives and tradeoff:

- Write approvals into the TAX-061 JSON registry: that registry binds approvals to reviewed source digests through PR review, which suits code-defined playbooks. A learned workflow has no new source to digest, so its approval is a runtime human decision that belongs in Frappe (ADR-001). The registry stays the gate for code playbooks; this is the gate for learned step sequences.
- Mutable "active" flag on the version row: simpler, but it loses who activated what, when and why, and offers no safe rollback target. An append-only history keeps both.
- Rollback to "the version replaced": a second rollback would flip back. Carrying `rollback_to` forward walks back through history.

Official sources and version/date checked (2026-11-05): repository sources only. These are `ai/workflows/workflow_observation.py` and its README entry (TAX-064 proposal shape and step names), ADR-001, ADR-007, ADR-013, ADR-015, ADR-017, docs/AI_STANDARD.md, docs/ARCHITECTURE.md, and Frappe Framework v16.54.0 at the commit in `integration/frappe-framework.lock.json` (same pinned source as ADR-011/013/015/016).

Verification performed: 20 Frappe-free tests in `workflows/test_service.py` cover:

- intake, including real TAX-064 `propose_workflows` output (run from the repository root, where `ai` is importable), and eight refused malformed, self-approved or active proposals and unknown steps;
- a refused and recorded taxpayer proposal, replay and conflict;
- approval by a different supervisor; self-approval refused, recorded and signalled; officers and service refused; holdout acknowledgement and reasons; stale and decided versions;
- refused activation of unapproved versions; activation never by the service; rollback references; stale sequence;
- execution only of the active approved version, with refusals recorded;
- rollback restoring the previous approved version, and repeated rollbacks walking back;
- tenant isolation and an intact ledger.

Removing separation of duties, the approved-only or active-only execution check, or the rollback target each makes the suite fail. Site-backed tests are in `workflows/test_workflows_bench.py`; see the PR for the `scripts/frappe_dev.py verify` result.

Consequences and revisit trigger: the LangGraph worker must call `check_workflow_execution` before running any learned workflow, and must still pause for officer approval at every consequential step (TAX-047). TAX-077 can add workflow overrides to its integrity views (`override.playbook_step`). Revisit when learned workflows become executable LangGraph playbooks (bind the approval to a TAX-061 registry entry and its digest), or when activation needs a holdout threshold rather than a recorded acknowledgement (TAX-056/066).
