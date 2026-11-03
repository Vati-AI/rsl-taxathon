# Development commands

Start with PROJECT.md and docs/ONBOARDING.md. Python 3.11+ is sufficient for the fixture environment and repository tooling.

```sh
python3 scripts/dev.py check
python3 scripts/dev.py serve
python3 scripts/task.py list --owner NAME --ready
python3 scripts/task.py start TAX-XXX --agent UNIQUE_AGENT_ID
```

Use docs/TASK_WORKFLOW.md for AI-owned status/evidence/PR updates. Use docs/SERVICE_SETUP.md for pinned upstream source, Frappe/Docling and live dependencies. All seven roles have independent initial tasks.

Root Compose starts integration PostgreSQL/Redis only. Frappe has its own optional isolated development Compose. UI fixture code has no package-manager dependencies; the AI environment uses ai/requirements.lock. Frontend/Frappe production build choices are finalized in their foundation tasks rather than hidden inside fixture tooling.

`docker compose stop` preserves data; `down` preserves volumes. Do not delete volumes unless deliberately resetting disposable synthetic data. Every agent uses a separate clean clone/worktree and task branch. Core workspace PRs #17, #73 and #85 are merged; start from current main.

No real RSL/taxpayer records or secrets in commits, prompts, issue bodies or logs. Run relevant tests plus the workspace check before PR. CI validates the task DAG, ownership, five fixtures, skills, script compilation and tooling tests. These checks do not claim unimplemented product acceptance.

Prototype v1 uses [the shared engineering standard](ENGINEERING_STANDARD.md) and [toolchain checks](TOOLCHAIN.md).
