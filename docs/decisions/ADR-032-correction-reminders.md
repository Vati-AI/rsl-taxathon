# ADR-032: Correction reminders from reviewed omissions

Status: proposed (TAX-078, pending review)
Owner and task: Kananelo (owner), TAX-078; implemented by kananelo-claude-20261105-078
Date: 2026-11-05

(ADR-028 to ADR-031 are taken on main or open branches, so this takes ADR-032.)

Context and smallest prototype need: TAX-078 asks the taxpayer portal to offer "taxpayer-specific correction reminders from reviewed prior omissions with clear explanation", with "no cross-taxpayer history leakage" and so that "withdrawn/incorrect reminders can be corrected". The building blocks already exist:
- TAX-044 (ADR-023) shows correction prompts before a claim is sent, from a fixed list of reconciliation codes.
- TAX-046 (ADR-013) records approved officer decisions on a taxpayer's case. These include `close_after_correction` and `request_further_evidence`, the two outcomes that record that the taxpayer had to correct or add something.
- TAX-069 (ADR-024) records objections and appeals, and whether a decision was overturned.

Nothing yet turns a reviewed omission into something the taxpayer sees next time.

Decision:

**What a reminder is.** A `Vati Correction Reminder` is one taxpayer-facing reminder. It carries `tenant_id` and `taxpayer_id`, so the TAX-029 wildcard hooks narrow it; it is taxpayer-owned.
- **Who records it.** An auditor or supervisor records it (`reminder.record`, human-only), always from one approved TAX-046 decision on the taxpayer's own case whose outcome is `close_after_correction` or `request_further_evidence`.
- **Topic.** One from a fixed list: `missing_invoices`, `period_figures`, `invoice_totals`, `payment_details`, `contract_documents`. Each topic names the TAX-044 prompt codes for the same kind of gap.
- **Explanation.** 20 to 1,000 characters, written for the taxpayer.
- **Uniqueness.** There is at most one reminder per decision and topic (`reminder_key`).
- **Free text and decision rationale are never shown.** The officer writes the explanation specifically for the taxpayer. The internal rationale stays internal.

**Correctable, never rewritten silently.** An auditor or supervisor can correct the topic or explanation, or withdraw the reminder (`reminder.correct`, human-only).
- Each change needs a 20 to 2,000-character reason and the current `expected_version`.
- The earlier wording, who changed it, when and why are appended to `history`.
- A withdrawn reminder never changes again; a new one is recorded instead.
- Every record, correction, withdrawal, staff read and refusal is sealed in the TAX-059 ledger under subject type `reminder`. The DocType controller also refuses changes to the source fields, version jumps, changes without a reason, and deletion.

**Hidden when its basis is in doubt.** A reminder is shown only while it is `active` and its decision stands.
- While any TAX-069 challenge to that decision is waiting for review, or after any review overturned or partly overturned it, the reminder is hidden. The staff view says why (`source_state`).
- No new reminder can be recorded from such a decision.
- A taxpayer who disagrees with the underlying decision therefore uses the TAX-069 objection, and the reminder disappears while it is reviewed. A wrong reminder on a sound decision is corrected or withdrawn by staff.

**What the taxpayer sees.**
- **The reminder list.** `vati_tax.portal.api.get_correction_reminders` returns the caller's own visible reminders: reminder ID, topic, title, explanation, matching prompt codes, and the dates noted and last updated.
  - It has no case or decision IDs, staff identities, history or status.
  - It shows one per topic, the newest wording first.
  - Queries are filtered by the caller's own (tenant, taxpayer) memberships. Taxpayers have no DocType permission and cannot use the staff method, `vati_tax.reminders.api.get_reminders`.
- **The pre-submission check.** `review_refund_claim` adds `reminder_id` to each TAX-044 prompt a visible reminder covers. Nothing else in the review changes.
- **In the portal.** The claim form shows a "From your earlier claims" panel on its first step, and an "Also noted on an earlier claim" label on matching prompts.
- **Guidance only.** Reminders never block sending, never change a claim or case, and say nothing about risk, penalties or fraud.

Alternatives and tradeoff:

- **Derive reminders automatically from past TAX-044 prompts or reconciliation results.** Rejected. Those are unreviewed draft checks, and the task asks for *reviewed* omissions. Showing them would let unreviewed output reach the taxpayer as history.
- **Show the decision rationale.** Rejected. It is staff text written for supervisors, and may cite internal findings.
- **Let the taxpayer edit or dismiss a reminder.** Not done. The reminder is a staff statement. The taxpayer disputes the decision behind it through TAX-069, or asks the officer to correct it. A taxpayer-side "report a problem" flow can follow if needed.
- **A staff screen in the auditor workspace.** Out of scope for this taxpayer-web task. Staff use the whitelisted methods; a screen can call `get_reminders`, `record_reminder` and `correct_reminder` later.

Official sources and version/date checked (2026-11-05):
- This repository's TAX-078 task, REQUIREMENTS.md ("Taxpayer service, correction and recurring errors"), PROPOSAL.md ("recognise recurring mistakes"), and ADR-001, ADR-007, ADR-013, ADR-015, ADR-023 and ADR-024.
- Frappe Framework v16.54.0 at the commit in `integration/frappe-framework.lock.json`, for the DocType, unique key and `has_permission` hook behaviour used here.
- No RSL rule on recurring errors exists in this repository, and none is claimed.

Verification performed: see the task evidence and the PR.
- `reminders/test_service.py`: Frappe-free tests with the real TAX-046 engine and TAX-069 service.
- `reminders/test_reminders_bench.py`: site-backed tests, run with `scripts/frappe_dev.py verify`.
- `apps/taxpayer-web/src/lib/reminders.test.ts`: Vitest.
- `tests/frontend/reminders.spec.ts`: Playwright at 1440, 1024 and 390 px.

Consequences and revisit trigger:
- The auditor workspace can add a staff screen over the staff methods.
- TAX-070 (outcome feedback) can count reminders per topic.
- Revisit:
  - if RSL wants reminders generated from structured findings rather than recorded by hand
  - if reminders should expire
  - if taxpayers should be able to report a wrong reminder from the portal
