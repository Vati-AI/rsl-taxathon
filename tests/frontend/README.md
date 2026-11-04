# frontend-qa

Owner: Kananelo (reassigned from Jane). See `tasks/README.md` for work and `docs/ARCHITECTURE.md` for boundaries.

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
`PYTHON=python` if `python3` is not the interpreter you want.

The harness starts its own servers on dedicated loopback ports, so it never
reuses a server or Frappe site you already have running:

| Port   | Server                                                                  |
| ------ | ----------------------------------------------------------------------- |
| :58092 | Synthetic fixture API, `scripts/dev.py serve` (auditor data and access) |
| :58093 | Built auditor app, `vite preview` with `/api` proxied to :58092         |
| :58094 | Taxpayer portal, Vite dev server (portal API answered by session mocks) |

Why `vite preview` for the auditor: on Windows, `scripts/dev.py` can truncate
the largest built asset (about 300 KB) when several requests arrive at once,
which leaves auditor pages blank. Observed during TAX-023: an HTTP/1.0
connection-close response loses its tail; keep-alive (HTTP/1.1) did not.
CI on Linux is unaffected. The fixture server is shared tooling and was not
changed here; the harness only avoids using it for static files. API
responses are small JSON and still come from it, including its access rules.

## What is in here

| File                                 | Purpose                                                                                |
| ------------------------------------ | -------------------------------------------------------------------------------------- |
| `playwright.config.ts`               | One project per width: `desktop-1440`, `tablet-1024`, `mobile-390`.                    |
| `support/matrix.ts`                  | Viewport, route and keyboard test matrix for both portals.                             |
| `support/sessions.ts`                | Role/session mocks (below).                                                            |
| `support/checks.ts`                  | axe (WCAG 2.1 A/AA, serious/critical), reflow and focus helpers.                       |
| `support/warmup.ts`                  | Visits taxpayer routes once so Vite's first-load reload stays out of tests.            |
| `support/auditor-preview.config.mts` | Vite preview of the built auditor with `/api` sent to the fixture API.                 |
| `auditor.spec.ts`                    | Positive navigation, denied access, keyboard, axe/reflow matrix.                       |
| `taxpayer.spec.ts`                   | Positive navigation, guest and API-denied access, logout, keyboard, axe/reflow matrix. |
| `visual.spec.ts`                     | Opt-in screenshots and baseline comparison (below).                                    |

## Session mocks

Everything is answered inside the test browser; no account or password exists.

- **Taxpayer portal**: `vati_tax.portal.api.*` and `/login` are intercepted.
  Submission records are read from `backend/vati_tax/vati_tax/portal/fixtures.py`
  so they match what the Frappe app serves. Responses follow Frappe v16 shapes
  (`{ message }`, or HTTP 403 with `exc_type`). Personas:
  - `taxpayer`: signed-in synthetic Website User `taxpayer.a@example.test`.
  - `guest`: no session; every portal method is refused with 403.
  - `denied`: signed in, but the portal API refuses submission data.
- **Auditor workspace**: the fixture API (`scripts/fixture_api.py`, TAX-040)
  has one signed-in officer principal scoped to one synthetic tenant. Nothing
  is faked in the browser:
  - `officer`: requests go unchanged and see the principal's assigned cases.
  - `denied`: requests select another synthetic tenant, so the fixture API
    itself answers 403 `tenant_scope_denied`.

Personas are session states, not RSL roles. Taxpayer roles and tenant
permissions arrive in TAX-029; extend the personas then rather than inventing
roles here. The taxpayer guest test covers the client-side guard; the server
page guard is covered by `vati_tax/portal/test_access.py` and the live TAX-021
evidence.

## Accessibility scan and known issues

Every matrix route is scanned with axe-core for serious or critical WCAG 2.0/2.1
A and AA violations. Recorded defects owned by another task are listed in
`KNOWN_A11Y_ISSUES` (`support/checks.ts`); each entry excludes one rule on one
element only. Current entry:

- `/auditor/showcase`, `label-content-name-mismatch` (WCAG 2.5.3 Label in Name):
  the loading-demo button shows "Show loading" but its accessible name is
  "Saving" (`apps/auditor-web/src/views/ShowcaseView.vue`). Owner: auditor
  frontend. Remove the entry when fixed.

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
