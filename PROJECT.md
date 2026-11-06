# Vati AI RSL Taxathon — project entry point

Build an API-connected tax intelligence platform for RSL Challenge 2. Read this file, AGENTS.md and your task before coding. The supplied November 2026 proposal and technical summary are the scope baseline; legal claims in them require independent RSL validation before product use.

## Start coding

```sh
python3 scripts/dev.py check
python3 scripts/dev.py serve
# in another terminal, or in your agent chat:
python3 scripts/task.py list --owner Keletso --ready
```

The fixture server runs on http://127.0.0.1:58080 and serves both starter interfaces and synthetic contract data. It requires no API keys, paid model, upstream deployment or Python packages. It is explicitly fixture mode, not a tax system or authentication layer.

For live infrastructure: `python3 scripts/dev_env.py`, then `docker compose up -d --wait`. For pinned upstream source: `python3 scripts/upstreams.py fetch --all`. Read `docs/SERVICE_SETUP.md` before starting vendor stacks.

## What lives where

Keletso owns all tasks and areas as of 2026-11-06. Previous ownership remains in task notes and Git history.

- `apps/auditor-web`: officer-facing frontend.
- `apps/taxpayer-web`: portal UX assets; `backend/vati_tax` supplies the Frappe application, forms and permissions.
- `backend/vati_tax`: custom Frappe app scaffold; `backend`: shared tax-domain and gateway contracts.
- `integration`: API adapters, service configuration, upstream lock. Keletso owns cross-service coordination.
- `ai`: Vati LangGraph workflows, approved tools and prompts.
- `tests/frontend`: frontend QA. `tests/ai`: AI scenarios, synthetic fixtures and data evaluation.
- `tasks`: canonical task state. `.agents/skills`: reusable agent workflows.

Jube owns operational cases, assignment/status/escalation/risk execution. Frappe owns application identity/permissions, submissions/evidence requests and approved decision records. The Vati API coordinates these authorities; it must not create a second independent operational case state machine.

## Read in order

1. `AGENTS.md` — agent behavior and task lifecycle.
2. `PROJECT_SCOPE.md`, `docs/REQUIREMENTS.md` — product scope and coverage.
3. `docs/ARCHITECTURE.md`, `docs/API_CONTRACTS.md`, `docs/DATA_MODEL.md` — boundaries.
4. `docs/ONBOARDING.md`, `docs/DEVELOPMENT.md` — commands and verification.
5. `tasks/TAX-XXX.json` — the assigned unit of work.

Foundation and pilot tasks build the five-scenario vertical slice. Expansion and future tasks describe the broader national platform; they are deliberately not claimed as already implemented. Keletso implements and merges the remaining work after local checks (ADR-038); humans still approve consequential investigation actions. Agents implement, test and keep task files/issues current.

Frontend implementation and review must follow [the shared design standard](docs/DESIGN_STANDARD.md). Leseli and Kananelo establish representative screen baselines; Kananelo owns frontend QA (visual consistency and accessibility checks).

Shared implementation rules: [engineering](docs/ENGINEERING_STANDARD.md), [API](docs/API_STANDARD.md), [AI](docs/AI_STANDARD.md), [v1 acceptance](docs/V1_ACCEPTANCE.md), [tool installation](docs/TOOLCHAIN.md). GitHub Free and proportional prototype safeguards are deliberate decisions (ADR-002).
