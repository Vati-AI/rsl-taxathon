---
name: update-project-task
description: Maintain canonical TAX task status, acceptance evidence and GitHub issue/board mirrors.
---

# Update Project Task

The coding agent updates tasks; the human owner is not an instruction to delegate administration. Use `scripts/task.py status` for IN_PROGRESS/BLOCKED/IN_REVIEW and concrete notes; use `evidence TAX-XXX --criterion N --text ...` after observing the criterion. Commit status with the branch. `scripts/task.py sync TAX-XXX` mirrors current state to GitHub; report missing permissions without discarding local task state. DONE requires a merged implementation PR and all acceptance evidence. Epics remain planning records; do not claim or falsely complete them.
