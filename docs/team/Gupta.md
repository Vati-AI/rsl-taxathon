# Gupta — ai

Paste this into your coding agent after opening the repository:

> Read PROJECT.md and AGENTS.md. I am Gupta; work only on my RSL integration lane (ADR-045). Use the repository start-task skill, list my dependency-ready leaf tasks, claim the next foundation/pilot task with your unique agent identity, implement and test it, maintain task status and evidence, push a branch and open a PR using finish-task. Do not merge. If blocked, record the concrete blocker and continue only with another eligible task in my scope. Do not ask me to manually update task files.

Primary path: `ai`. Cross-boundary contracts are coordinated with Keletso; human owners remain accountable for review.

Lane: RSL integration lane (ADR-045). `python3 scripts/task.py list --owner Gupta --ready` lists only your lane; no task in it waits on Keletso's lane.
