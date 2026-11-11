# ADR-045: Two independent delivery lanes for Keletso and Gupta

Status: accepted
Owner and task: Keletso and Gupta; applies to every open leaf task (amends ADR-038 decision 1)
Date: 2026-11-11

Context and smallest prototype need:
Under ADR-038 Keletso owned and implemented every task. Gupta is now available to work on the
remaining backlog. ADR-038 records why ownership was centralised: when five people worked in
parallel, most open tasks waited on another person's task. A second developer must not bring that
back, so the split has to be made along the dependency graph, not by area.

On 2026-11-11 the open backlog was 61 BACKLOG leaf and epic tasks plus TAX-086, TAX-110, TAX-125
and TAX-144 (IN_REVIEW) and TAX-095 (IN_PROGRESS). A dependency analysis of the open leaf tasks
found two groups with no dependency between them once those five land, and six tasks that need
work from both groups.

Decision:

1. Precondition: TAX-086, TAX-110, TAX-125, TAX-144 and TAX-095 are merged and recorded DONE before
   either lane depends on them. Both lanes build on them.
2. Keletso owns the **AI and new UI lane** (23 tasks):
   - New UI design: TAX-145 → TAX-146 → TAX-150, TAX-147, TAX-148, TAX-149 → TAX-152
   - AI pipeline: TAX-088 → TAX-054 → TAX-057, TAX-063
   - Taxpayer assistant: TAX-098 → TAX-099; TAX-100 → TAX-101
   - AI scenarios and measurement: TAX-139, TAX-140, TAX-141, TAX-119, TAX-120, TAX-128
   - Portal: TAX-126, TAX-130
3. Gupta owns the **RSL integration lane** (23 tasks):
   - API and login: TAX-090 → TAX-091 → TAX-117 → TAX-136
   - RSL contracts: TAX-123 → TAX-133 → TAX-131, TAX-138; TAX-124 → TAX-132
   - Native tools: TAX-107 → TAX-134, TAX-108 → TAX-135, then TAX-106 and TAX-109
   - Demo: TAX-094 → TAX-143
   - Stand-alone: TAX-142, TAX-127, TAX-129, TAX-072, TAX-076
4. No task in one lane depends on an open task in the other lane. Each person works only from
   `python3 scripts/task.py list --owner NAME --ready`, lowest `sequence` first, and finishes their
   lane without waiting on the other person.
5. The **final wave** stays with Keletso and starts only after both lanes are finished, because
   each task needs work from both lanes:
   - TAX-093 (needs TAX-090 and TAX-088)
   - TAX-151 (needs TAX-107, TAX-108, TAX-091 and TAX-145)
   - TAX-137 (needs TAX-106, TAX-133, TAX-150 and TAX-151)
   - TAX-103 → TAX-058 → TAX-081 (final regression, demo and acceptance)
6. The new UI chain (TAX-145–152) stays in the AI lane although Gupta's team role is `ai`. The chain
   is self-contained, and keeping it with the AI tasks gives two lanes of 23 tasks. Moving it to
   the integration lane would give about 30 tasks against 16.
7. Epics and DONE tasks keep Keletso as owner. They are planning and historical records and are
   not claimed.
8. ADR-038 decisions 2 to 5 are unchanged: each owner merges their own PR after the docs/FREE_CI.md
   local checks, records DONE straight away, and no acceptance criterion requires a named
   reviewer. Product safeguards are unchanged.

Alternatives and tradeoff:

- Split by area (Gupta takes `ai`, Keletso the rest): rejected. TAX-088 and the assistant tasks
  feed TAX-093 and TAX-058, and the UI and RSL chains are interleaved, so both people would wait
  on each other.
- Keep a single developer: rejected; the second person is available.

Tradeoff: the two lanes touch some of the same files (for example the shared Frappe API and the
taxpayer portal). These are merge conflicts, not dependencies. `task.py refresh` merges current
main into each branch before finishing; resolve conflicts there and retest.

Official sources and version/date checked:
Canonical task files on main at f720925 (2026-11-11). The dependency analysis read `depends_on` from
every `tasks/TAX-*.json` file and ignored DONE tasks and epics.

Verification performed (or named implementation task):
A script checked that every open leaf task is in exactly one lane, the final wave or the five
landing tasks, and that no lane task depends on a task in the other lane or the final wave.
`scripts/validate.py` passes with the new owners.

Consequences and revisit trigger:
`task.py list --owner Gupta --ready` and `--owner Keletso --ready` now give two separate work
queues. Revisit if a new task is added that links the lanes (give it to the final wave or to the
lane whose tasks it depends on), or if one lane finishes much earlier than the other.
