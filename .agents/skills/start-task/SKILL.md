---
name: start-task
description: Claim an eligible RSL Taxathon coding task and prepare an isolated task branch.
---

# Start Task

Read PROJECT.md and docs/TASK_WORKFLOW.md. Run `python3 scripts/task.py list --owner NAME --ready`. Choose a leaf task in foundation/pilot unless the user chooses another phase. Read its scope, inputs, acceptance and dependencies. Require a clean tree and a unique agent ID. Run `python3 scripts/task.py start TAX-XXX --agent ID`; it reserves a remote branch atomically. Stop on a failed/existing claim; never overwrite it. Keep the human owner unchanged. Open docs/API_CONTRACTS.md before integration changes.
