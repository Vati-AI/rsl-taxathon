# Development

Prerequisites: Git, GitHub CLI authenticated to the repository, Python 3.11+, Docker with Compose v2. The validation/task tools use only Python's standard library.

```sh
python3 scripts/validate.py
python3 -m unittest discover -s tests/tooling -v
python3 scripts/dev_env.py
docker compose config --quiet
docker compose up -d --wait
```

This starts infrastructure only: PostgreSQL on localhost:55433 and Redis on localhost:56380. It does not start a product UI, backend or AI worker. PostgreSQL credentials are generated in ignored `.env`; never commit them. Redis is unauthenticated and loopback-only for disposable synthetic development; production needs authentication and private networking. Use a database client with the local credentials; do not print connection strings in logs.

`docker compose stop` preserves data. `docker compose down` removes containers but preserves volumes. Removing volumes destroys local data; do it only when you intend to reset the disposable environment. Images use version tracks; record tested digests before shared deployment.

## Parallel work

```sh
git clone git@github.com:Vati-AI/rsl-taxathon.git
cd rsl-taxathon
python3 scripts/task.py start TAX-001 --agent leseli-codex
# implement and test
python3 scripts/task.py status TAX-001 IN_PROGRESS --note 'Dashboard shell in development'
git add apps/auditor-web tasks/TAX-001.json
git commit -m 'feat(TAX-001): add auditor dashboard shell'
git push
gh pr create --base main --title 'feat(TAX-001): auditor dashboard shell' --body-file /tmp/pr-body.md
python3 scripts/task.py status TAX-001 IN_REVIEW --pr https://github.com/Vati-AI/rsl-taxathon/pull/NUMBER --note 'Checks passed; awaiting human review'
git add tasks/TAX-001.json
git commit -m 'chore(TAX-001): record review status'
git push
```

Substitute the actual repository owner if the organization name changes. `start` reserves the branch remotely before edits so simultaneous claims cannot silently overwrite one another. Run from a clean checkout; for parallel local work use separate clones/worktrees. A task with unfinished dependencies cannot be started; build agreed mocks in an independent task instead.

Task changes on feature branches become canonical when merged. For live visibility, run `python3 scripts/github_sync.py --repo Vati-AI/rsl-taxathon --project-number N --task TAX-001` from the task branch. Full sync is from main only. Board edits never rewrite task files automatically. Record DONE in a follow-up PR only after the implementation PR is merged and all criteria are checked.

## Review discipline

Do not merge your own PR. Request the human area owner and Keletso for shared API changes. Configure required checks and review protection once the GitHub plan supports it. CI alone does not prevent direct pushes on a free private organization repository.
