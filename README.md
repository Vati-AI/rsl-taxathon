# Vati AI — RSL Taxathon

Private team monorepo for Challenge 2: evidence-backed tax investigations with human decisions.

**Status:** project scaffold and developer tooling. Product UI, case APIs and live integrations are assigned tasks, not completed features. No real taxpayer data belongs in this repository.

## Start here

1. Read [scope](PROJECT_SCOPE.md), [architecture](docs/ARCHITECTURE.md) and [agent instructions](AGENTS.md).
2. Find your task in [the task index](tasks/README.md).
3. Run `python3 scripts/validate.py` (Python 3.11+; no packages required).
4. Run `python3 scripts/dev_env.py`, then `docker compose up -d --wait` for local PostgreSQL and Redis.
5. Read [development](docs/DEVELOPMENT.md) and [integration contracts](docs/INTEGRATIONS.md).

| Area | Owner |
|---|---|
| Auditor frontend | Leseli |
| Taxpayer frontend | Kananelo |
| Backend | Edga |
| AI / LangGraph | Gupta |
| Frontend QA and fixes | Jane |
| AI/data QA and testing | Yellow |
| APIs / integrations | Keletso |

Folders: `apps/auditor-web`, `apps/taxpayer-web`, `backend`, `ai`, `integration`, `data`, `tests`, `docs`, `tasks`.

One task → one branch → one PR → human review. `main` is read-only for normal agent work.

[GitHub setup](docs/GITHUB_SETUP.md) explains publishing, teams, board sync and enforcement limits.
