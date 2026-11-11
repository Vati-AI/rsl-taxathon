# TAX-146 evidence: the Vati Workspace app shell

Everything is synthetic. The auditor app runs against the fixture API (`scripts/dev.py serve`),
whose `GET /api/v1/session` answers for the `X-Vati-Fixture-Role` officer. The taxpayer
portal uses the browser harness's session mocks. Browser: Chromium (headless, Playwright
1.64, chromium build 1243), at 1440, 1024 and 390px.

`CAPTURE_SCREENSHOTS=1 npx playwright test -c tests/frontend/playwright.config.ts shell.spec.ts`

| File | Shows |
|---|---|
| `auditor-overview-*.png` | Auditor shell: header (Cases, Investigation and Explore marked not connected), fixture strip and role switch, sidebar; at 390 the Menu drawer is open |
| `auditor-not-connected-*.png` | The honest "Investigation is not connected yet" page |
| `supervisor-management-*.png` | A supervisor: Reports tab and the supervision views |
| `auditor-rail-*.png` | Sidebar collapsed to the 56px icon rail (1024 and 1440) |
| `auditor-drawer-390.png` | Below 1024px: the navigation drawer |
| `auditor-palette-*.png` | Command palette, a masked TIN ("4102") finds the taxpayer |
| `auditor-loading-*.png` | Navigation loading (sidebar skeleton) |
| `auditor-error-*.png` | Session failed to load: inline error with retry |
| `auditor-denied-*.png` | Another tenant's scope: access denied, no navigation |
| `taxpayer-requests-*.png` | Taxpayer portal shell (simpler variant), Evidence requests |
| `taxpayer-account-menu-*.png` | Account menu with Log out |
| `taxpayer-loading-*.png` | Portal loading |
| `taxpayer-denied-*.png` | Membership revoked: access denied |

## Checks (`tests/frontend/shell.spec.ts`, 33 passed, 6 width-specific skips)

- Every auditor and taxpayer route renders inside the shell; content is at most 1280px wide
  and starts at the sidebar's right edge from 1024px.
- Tabs and sidebar follow the session: an auditor sees Cases, Investigation and Explore (the
  last two "not connected") and no Reports; a supervisor also sees Reports, Management
  dashboard and Integrity review. The showcase is not in the navigation; it is under Help.
- "Back to cases" appears in case context.
- The sidebar collapses to a 56px rail, item names stay available to screen readers, and
  the choice survives a reload.
- Below 1024px the Menu button opens a modal drawer: Tab stays inside, Escape closes it and
  returns focus to Menu, and following a link closes it; axe is clean while open.
- Ctrl K opens the palette; a masked-TIN search and a case reference each open their page by
  keyboard alone; a page the officer may not open (Management, for an auditor) is not offered;
  axe is clean while open.
- Loading, error with retry, and denied states for both apps; axe is clean on the error state.
- Page length at 1440×900 (TAX-144 budget 1.5): Overview 1.0, My case queue 1.0, taxpayer
  profile 1.0, taxpayer My cases 1.0, Evidence requests 1.0, Account 1.0.

A second person has not reviewed these screens visually (optional under ADR-038).
