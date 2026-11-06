# ADR-038: Single-developer delivery of the remaining backlog

Status: accepted
Owner and task: Keletso; applies to every task (2026-11-06 amendment)
Date: 2026-11-05

Context and smallest prototype need:
Five people working on the backlog in parallel created more coordination work than delivery.
On 2026-11-05, 25 of the 27 open leaf tasks were waiting on another person's task, a review or a
status update. Examples:

- TAX-096 merged in #221 but stayed IN_REVIEW because one criterion required reviews from four
  owners. That alone held up 18 tasks.
- TAX-087, TAX-097 and TAX-105 had merged code but were not recorded as DONE, so the tasks
  depending on them still looked blocked.
- TAX-086 had not been started and held up 13 tasks.

Hosted GitHub Actions are also unavailable: the organisation has no remaining usage, so no job
starts. Reviews that waited on CI could never complete.

The remaining work is mostly integration across Frappe, Jube, OpenAleph, yente, Docling and the AI
runtime, and has to run on one machine with the services together. Keletso's machine (16 GB)
can run the stack.

Decision:

1. As explicitly requested on 2026-11-06, Keletso owns every task (leaf and epic,
   open and completed), with no exceptions. Existing agent claims are preserved;
   previous owners remain recorded in task notes and Git history. Each open leaf
   task has a `sequence` number. Work in that
   order: from `python3 scripts/task.py list --owner Keletso --ready`, take the lowest `sequence`.
   tasks/README.md lists the full order.
2. A PR does not need review from another person before it is merged or the task is marked DONE.
   PRs are still opened, so each change has a record and a diff. The owner merges their own PR
   after the local checks in docs/FREE_CI.md pass, and records the results in the PR because
   hosted CI cannot run.
3. After merging, the owner marks the task DONE straight away, in a follow-up commit or PR that
   they also merge. Code that has merged while its task is still open is the main cause of false
   blockage.
4. Acceptance criteria must not require named people to review. Removed in this change:
   - TAX-096 criterion 2: review by Gupta, Edga, Leseli and Kananelo
   - "human review" and "human usability review" in TAX-106 to TAX-109
   - "open a human-review PR" (now "open a PR") in every open task

   Visual and accessibility evidence (screenshots, keyboard checks, contrast) is still required.
   Approval of that evidence by a second person is optional.

5. Product safeguards are unchanged:
   - officer approval of consequential actions
   - interrupt and resume only after an authorised Frappe decision
   - segregation of duties, tenant isolation and synthetic-only data
   - the bans on AI assessments, refund decisions or accusations

   These are features under test, not team process.

Alternatives and tradeoff:

- Keep owners by area and chase reviews: rejected. It is the friction this ADR removes.
- Remove the PR step entirely and push to main: rejected. PRs cost little and keep a reviewable
  history.

Tradeoff: no second person checks the code before merge. Mitigations: local `scripts/dev.py check`
and `scripts/quality.py`, focused behaviour tests, and the measured acceptance in TAX-058 and
TAX-081.

Official sources and version/date checked:
Repository task graph on main at f012827 (2026-11-05), the GitHub PR list for #221, #225, #243,
#244 and #248, and the GitHub Actions billing block recorded in docs/FREE_CI.md.

Verification performed (or named implementation task):
`scripts/validate.py` passes with the new owners, criteria and sequence. TAX-096 was recorded
DONE after its merge was verified (#221, 2026-11-05T11:20:26Z). TAX-095 is linked to its merged
implementation PR (#248) but stays IN_PROGRESS: the cold-start rehearsal, memory measurement and
rate-limit observations have not been run.

Consequences and revisit trigger:
Former owners no longer hold any tasks. Previous owner names remain in task notes and Git history; all canonical owner fields are Keletso. Revisit
if more developers join the remaining work, or if hosted CI becomes available again. In that case,
reinstate CI as a required check before self-merge.
