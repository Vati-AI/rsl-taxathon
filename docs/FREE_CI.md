# Working while hosted CI is out of included usage

GitHub Free remains the project plan. Local checks do not consume GitHub-hosted
runner allowance. They do not clear failed hosted checks or replace human review.
Do not mark a blocked job successful, disable checks, or merge solely because the
billing error prevented execution.

## Verify a PR locally

Use an independent checkout. Inspect the diff before executing PR code, and do not
copy `.local` credentials or taxpayer data into it. Record the PR number and exact
`git rev-parse HEAD` commit with the results. Recheck after any new commit or merge.
The commands below use macOS/Linux shell syntax; see TOOLCHAIN.md for Windows.

Install Python 3.13, Node 22.13+ (or supported Node 24), and Docker Compose.
Install the committed dependency locks, not newer unpinned versions:

```sh
python3.13 -m venv .venv-dev
. .venv-dev/bin/activate
python -m pip install -r requirements-dev.lock
python3.13 -m venv .venv-ai
.venv-ai/bin/python -m pip install -r ai/requirements.lock
npm ci
npx playwright install chromium
export LANGSMITH_TRACING=false
export PYTHON="$VIRTUAL_ENV/bin/python"
```

Run all five job equivalents from the checkout root. The AI dependencies above
ensure the workspace's LangGraph tests execute instead of being skipped. Keep AI
and quality dependencies separate, matching the hosted jobs.

```sh
PATH="$PWD/.venv-ai/bin:$PATH" .venv-ai/bin/python scripts/dev.py check
python3 scripts/dev_env.py
# Validate configuration only; these commands do not start vendor services.
docker compose config --quiet
docker compose -f docker-compose.frappe.yml config --quiet
docker compose -f docker-compose.docling.yml config --quiet
python3 scripts/quality.py
# quality.py builds the frontend used by the browser suites.
npm run test:auditor:browser -- --workers=2
npm run test:frontend:browser -- --workers=2
.venv-ai/bin/python ai/smoke.py
```

Retain exit codes and logs, including skips. A local macOS run is not proof of a
Linux-hosted CI pass. Frappe bench integration tests need a disposable site and
remain separate from dependency-free tests and mocked browser sessions.

## Save future hosted usage

CI groups runs by workflow and Git ref and cancels superseded runs in that group.
Different PRs remain independent. All existing jobs and triggers remain enabled.
Cancellation saves only work that has not yet finished; it cannot refund consumed
allowance or remove the current account block. See GitHub's
[concurrency documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).

Keep paid usage disabled if the budget is zero. Once included usage resets or the
account block is resolved, rerun hosted checks on the current PR commits. Review
actual usage and billed amounts separately in organization billing settings.

## Optional self-hosted runner

No persistent runner is installed by this change. A self-hosted runner needs a
separate disposable machine or VM with no personal files, Jube credentials, cloud
credentials, production access, or host home-directory mounts. It must be online
to accept jobs. Restrict it to the intended repository and trusted execution;
private-repository PR code can still compromise a runner. Registration and a
reviewed workflow change are required before any job can use it.

Do not attach an unattended runner to a developer's normal login to work around
billing. A container that mounts that login's home or Docker socket is not the
required isolation. See GitHub's
[runner security guidance](https://docs.github.com/en/actions/reference/security/secure-use).
