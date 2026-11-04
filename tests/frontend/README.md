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

| File                                 | Purpose                                                                                                                                                                 |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `playwright.config.ts`               | One project per width: `desktop-1440`, `tablet-1024`, `mobile-390`.                                                                                                     |
| `support/matrix.ts`                  | Viewport, route and keyboard test matrix for both portals.                                                                                                              |
| `support/sessions.ts`                | Role/session mocks (below).                                                                                                                                             |
| `support/checks.ts`                  | axe (WCAG 2.1 A/AA, serious/critical), reflow and focus helpers.                                                                                                        |
| `support/warmup.ts`                  | Visits taxpayer routes once so Vite's first-load reload stays out of tests.                                                                                             |
| `support/auditor-preview.config.mts` | Vite preview of the built auditor with `/api` sent to the fixture API.                                                                                                  |
| `auditor.spec.ts`                    | Positive navigation, denied access, keyboard, axe/reflow matrix.                                                                                                        |
| `taxpayer.spec.ts`                   | Positive navigation, guest and API-denied access, logout, keyboard, axe/reflow matrix.                                                                                  |
| `visual.spec.ts`                     | Opt-in screenshots and baseline comparison (below).                                                                                                                     |
| `claim.spec.ts`                      | TAX-043 VAT refund claim: send, duplicate retry, invalid documents, cross-scope evidence, keyboard, axe/reflow. `CAPTURE_SCREENSHOTS=1` writes `docs/evidence/TAX-043`. |

## Session mocks

Everything is answered inside the test browser; no account or password exists.

- **Taxpayer portal**: `vati_tax.portal.api.*` and `/login` are intercepted.
  Records come from `list_submissions(scope)` in
  `backend/vati_tax/vati_tax/portal/fixtures.py`, so the mock follows the
  same TAX-029 ownership rules as the Frappe app. Responses follow Frappe v16
  shapes (`{ message }`, or HTTP 403 with `exc_type`). Personas:
  - `taxpayer`: `taxpayer.a@example.test` with the taxpayer membership from
    docs/PERMISSIONS.md; sees only SUB-SYN-0001, and another taxpayer's
    submission is "not found".
  - `guest`: not signed in; every portal method is refused with 403.
  - `no-membership`: signed in but not linked to a taxpayer; refused with 403.
  - `revoked`: the portal loaded, then the membership was removed; data calls
    are refused.

  TAX-043 adds `vati_tax.evidence.api.upload_evidence` and
  `vati_tax.portal.api.submit_refund_claim`. The upload mock requires the
  session's `X-Vati-Tenant-ID` and refuses files the way TAX-032 reports it
  (HTTP 422 with the `code:reason` message title); content checks the browser
  cannot make are keyed off the file name (`renamed…` → `extension_mismatch`,
  `broken…` → `malformed_document`). The claim mock replays the same
  idempotency key with the same answers and refuses changed answers
  (`idempotency_conflict`). Tests can set `dropNextSendResponse` (store the
  claim, then drop the response) or `rejectEvidenceOnSend` (the server's
  evidence-ownership refusal). The server rules themselves are tested on a
  Frappe site by `vati_tax/portal/test_refund_claim_bench.py`.

- **Auditor workspace**: the fixture API (`scripts/fixture_api.py`, TAX-040)
  has one signed-in officer principal scoped to one synthetic tenant. Nothing
  is faked in the browser:
  - `officer`: requests go unchanged and see the principal's assigned cases.
  - `denied`: requests select another synthetic tenant, so the fixture API
    itself answers 403 `tenant_scope_denied`.

Personas mirror the documented TAX-029 rules (docs/PERMISSIONS.md); do not
invent roles here. The taxpayer tests cover the client; the server page guard
and API scope are covered by `vati_tax/portal/test_access.py`.

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
