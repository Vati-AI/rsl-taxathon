# TAX-149 evidence: Reports as tabbed reports

The management dashboard (TAX-060) and the integrity review (TAX-077) are now one Reports
page (`apps/auditor-web/src/views/ReportsView.vue`).

## What changed

- **Six routed tabs under one reporting window** (`?window=7|30|90`). The tabs keep the
  window as you move between them.

  | Tab | URL | Content |
  |---|---|---|
  | Workload | `/auditor/management` | Open cases, unassigned, awaiting evidence; by status and by officer |
  | Timeliness | `/management/timeliness` | Age of open cases and time to close |
  | Evidence | `/management/evidence` | Outstanding and overdue evidence requests |
  | Outcomes | `/management/outcomes` | Closures and legal-route coverage |
  | Ledger assurance | `/management/assurance` | Ledger check, entries, overrides, refusals, review signals |
  | Integrity | `/management/integrity` | Policy exposure, control overrides, review signals and the ledger check |

- **Same data, same names.**
  - The management summary loads once for its five tabs; the Integrity tab loads the
    integrity review. Both are the existing APIs, unchanged.
  - The route names `management` and `integrity` are unchanged, so existing links still
    work.
  - Every figure comes from those two summary APIs. Small groups are still combined into
    one row (ADR-036), with the privacy note shown.
- **Layout.**
  - The Reports page uses the wide layout.
  - The Integrity sections sit two per row on wide screens.
  - The breadcrumb reads "Reports / <tab>".
- **Access.**
  - Both APIs answer only a supervisor. An auditor or specialist who opens any Reports URL
    directly gets the "Supervisors only" state with no figures and no window line.
  - Non-supervisors do not see the Reports entries in the sidebar (session views,
    TAX-146).

## Results

**Page length at 1440×900** (TAX-144 budget 1.5; the TAX-149 baseline entries are
removed, so the over-budget baseline is now empty):

| Page | Before | After |
|---|---|---|
| Management, as supervisor | 1.94 (1743px) | 1.00 (every management tab) |
| Integrity, as supervisor | 1.64 (1479px) | 1.44 |
| Management and Integrity, as auditor (denied) | — | 1.00 |

**`tests/frontend/reports.spec.ts`** passes all 12 tests at 1440, 1024 and 390. It covers:
- each tab's URL, `aria-current` and labelled panel;
- the window line;
- axe, product names and the budget;
- the tabs keeping the reporting window, Enter on a focused tab with a visible focus, and
  Back;
- auditor and specialist denied on all six tabs by direct URL, with no Reports entry in
  the sidebar;
- loading, empty ("No cases to measure") and error states, with the tabs still present.

## Screenshots (1440, 1024 and 390)

| What | Files |
|---|---|
| Each tab, as supervisor | `tab-{workload,timeliness,evidence,outcomes,ledger-assurance,integrity}-{w}.png` |
| Loading, empty, error and denied states | `state-{loading,empty,error,denied}-{w}.png` |

## Verification

```bash
npm run check
```

```bash
python3 scripts/dev.py check
```

```bash
npm run test:auditor:browser -- --trace off
```

```bash
npx playwright test --config tests/frontend/playwright.config.ts --trace off
```

- **`npm run check`:** passes. Auditor vitest has 143 tests and taxpayer vitest 77.
- **`dev.py check`:** passes.
- **Auditor end-to-end:** 66 passed. `management.spec.ts` and `integrity.spec.ts` keep
  their assertions and now open the tab that holds each section.
- **Full frontend suite:** 332 passed and 22 skipped.
  - 9 failures are the demo recording harness, which needs a separate demo stack.
  - The officer-onboarding supervisor journey failed at all three widths, because it
    looked for "Past their due date" on the first page. That figure is now on the Evidence
    tab, so the journey opens that tab first. It then passed at all three widths.
