# Coding workspace status — 2026-11-03

The expanded coding workspace was merged through PR #17. Agents can pull main and claim independent tasks. Final Frappe setup/verification and role ownership additions are in a follow-up setup PR for review.

## Delivered

- Private Vati-AI/rsl-taxathon repository; seven role teams with write access; private Project 1.
- PROJECT.md, scope/architecture/API/data-model/requirements/decision docs, supplied product references and seven role onboarding prompts.
- Six canonical .agents skills plus Claude aliases: start-task, implement-change, fix-bug, update-project-task, finish-task, integrate-service.
- Original 16 planning epics, setup task TAX-017 and 65 executable tasks TAX-018–082 across foundation/pilot/expansion/future.
- AI-owned claim/status/evidence/finish/sync commands with human review before merge.
- Both browser starter interfaces and five explicit synthetic scenario fixtures; localhost:58080.
- Official source checkouts for Jube, OpenAleph, yente, Docling Serve, Frappe Docker, FtM and LangGraph at immutable commits, reproducible using scripts/upstreams.py. No unnecessary forks.
- Vati PostgreSQL/Redis healthy on localhost:55433/56380. Isolated pinned Frappe framework/site and editable Vati app installed; migration, login and protected health checks passed (docs/FRAPPE_DEV.md).
- Installed/locked AI dependencies and a model-free LangGraph interrupt/resume smoke.

## Verified

Eleven tooling tests passed, including real local Git claim protection, ready-task filtering, five synthetic HTTP scenarios, denied file exposure and rejected fixture mutations. All six skills pass the skill-creator validator. All seven upstream checkout SHAs match the lock. Python compilation and Compose configurations pass. LangGraph smoke passed. Relevant checks also run in GitHub Actions.

## Remaining human/account prerequisites

- Confirm GitHub usernames for Leseli, Kananelo, Edga, Gupta, Jane and Yellow before invitations. Keletso is Kellybrackets.
- Review/merge the follow-up Frappe readiness PR; the core coding workspace is already merged via PR #17. Agents do not self-merge.
- GitHub rejected private-repo branch protection on the free organization plan. Intended policy is versioned but cannot be enforced until a supported paid plan is chosen. No purchase was made.
- Optional unattended Actions board sync needs a dedicated Project token; none is copied from a developer login. Local agents can sync using their own authorized gh login.

## Implementation boundaries

Source checkout and a developer environment are not live integration completion. Service deployment/adapter tasks TAX-025–028 and TAX-034–039 still need their acceptance evidence. No real RSL credentials, model keys, taxpayer data or licensed watchlist data are required to start fixture coding. Do not claim that the whole tax investigation product is built.
