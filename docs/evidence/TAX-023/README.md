# TAX-023 review evidence

Evidence for the frontend test harness and accessibility baseline
(`tests/frontend`). Screenshots are **review evidence, not approved baselines**:
baselines are only created after the human frontend owner accepts the screens
(docs/DESIGN_STANDARD.md). Synthetic data only; no Frappe site, account or
password was used.

## Environment (2026-11-04)

- Windows 11, Microsoft Edge 156.0.4314.50 (`PLAYWRIGHT_CHANNEL=msedge`),
  Playwright 1.64.1, axe-core via @axe-core/playwright 4.14.0.
- Branch `task/TAX-023` merged with current `main` (includes TAX-040 auditor
  queue/profile, TAX-029 taxpayer roles and tenant permissions, and the #106
  reassignment).
- Auditor: built app (`npm run build:auditor`) on `vite preview`, `/api` from
  the synthetic fixture server `scripts/dev.py` (unchanged).
- Taxpayer: Vite dev server; portal API answered by the session mocks in
  `tests/frontend/support/sessions.ts`, which call `list_submissions(scope)`
  from `backend/vati_tax/vati_tax/portal/fixtures.py` for the synthetic
  taxpayer user documented in docs/PERMISSIONS.md (owner of SUB-SYN-0001).

## Results (`results.json`)

`npm run test:frontend:browser`: **66 passed, 0 failed, 0 flaky**; the 9 skipped
are the opt-in visual tests (run separately below). Same result in two full
runs: before and after merging `main` at 2ad25f2. `results.json` is the
Playwright JSON report of the final run on `308205b`, summarised.

| Spec               | 1440px | 1024px | 390px |
| ------------------ | ------ | ------ | ----- |
| `auditor.spec.ts`  | 9/9    | 9/9    | 9/9   |
| `taxpayer.spec.ts` | 13/13  | 13/13  | 13/13 |

Covered at every width:

- **Positive navigation** — auditor: overview → My case queue → SYN-002 →
  taxpayer profile → back → breadcrumb to queue. Taxpayer: list → own
  submission status → breadcrumb → new submission → account.
- **Denied access, auditor** — requests for another synthetic tenant are
  refused by the fixture API itself (403 `tenant_scope_denied`) on the queue,
  case and taxpayer pages; "You do not have access" is shown and no taxpayer
  data or Request evidence action appears.
- **Denied access, taxpayer (TAX-029 rules)** — a guest is sent to
  `/login?redirect-to=/taxpayer/...` and only the session check is called; a
  signed-in user without a taxpayer membership is refused and sent to login;
  another taxpayer's submission is concealed as "Submission not found"; a
  membership revoked after loading shows "Access denied" and no submissions;
  after logout a revisit goes to login.
- **Keyboard** — first Tab focuses a visible "Skip to main content" link and
  Enter moves into the main region; sidebar/bottom-bar links show a visible
  focus indicator and open on Enter; submitting an incomplete taxpayer form with
  Enter keeps entered values and focuses the first invalid field.
- **Accessibility and reflow** — 6 auditor and 5 taxpayer routes: no serious or
  critical axe violations (WCAG 2.0/2.1 A and AA) apart from the known issue
  below, and no page-wide horizontal scrolling.

## Screenshots

`CAPTURE_SCREENSHOTS=1 npm run test:frontend:browser -- visual` (7 passed; the 2
skipped are the focus captures, which run at 1440px only). 41 full-page
captures, all from the run on 2026-11-04 after the TAX-029 merge, at 1440, 1024
and 390px:

- Auditor: `auditor-overview`, `auditor-queue`, `auditor-case-detail`,
  `auditor-taxpayer`, `auditor-not-found`, `auditor-denied` (another tenant).
- Taxpayer: `taxpayer-submissions` (own submission only),
  `taxpayer-submission`, `taxpayer-new-submission`, `taxpayer-account`,
  `taxpayer-not-found`, `taxpayer-other-taxpayer` (concealed as not found),
  `taxpayer-denied` (membership revoked after loading).
- Keyboard focus (1440px): `auditor-focus-skip-link-1440.png`,
  `taxpayer-focus-first-invalid-1440.png`.

## Findings for review

1. **Accessibility defect (auditor showcase)** — axe `label-content-name-mismatch`
   (WCAG 2.5.3 Label in Name, level A): the loading-demo button shows
   "Show loading" but its accessible name is "Saving"
   (`apps/auditor-web/src/views/ShowcaseView.vue`). Reproduce: open
   `/auditor/showcase` and scan with axe including the `wcag21a` tag. Recorded in
   `KNOWN_A11Y_ISSUES` (that one rule on that one element only) for the auditor
   frontend owner; not changed here.
2. **Visual consistency, for human judgement** — the taxpayer portal's primary
   buttons are black, while the auditor uses the `#1d4ed8` blue action accent
   from the visual contract (compare `taxpayer-submissions-1440.png` with
   `auditor-overview-1440.png`). Not asserted as a defect by this harness.

## Limitations

- These are harness results, not visual approval. Human review of the
  screenshots is still required; `tests/frontend/baselines/` is intentionally
  empty.
- Taxpayer access tests exercise the client with mocked Frappe responses that
  follow TAX-029. The server-side rules are covered by
  `vati_tax/portal/test_access.py`; a user without a taxpayer membership is
  refused by the Frappe page guard before the app loads.
- On Windows, `scripts/dev.py` can truncate the largest built auditor asset
  under parallel requests (observed: about 3 in 4 rounds lost the end of a
  310 KB file over HTTP/1.0 connection-close; keep-alive did not). The harness
  therefore serves auditor static files with `vite preview`. The fixture server
  was not modified; this is reported to its owner.
- Screenshots were taken with Edge on Windows. A Linux/CI capture was not
  possible locally (the Linux browser download did not complete on this
  network), so fonts may differ slightly from CI.
