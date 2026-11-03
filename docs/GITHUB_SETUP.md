# GitHub setup and governance

Target: private `Vati-AI/rsl-taxathon`. Organization creation requires a signed-in owner to accept GitHub terms and any verification. The live setup is recorded in `SETUP_STATUS.md`.

## Publishing

Once the organization exists and the authenticated account can create repositories:

```sh
gh repo create Vati-AI/rsl-taxathon --private --source . --remote origin --push
python3 scripts/github_setup.py --repo Vati-AI/rsl-taxathon
python3 scripts/github_sync.py --repo Vati-AI/rsl-taxathon --project-number NUMBER
```

The setup script refuses a public repo, configures squash-only merges and branch cleanup, creates role teams, links a private Project and attempts branch protection. It records a resumable local report in ignored `.local/github-setup.json`. The CLI needs `repo`, `admin:org` and `project` authorization as applicable. Use `gh auth refresh -h github.com -s project -s admin:org` interactively if scopes are missing. Never paste tokens into chat or commit them.

Only Keletso's username (`Kellybrackets`) has been verified. Fill null entries in `config/team.json` from confirmed team identities, then run `scripts/github_setup.py --invite`. This explicitly invites those people to their role teams; it never searches names and guesses accounts. Members receive write access through teams; Keletso retains ownership/admin access. CODEOWNERS is generated only from verified configured usernames, with Keletso as fallback. Invitation acceptance is separate from sending an invitation.

## Branch policy

Intended protection in `.github/branch-protection.json`: one approving review, code-owner review, stale approvals dismissed, required `validate` check, up-to-date branch, resolved conversations, linear history, admin enforcement, no force push or branch deletion. Enable after initial main seed. Human reviewer merges after acceptance; agents do not merge.

GitHub Free organizations cannot enforce protected branches/CODEOWNERS on private repositories. A paid plan such as GitHub Team is required; no purchase is performed by these scripts. If GitHub rejects protection, the setup report explicitly records the failure. CI and written instructions are not a substitute for server-enforced protection. Keep the repository private; do not make it public to gain protection.

## Board and tasks

Project title: RSL Taxathon — Team delivery. Private, linked to this repo. The live Board view is saved; for another deployment, create a Board view in the Project UI grouped by Status (Todo / In Progress / Done); labels preserve READY, BACKLOG, BLOCKED and IN_REVIEW distinctions. Repo-native tasks remain the source of truth. The sync tool upserts one issue per TAX ID, applies owner/status/area labels, adds it to the Project and updates the Status field. It also reopens tasks that are no longer DONE. Run it from trusted main after merges, or with `--task TAX-XXX` from that task's branch for live progress.

Automation is provided as a manual workflow, using optional `PROJECT_TOKEN` and repository variable `PROJECT_NUMBER`. Do not supply broad credentials to pull_request workflows. Without that token use the local CLI. Assignments wait until the configured collaborator has accepted access. No automatic merge is configured.

## Verification

Check repo visibility, organization ownership, accepted memberships, team permissions, project visibility/items, actual branch protection response, and a successful `validate` CI run. Preserve actual URLs and unresolved blockers in the setup handover; do not label untested service adapters as ready.

Expanded workspace: PROJECT.md and docs/ONBOARDING.md are the developer entry points. Initial broad tasks are epics; use dependency-ready leaf tasks from task.py list. During the authorized setup only, a maintainer may run github_sync.py with --allow-branch chore/TAX-017-setup-handover to seed the reviewed backlog before merge.
