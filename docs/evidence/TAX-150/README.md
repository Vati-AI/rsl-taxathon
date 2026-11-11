# TAX-150 evidence: one product name and the open-source notices page

Everything users see uses the docs/DESIGN_STANDARD.md register. Component names stay only
where provenance or licences need them: API fields, the audit ledger, logs, Admin
diagnostics, verbatim provenance values and the **About · Open-source notices** page.

## What changed

- **User-visible text in both apps** now uses the register.
  - "Approve and apply in Investigation", "Nothing is sent to Investigation", "Recorded in
    Vati records".
  - Service labels: Vati records, Investigation, Document reading, Explore, Name
    screening, Vati assistant.
  - Browser tab titles end in "Vati Workspace · Cases" and "Vati Taxpayer Portal".
- **Backend messages officers can see.** Six decision messages now name Investigation
  instead of Jube: `decisions/engine.py`, `decisions/rules.py`, `decisions/store.py` and
  the `Vati Decision` DocType. Reason codes such as `jube_unavailable` are API fields and
  are unchanged.
- **Exports and notifications.** Evidence-export manifests are machine data, so their
  component fields are provenance. The repository has no email or notification templates
  (checked `backend/vati_tax/vati_tax` for templates and `sendmail`). There was nothing
  user-facing to rename.
- **Provenance.**
  - A service's own record ID or version string, such as `docling-serve@locked`, a
    synthetic Investigation case ID or a name-screening candidate ID, is shown verbatim in
    the shared `ProvenanceValue`. Its label uses the register name, for example
    "Document reading, docling-serve@locked".
  - The API, the audit ledger and the Admin diagnostics are unchanged. RSL system names
    (e-Tax, Lekuka, ASYCUDA World, Online Refunds Application) are unchanged.
- **Open-source notices** (`apps/shared/components/OpenSourceNotices.vue`, data in
  `apps/shared/notices/components.ts`).
  - The page lists 5 services and 8 libraries, each with the pinned version, the licence
    and links to the upstream source and licence text, and the Vati name it is shown under.
    It also lists the one hosted service.
  - Versions come from `integration/upstreams.lock.json`, `integration/services.json`,
    `integration/frappe-framework.lock.json`, `requirements-dev.lock` and `package.json`.
  - Licences were read from each upstream repository's licence file on 2026-11-11.
    Jube is AGPL-3.0. OpenAleph, yente, Docling Serve, Frappe, FollowTheMoney and
    LangGraph are MIT.
  - **Auditor app:** the page is at `/auditor/about/notices`, reached from the user menu
    ("About · Open-source notices").
  - **Taxpayer portal:** the page is at `/taxpayer/about/notices`, reached from a new
    footer and from the account menu. The footer is also shown in the loading and error
    states.
- **Blocking naming checks.**
  - **Static check:** `npm run check` now starts with `npm run check:naming`. It runs
    `scripts/check_naming.mjs` over both apps' Vue templates (text, static attributes and
    expressions) and their TypeScript strings, and it has a self-test in
    `scripts/check_naming.test.mjs`. It ignores comments, imports, single-token machine
    values (`jube`, `docling.submit`) and lines marked `naming-allowed:`; one is used, for
    the `X-Frappe-CSRF-Token` HTTP header.
  - **Browser check:** the TAX-144 naming ratchet's baseline is now empty
    (`tests/frontend/support/layout-baseline.json`). Any component name rendered outside
    a `data-component-names-allowed` region fails `layout.spec.ts`.

## Results

- **Naming:**
  - Static check: 0 hits, down from 28.
  - Browser check at 1440×900 on every auditor and taxpayer screen, including the notices
    pages: 0 names, down from 19 across 9 auditor screens before TAX-150.
- **Page length (TAX-144 budget, 1.5 viewports at 1440×900):**
  - Auditor notices page: 1.09 viewports.
  - Taxpayer notices page: 1.23 viewports.
  - Every other screen not already listed in the TAX-144 baseline is at most 1.44
    viewports, including the new footer.
- **`tests/frontend/notices.spec.ts`** passes at 1440, 1024 and 390 (18 tests). It covers:
  - menu and footer links, and the page title;
  - the table contents and licence links;
  - 0 names outside the exempt region;
  - axe with no serious violations;
  - no horizontal page scroll;
  - keyboard focus reaching the licence links, with a visible outline;
  - provenance values on a case;
  - the portal's loading and error states;
  - the denied auditor state.
- **Unit tests:** `kit.test.ts` adds `ProvenanceValue` and the notices table (every row
  has source and licence links, and the region is exempt).

## Screenshots (1440, 1024 and 390)

| State | Files |
|---|---|
| Notices, auditor (from the user menu) | `auditor-notices-{1440,1024,390}.png` |
| Notices, taxpayer (from the footer) | `taxpayer-notices-{1440,1024,390}.png` |
| Case detail with provenance values | `auditor-case-detail-{1440,1024,390}.png` |
| Portal loading, footer visible | `taxpayer-loading-footer-{1440,1024,390}.png` |
| Portal error, footer visible | `taxpayer-error-footer-{1440,1024,390}.png` |
| Auditor denied state, no names | `auditor-denied-{1440,1024,390}.png` |

The notices page has no empty state, because its data is static. Empty states on the
other screens are unchanged from TAX-145 and TAX-146.

## Verification

```bash
npm run check
```

```bash
npx playwright test --config tests/frontend/playwright.config.ts --trace off
```

```bash
npm run test:auditor:browser -- --trace off
```

- **`npm run check`:** passes. It includes the naming check and its self-test; auditor
  vitest 137 and taxpayer vitest 77.
- **Full frontend suite:** 283 passed and 11 failed.
  - 9 of the failures are the demo recording harness (`tests/frontend/demo`). It needs a
    separately started demo stack on ports 58080/58082 and fails with "connection
    refused" before loading a page, so it is unrelated to this change.
  - 2 were SYN-004, a timing race in the test: text typed into the evidence-request form
    was overwritten by the suggestion prefill when it arrived late. The spec now waits for
    the prefill to tick the justifying finding before typing. Afterwards SYN-004 passed
    15 of 15 runs (5 repeats at each width). Before the fix it failed 3 of 9.
- **Auditor end-to-end tests:** 66 passed.
- **Backend:** `python3 scripts/dev.py check` passes. No backend test asserted the
  reworded messages.
