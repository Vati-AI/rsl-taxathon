---
name: finish-task
description: Verify a Vati task, push its branch and open a human-review PR with task status updates.
---

# Finish Task

Run focused behavior checks plus `python3 scripts/dev.py check`. Review the diff and commit intended implementation files. Write a PR body file using .github/pull_request_template.md with task, concrete change, observed tests and limitations. Run `python3 scripts/task.py finish TAX-XXX --body-file /absolute/path/pr.md`. It pushes the branch, opens/reuses a PR, records IN_REVIEW, commits that update and syncs the task. Check CI and report the PR. Do not merge your own PR or set DONE before human merge. If GitHub sync fails, the branch/task file stays authoritative and the failure must be reported.
