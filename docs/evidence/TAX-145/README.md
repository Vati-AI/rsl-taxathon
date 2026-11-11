# TAX-145 evidence: design tokens v2 and the shared Vati Workspace kit

Everything here is synthetic. The showcase uses made-up sample rows (DEMO-2xx), and the
taxpayer screens use the browser harness's session mocks. Browser: Chromium (headless,
Playwright 1.64, chromium build 1243), at the design-standard widths 1440, 1024 and 390.

## Contrast (measured)

`python3 scripts/check_contrast.py --markdown`, run by `tests/tooling/test_contrast.py` in
`python3 scripts/dev.py check`. The pairs are the ones the shared components and shells use
(`apps/shared/design/contrast-pairs.json`); values come from `apps/shared/design/tokens.css`.

| Use | Foreground | Background | Ratio | Minimum | Result |
|---|---|---|---|---|---|
| Body text on the page canvas | `--vati-text` #101828 | `--vati-canvas` #f7f8fa | 16.7:1 | 4.5:1 | pass |
| Body text on cards, tables, drawers | `--vati-text` #101828 | `--vati-surface` #ffffff | 17.75:1 | 4.5:1 | pass |
| Table header, hovered row | `--vati-text` #101828 | `--vati-surface-subtle` #f2f4f7 | 16.11:1 | 4.5:1 | pass |
| Page description, usage line | `--vati-text-muted` #475467 | `--vati-canvas` #f7f8fa | 7.23:1 | 4.5:1 | pass |
| Supporting text, empty value dash, key-fact labels | `--vati-text-muted` #475467 | `--vati-surface` #ffffff | 7.69:1 | 4.5:1 | pass |
| Column header info text | `--vati-text-muted` #475467 | `--vati-surface-subtle` #f2f4f7 | 6.98:1 | 4.5:1 | pass |
| Links and row actions | `--vati-accent` #1d4ed8 | `--vati-surface` #ffffff | 6.7:1 | 4.5:1 | pass |
| Links on the canvas | `--vati-accent` #1d4ed8 | `--vati-canvas` #f7f8fa | 6.31:1 | 4.5:1 | pass |
| Active tab and active sidebar item | `--vati-accent` #1d4ed8 | `--vati-accent-subtle` #eff6ff | 6.16:1 | 4.5:1 | pass |
| Primary button label | `--vati-surface` #ffffff | `--vati-accent` #1d4ed8 | 6.7:1 | 4.5:1 | pass |
| Primary button label, hover | `--vati-surface` #ffffff | `--vati-accent-hover` #1e40af | 8.72:1 | 4.5:1 | pass |
| Info status chip and notice | `--vati-info-text` #1e40af | `--vati-info-bg` #eff6ff | 8.01:1 | 4.5:1 | pass |
| Success status chip and notice | `--vati-success-text` #067647 | `--vati-success-bg` #ecfdf3 | 5.4:1 | 4.5:1 | pass |
| Warning status chip and notice | `--vati-warning-text` #93370d | `--vati-warning-bg` #fffaeb | 7.21:1 | 4.5:1 | pass |
| Danger status chip, inline error | `--vati-danger-text` #b42318 | `--vati-danger-bg` #fef3f2 | 6.05:1 | 4.5:1 | pass |
| Inline error on a surface | `--vati-danger-text` #b42318 | `--vati-surface` #ffffff | 6.57:1 | 4.5:1 | pass |
| Neutral status chip (not available) | `--vati-neutral-text` #344054 | `--vati-neutral-bg` #f2f4f7 | 9.49:1 | 4.5:1 | pass |
| Fixture-mode strip in the header | `--vati-fixture-text` #93370d | `--vati-fixture-bg` #fffaeb | 7.21:1 | 4.5:1 | pass |
| Dark navigation panel item | `--vati-nav-text` #e5e7eb | `--vati-nav-bg` #111827 | 14.33:1 | 4.5:1 | pass |
| Dark navigation panel, active item | `--vati-nav-text` #e5e7eb | `--vati-nav-active-bg` #1f2937 | 11.86:1 | 4.5:1 | pass |
| Dark navigation panel section label and count | `--vati-nav-text-muted` #9ca3af | `--vati-nav-bg` #111827 | 6.99:1 | 4.5:1 | pass |
| Focus ring on a surface | `--vati-accent` #1d4ed8 | `--vati-surface` #ffffff | 6.7:1 | 3.0:1 | pass |
| Focus ring on the canvas | `--vati-accent` #1d4ed8 | `--vati-canvas` #f7f8fa | 6.31:1 | 3.0:1 | pass |
| Focus ring in the dark navigation panel | `--vati-nav-text` #e5e7eb | `--vati-nav-bg` #111827 | 14.33:1 | 3.0:1 | pass |

Not claimed: input and divider borders (`--vati-border`, `--vati-border-strong`) are below
3:1 against white, as in frappe-ui; controls are identified by their labels and focus ring,
which is measured above.

## Screenshots

`CAPTURE_SCREENSHOTS=1 npx playwright test -c tests/frontend/playwright.config.ts kit.spec.ts`

| File | Shows |
|---|---|
| `kit-*.png` | The whole kit in the showcase: header with the fixture strip, sidebar (expanded, rail, dark), page header with status chip and a disabled action, notices, status chips, key facts, routed tabs, toolbar with a disabled Export, usage line, data table with sorting and pagination, and its loading, empty and error states, inline errors, skeleton and empty state |
| `table-focus-*.png` | Keyboard focus on a sortable column header after sorting by keyboard |
| `drawer-*.png` | Right-side drawer open (modal) |
| `confirm-*.png` | Confirmation dialog naming the action ("Send request") |
| `palette-*.png` | Command palette opened with Ctrl K, filtered by a TIN |
| `taxpayer-empty-*.png` | Taxpayer portal with the shared page header and empty state |
| `taxpayer-denied-*.png` | Taxpayer portal, membership revoked: denied state |

## Checks in `tests/frontend/kit.spec.ts` (all three widths)

- Every kit component renders in the showcase, with no serious or critical axe violations
  (WCAG 2.1 A/AA) and no page-wide horizontal scroll.
- Table: sorting by keyboard updates `aria-sort`, focus is visible, pagination moves to page
  2, and the row action is named with its row ("Open DEMO-201").
- Drawer: Tab stays inside the drawer, Escape closes it and focus returns to the opener;
  axe is clean with it open.
- Confirmation dialog: axe is clean while open; confirming shows a toast.
- Command palette: Ctrl K focuses the search, a TIN filters to one result, Enter chooses it;
  axe is clean while open.
- Taxpayer portal: shared page header and empty state, axe clean; denied state shown.
- Page length at 1440×900 (TAX-144 budget 1.5): My cases 1.0, Evidence requests 1.0; the
  showcase is 6.17 and exempt (TAX-144 baseline).

A second person has not reviewed these screens visually (optional under ADR-038).
