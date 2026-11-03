# AI-managed task lifecycle

`tasks/TAX-XXX.json` is canonical. Human owner = reviewer/accountability; claimed_by = current AI implementer. The board is a mirror. Epics group scope but cannot be claimed. Dependency-ready leaf tasks appear in `task.py list --ready` even if their stored status is BACKLOG.

```sh
python3 scripts/task.py list --owner Leseli --ready
python3 scripts/task.py start TAX-020 --agent leseli-codex-session-1
# implement and run focused checks
python3 scripts/task.py evidence TAX-020 --criterion 1 --text 'Observed output and test command/result'
python3 scripts/task.py status TAX-020 BLOCKED --note 'Specific missing contract or failed upstream behavior'
# or commit implementation and finish:
python3 scripts/task.py finish TAX-020 --body-file /tmp/pr.md
```

Evidence criteria are one-based. Mark only observed satisfied criteria; a command existing is not proof it passed. Finish checks the repository, pushes a committed branch, creates/reuses the PR, records IN_REVIEW and synchronizes GitHub. Project sync uses the agent's existing gh login; no background LLM runner or shared model credential is installed. Opening an agent with the role prompt starts work; it does not need a separate hosted agent service.

Claiming checks canonical dependencies and reserves a unique remote ref with an empty expected SHA. A second agent cannot overwrite it. Each agent needs a separate clone/worktree. The claim tool switches main only in a clean checkout; do not use a worktree whose main branch is checked out elsewhere.

After a human merges, the next agent can use a follow-up branch to record DONE using `task.py status ... DONE --note ...`; it queries GitHub to verify the referenced PR is merged and checks recorded criteria/evidence. Commit and open a small follow-up PR. Until that update is merged, dependencies remain blocked on main. Do not use issue closure alone as proof of completion.

On task status changes the AI should run `task.py sync TAX-XXX`; start and finish do this automatically when a GitHub remote is configured. Without Projects scope, task files/branches still work and the command reports the unsynchronized board. A maintainer can rerun full sync from trusted main. The optional Actions workflow is a manual alternative and needs a dedicated token; none is silently copied from a developer's login.
