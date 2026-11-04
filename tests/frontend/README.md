# frontend-qa

Owner: Jane (reassignment to Kananelo proposed in PR #106). See `tasks/README.md`
for work and `docs/ARCHITECTURE.md` for boundaries.

Cross-portal browser harness from TAX-023. It checks the auditor workspace and
the taxpayer portal at the three design-standard widths (docs/DESIGN_STANDARD.md)
using synthetic data only. Passing it supports, but never replaces, human visual
review.

## Run it (repository root)

```sh
npm ci
npx playwright install chromium          # once; on Windows you can use Edge instead
npm run build:auditor                    # the auditor is tested from its build
npm run test:frontend:browser            # all specs × 1440/1024/390px
npm run test:frontend:browser -- --project=mobile-390 taxpayer   # one width, one portal
```

Windows: set `PLAYWRIGHT_CHANNEL=msedge` to use the installed Edge, and
`PYTHON=python` if `python3` is not the interpreter you want. Known limitation:
on Windows the fixture server (`scripts/dev.py`) can truncate the largest
auditor asset under parallel load, which blanks auditor pages; run the auditor
specs on Linux/CI or in a container until that is fixed. The taxpayer specs run
on Windows.

The harness starts its own servers on dedicated loopback ports (auditor fixture
server :58093, taxpayer Vite dev server :58094), so it never reuses a server or
Frappe site you already have running.

## What is in here

| File                   | Purpose                                                                                |
| ---------------------- | -------------------------------------------------------------------------------------- |
| `playwright.config.ts` | One project per width: `desktop-1440`, `tablet-1024`, `mobile-390`.                    |
| `support/matrix.ts`    | Viewport, route and keyboard test matrix for both portals.                             |
| `support/sessions.ts`  | Role/session mocks (below).                                                            |
| `support/checks.ts`    | axe (WCAG 2.1 A/AA, serious/critical), reflow and focus helpers.                       |
| `support/warmup.ts`    | Visits taxpayer routes once so Vite's first-load reload stays out of tests.            |
| `auditor.spec.ts`      | Positive navigation, denied access, keyboard, axe/reflow matrix.                       |
| `taxpayer.spec.ts`     | Positive navigation, guest and API-denied access, logout, keyboard, axe/reflow matrix. |
| `visual.spec.ts`       | Opt-in screenshots and baseline comparison (below).                                    |

## Session mocks

Everything is answered inside the test browser; no account or password exists.

- **Taxpayer portal**: `vati_tax.portal.api.*` and `/login` are intercepted.
  Submission records are read from `backend/vati_tax/vati_tax/portal/fixtures.py`
  so they match what the Frappe app serves. Responses follow Frappe v16 shapes
  (`{ message }`, or HTTP 403 with `exc_type`). Personas:
  - `taxpayer`: signed-in synthetic Website User `taxpayer.a@example.test`.
  - `guest`: no session; every portal method is refused with 403.
  - `denied`: signed in, but the portal API refuses submission data.
- **Auditor workspace**: no sign-in exists yet. `officer` reads the fixture API;
  `denied` answers case requests with 403.

Personas are session states, not RSL roles. Taxpayer roles and tenant
permissions arrive in TAX-029; extend the personas then rather than inventing
roles here. The taxpayer guest test covers the client-side guard; the server
page guard is covered by `vati_tax/portal/test_access.py` and the live TAX-021
evidence.

## Screenshots and baselines

```sh
CAPTURE_SCREENSHOTS=1 npm run test:frontend:browser -- visual   # review evidence → docs/evidence/TAX-023
VISUAL_COMPARE=1 npm run test:frontend:browser -- visual        # compare with baselines/
```

`baselines/` stays empty until the human frontend owner accepts the screens.
Then, and only then, create them with
`VISUAL_COMPARE=1 npm run test:frontend:browser -- visual --update-snapshots`
in the CI Linux environment (fonts differ by OS) and commit them in a reviewed
PR that links the acceptance. Agents do not approve baselines themselves.
