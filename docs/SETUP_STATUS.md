# Setup handover — 2026-11-03

## Live GitHub resources

- Organization: https://github.com/Vati-AI — free plan; Kellybrackets is owner.
- Private repository: https://github.com/Vati-AI/rsl-taxathon
- Private Project: https://github.com/orgs/Vati-AI/projects/1 — saved Board view grouped by Status.
- TAX-001 through TAX-016 have issue mirrors and Project items. TAX-017 tracks this handover review.
- Seven role teams exist with write access to this repository: auditor-frontend, taxpayer-frontend, backend, ai, frontend-qa, ai-data-qa, integration.
- Squash-only merges and merged-branch cleanup configured.
- Initial CI passed: https://github.com/Vati-AI/rsl-taxathon/actions/runs/41617888284

## Verified local setup

Python task validation, syntax checks and all six tooling tests passed. The claim test verifies a second agent cannot take an existing remote claim. PostgreSQL and Redis are healthy; SELECT 1 and PING succeeded. Host ports are localhost:55433 and localhost:56380 to avoid the existing Lekuka containers. Generated secrets stay in ignored .env.

## Outstanding configuration

1. GitHub rejected main branch protection with HTTP 403 because this organization has a free plan and the repository is private. The intended policy is stored in .github/branch-protection.json. A supported paid plan is required to enforce it; no purchase was made. CODEOWNERS remains useful documentation but is not enforced under this plan.
2. Only Keletso/Kellybrackets is a verified identity. Provide usernames for Leseli, Kananelo, Edga, Gupta, Jane and Yellow; then populate config/team.json and run setup with --invite. No invitations to guessed accounts were sent.
3. Local CLI Project synchronization is authorized and was run. The optional workflow requires a dedicated PROJECT_TOKEN secret; none has been created or copied into GitHub Actions. PROJECT_NUMBER is set to 1. Until a narrowly scoped credential is configured, sync with the authenticated local CLI.
4. Jube, OpenAleph/FtM, yente, Docling, LangGraph and Frappe have integration documentation/configuration. Their live adapters are implementation tasks; they are not connected. No upstream forks were necessary. Optional Docling configuration was not started or smoke-tested.
5. The taxpayer/auditor apps and case/AI APIs are scaffolds. This is the team development foundation, not a working tax investigation product.

## First work

Each member reads AGENTS.md and tasks/README.md, selects their READY task, claims the branch, implements and verifies, updates the task file and pushes a PR. Human review and merge remain mandatory team policy even while GitHub cannot enforce branch protection. Never mark a task DONE merely because its issue exists.
