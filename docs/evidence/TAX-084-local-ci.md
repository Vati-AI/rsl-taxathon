# TAX-084 local CI recovery evidence

Date: 2026-11-05. Platform: macOS ARM64, Python 3.13, Node 24.20.0.
Quality tools and AI dependencies use separate environments with the committed
locks. PR checkouts share installed dependencies only after confirming identical
lock contents. No project `.local` credentials were copied.

The original 20 hosted checks on PRs 174–177 executed zero steps: GitHub reported
an account payment/spending-limit block. Local results are not hosted check runs.
The user chose local verification while waiting for included usage to reset.

## Results

| PR | Tested commit | Workspace / quality / AI smoke / Compose | AI tests | Auditor browser | Cross-portal browser |
|---|---|---|---|---|---|
| [#174](https://github.com/Vati-AI/rsl-taxathon/pull/174) | `c01ed12631d6a53eaa626fa5392c1b51266d2167` | Pass | 144 passed | 54 passed; 17 skipped | 126 passed; 9 skipped |
| [#175](https://github.com/Vati-AI/rsl-taxathon/pull/175) | `2f20ba1d7fd0d9cd4a755243cf14ce7f2d6173f2` | Pass | 144 passed | 54 passed; 17 skipped | 126 passed; 9 skipped |
| [#176](https://github.com/Vati-AI/rsl-taxathon/pull/176) | `2fcd6ad574fcb725dad053ab464e6f458d69af6f` | Pass | 144 passed | 54 passed; 17 skipped | 126 passed; 9 skipped |
| [#177](https://github.com/Vati-AI/rsl-taxathon/pull/177) | `92e43be0f67fc58fefde8e56d819d92c0ddc5fbf` | Pass | 144 passed | 54 passed; 17 skipped | 132 passed; 9 skipped |

All listed commands completed with exit code 0 on their final runs. No application
code changes were needed for these checks. Test skips remain visible below.
The TAX-084 maintenance branch also passed workspace and full quality checks.

## Scope and limitations

- Workspace checks use the existing `scripts/dev.py check` command. AI tests are
  additionally run with the AI lock installed: `python -m unittest discover -s tests/ai -v`.
- Quality includes Ruff, Mypy, ESLint, Prettier, TypeScript checks, Vue unit tests
  and frontend builds. Both existing Playwright suites run with two workers.
- Compose validation checks configuration, not live service health. Browser
  checks use synthetic fixtures/mocked sessions, not a live Frappe site.
- Frappe-dependent tests and optional screenshot/visual comparisons remain
  skipped where their required environment/approved baselines are absent.
  This is not human visual acceptance or full product acceptance.
- An initial combined AI/quality environment produced Mypy errors because it did
  not match CI job isolation. Those results were superseded by checks in separate
  environments. PR 174's first browser retry encountered a fixture server left
  by that interrupted attempt; only our orphaned servers were stopped, and the
  suite was rerun. These setup issues are not attributed to the PR code.
- CI YAML was parsed and compared with origin/main: all existing jobs, commands,
  triggers and permissions are unchanged. Only workflow/ref-scoped cancellation
  was added. Its hosted runtime behavior is unverified while usage is blocked.
- No PR was merged, no paid usage enabled, and no permanent runner installed.
