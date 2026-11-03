# GitHub setup and governance

Target: private `Vati-AI/rsl-taxathon`. Organization creation requires a signed-in owner to accept GitHub terms and any verification. The live setup is recorded in `SETUP_STATUS.md`.

## Publishing

Once the organization exists and the authenticated account can create repositories:

```sh
gh repo create Vati-AI/rsl-taxathon --private --source . --remote origin --push
python3 scripts/github_setup.py --repo Vati-AI/rsl-taxathon
python3 scripts/github_sync.py --repo Vati-AI/rsl-taxathon --project-number NUMBER
```

The setup script refuses a public repo, configures squash-only merges and branch cleanup, creates role teams, links a private Project and records current branch protection without attempting paid-only changes. It records a resumable local report in ignored `.local/github-setup.json`. The CLI needs `repo`, `admin:org` and `project` authorization as applicable. Use `gh auth refresh -h github.com -s project -s admin:org` interactively if scopes are missing. Never paste tokens into chat or commit them.

Only Keletso's username (`Kellybrackets`) has been verified. Fill null entries in `config/team.json` from confirmed team identities, then run `scripts/github_setup.py --invite`. This explicitly invites those people to their role teams; it never searches names and guesses accounts. Members receive write access through teams; Keletso retains ownership/admin access. CODEOWNERS uses verified role teams with repository access, with Keletso as fallback. Invitation acceptance is separate from sending an invitation.

## Branch policy

GitHub Free is the explicit v1 choice. Human area-owner review, passing validate/quality/ai-smoke checks, a refreshed branch and resolved comments precede squash merge. Agents never merge or push directly to main. CODEOWNERS records review responsibility; reviewers should be requested explicitly where automatic requests are unavailable.

The historical `.github/branch-protection.json` is a reference only, not an active requirement. Setup does not purchase plans, attempt paid-only protection or make the repository public. CI and team conventions do not technically prevent an authorized user bypassing review; the team accepts that prototype tradeoff.

## Board and tasks

Project title: RSL Taxathon — Team delivery. Private, linked to this repo. The live Board view is saved; for another deployment, create a Board view in the Project UI grouped by Status (Todo / In Progress / Done); labels preserve READY, BACKLOG, BLOCKED and IN_REVIEW distinctions. Repo-native tasks remain the source of truth. The sync tool upserts one issue per TAX ID, applies owner/status/area labels, adds it to the Project and updates the Status field. It also reopens tasks that are no longer DONE. Run it from trusted main after merges, or with `--task TAX-XXX` from that task's branch for live progress.

Automation is provided as a manual workflow, using optional `PROJECT_TOKEN` and repository variable `PROJECT_NUMBER`. Do not supply broad credentials to pull_request workflows. Without that token use the local CLI. Assignments wait until the configured collaborator has accepted access. No automatic merge is configured.

## Verification

Check repo visibility, organization ownership, accepted memberships, team permissions, project visibility/items, the documented free-plan review convention, and successful CI checks. Preserve actual URLs and unresolved blockers in the setup handover; do not label untested service adapters as ready.

Expanded workspace: PROJECT.md and docs/ONBOARDING.md are the developer entry points. Initial broad tasks are epics; use dependency-ready leaf tasks from task.py list. During the authorized setup only, a maintainer may run github_sync.py with --allow-branch chore/TAX-017-setup-handover to seed the reviewed backlog before merge.

Prototype v1 uses [the shared engineering standard](ENGINEERING_STANDARD.md) and [toolchain checks](TOOLCHAIN.md).
