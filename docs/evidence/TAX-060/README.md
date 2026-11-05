# TAX-060 review evidence: management dashboard

These screenshots are for the reviewer's visual check against docs/DESIGN_STANDARD.md. They are not approved baselines, and passing tests do not count as visual approval. All data is synthetic, from `integration/api/fixtures/auditor_queue.json` and `management.json`, served by `scripts/dev.py serve` in fixture mode.

They were captured with `CAPTURE_SCREENSHOTS=1 npm run test:auditor:browser -- management` in Microsoft Edge (`PLAYWRIGHT_CHANNEL=msedge`) on Windows, on 2026-11-04.

| File | State |
|---|---|
| `dashboard-1440.png`, `dashboard-1024.png`, `dashboard-390.png` | Acting as supervisor (banner "Acting as (fixture)": Supervisor), last 30 days, at the three standard widths |
| `loading-1440.png` | Loading |
| `denied-1440.png` | Acting as specialist: the server refuses with 403, no figures are shown, the window control is disabled and the "Supervision" nav entry is hidden |
| `empty-1440.png` | Nothing to measure: "No data" instead of 0%, and empty tables explained |
| `ledger-broken-1440.png` | Audit ledger check failed: red badge and a `ledger_integrity` review signal |

## What the figures show

These are the 30-day figures from the fixture, checked in `tests/contracts/test_management_summary.py`:
- 9 open cases, 1 not assigned to an officer.
- Closed with no further action: 3 of 6 closed cases with a recorded outcome. A 7th closed case has no recorded outcome and is listed as missing data.
- Legal routes verified: 3 of 7 findings.
- 3 outstanding evidence requests, 1 of them overdue.
- The ledger is intact. Supervisor C sees one review signal for officer A's repeated refused self-approvals. C's own repeated overrides are withheld from C and only counted ("1 signal concerns you").

The acting officer comes from TAX-042's fixture-only role switch. The auditor (the default) and the specialist are refused by the server and don't see the nav entry.

## Accessibility and keyboard checks

- `apps/auditor-web/e2e/management.spec.ts`: axe (WCAG 2 A/AA) finds no serious or critical violations at 1440, 1024 and 390px, and the page doesn't scroll sideways. The window control and each table region can be reached with the keyboard, and focus is visible on the table regions.
- `tests/frontend` (TAX-023 harness): the `management` route is in the matrix, and the axe and overflow checks pass at all three widths.

## Layout notes

- Tables sit in labelled regions that can take focus. At 390px the elapsed-time table scrolls inside its own region, as the design standard allows, and the page itself doesn't scroll sideways.
- There are no charts. The standard rules out decorative charts, so figures appear as labelled values and tables that work without colour, and every ratio is shown with its denominator.
- Status badges reuse `CaseStatusBadge`, which shows the status as text as well as colour.
