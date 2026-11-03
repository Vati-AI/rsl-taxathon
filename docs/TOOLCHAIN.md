# Developer toolchain

Use Python 3.13 and Node 22.13+ for the shared checks. Existing fixture tooling supports Python 3.11+. Frappe uses its container interpreter, not the host environment. Never replace system Python/Node or another project's environment automatically.

```sh
python3 -m venv .venv-dev
# Windows: use .venv-dev\Scripts\python instead of .venv-dev/bin/python
.venv-dev/bin/python -m pip install -r requirements-dev.lock
npm ci
python3 scripts/quality.py
python3 scripts/dev.py check
```

`python3 scripts/quality.py --fix` formats first-party Python and fixture web assets; inspect the diff. It never edits upstream source. AI installation remains `.venv-ai` with ai/requirements.lock; run its model-free smoke. Only backend/integration roles need Docker/vendor services for their tasks.

VS Code: select .venv-dev for repository tools; optionally use the Ruff/Python extensions. PyCharm: select .venv-dev as the host interpreter and run the same terminal checks. Frappe code executes via bench/container. Keep IDE metadata local. No paid IDE feature or extension is required.

The first Vue implementation adds its build, vue-tsc and behaviour tests to npm check/CI. TAX-020 did this for `apps/auditor-web` (ADR-004): `npm run check` now lints/formats TypeScript and Vue, type-checks, runs unit/component tests and builds the auditor app; CI also runs its Playwright browser smoke tests. Windows note: `python3` may be a Microsoft Store alias; activate `.venv-dev` (add a `python3.exe` copy of its `python.exe` if needed). If Smart App Control blocks Mypy's compiled extension, run Mypy in a `python:3.13-slim` container rather than weakening system security. Do not hide new TypeScript/Vue files outside lint/test coverage. Pin dependencies through npm and commit package-lock.json. Python tool versions are in requirements-dev.lock; update through a reviewed PR. Dependency audits inform review; examine applicable runtime findings rather than blindly upgrading all packages.

Dependency audit at setup (2026-11-03): npm reports five moderate entries rooted in a minimatch glob-amplification advisory in the ESLint development tree. Current checks use fixed repository globs; these packages are not shipped as application runtime dependencies. No forced downgrade/major override was applied. Recheck on tool updates; do not describe this dependency tree as vulnerability-free.
