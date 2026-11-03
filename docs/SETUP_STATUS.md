# Coding workspace status — 2026-11-03

The expanded coding workspace was merged through PR #17. Agents can pull main and claim independent tasks. Frappe and frontend design additions are merged through PRs #73 and #85.

## Delivered

- Private Vati-AI/rsl-taxathon repository; seven role teams with write access; private Project 1.
- PROJECT.md, scope/architecture/API/data-model/requirements/decision docs, supplied product references and seven role onboarding prompts.
- Six canonical .agents skills plus Claude aliases: start-task, implement-change, fix-bug, update-project-task, finish-task, integrate-service.
- Original 16 planning epics, completed setup task TAX-017, follow-up setup TAX-083 and 65 product tasks TAX-018–082 across foundation/pilot/expansion/future.
- AI-owned claim/status/evidence/finish/sync commands with human review before merge.
- Both browser starter interfaces and five explicit synthetic scenario fixtures; localhost:58080.
- Official source checkouts for Jube, OpenAleph, yente, Docling Serve, Frappe Docker, FtM and LangGraph at immutable commits, reproducible using scripts/upstreams.py. No unnecessary forks.
- Vati PostgreSQL/Redis healthy on localhost:55433/56380. Isolated pinned Frappe framework/site and editable Vati app installed; migration, login and protected health checks passed (docs/FRAPPE_DEV.md).
- Installed/locked AI dependencies and a model-free LangGraph interrupt/resume smoke.

## Verified

Twelve tooling tests passed, including real local Git claim protection, ready-task filtering, five synthetic HTTP scenarios, denied file exposure and rejected fixture mutations. All six skills pass the skill-creator validator. All seven upstream checkout SHAs match the lock. Python compilation and Compose configurations pass. LangGraph smoke passed. Relevant checks also run in GitHub Actions.

## Remaining human/account prerequisites

- Confirm GitHub usernames for Leseli, Kananelo, Edga, Gupta, Jane and Yellow before invitations. Keletso is Kellybrackets.
- GitHub Free is the chosen v1 plan. Human review and CI are the workflow; server-enforced private branch protection is deliberately not a prerequisite.
- Optional unattended Actions board sync needs a dedicated Project token; none is copied from a developer login. Local agents can sync using their own authorized gh login.

## Implementation boundaries

Source checkout and a developer environment are not live integration completion. Service deployment/adapter tasks TAX-025–028 and TAX-034–039 still need their acceptance evidence. No real RSL credentials, model keys, taxpayer data or licensed watchlist data are required to start fixture coding. Do not claim that the whole tax investigation product is built.

Prototype v1 uses [the shared engineering standard](ENGINEERING_STANDARD.md) and [toolchain checks](TOOLCHAIN.md).

## V1 standards update (TAX-083)

Shared engineering/API/AI standards and ADR-002 define a consistent prototype stack and essential controls. Pinned Python/web quality tools run locally and in CI; the AI installation smoke also runs in CI. Agent finish now refreshes main and runs quality checks. Current first-party sources were normalized with the shared formatters. Product build/contracts/permission tests remain acceptance work for the relevant foundation tasks, not claimed as implemented by tooling.
