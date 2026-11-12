# TAX-147 evidence: officer home, case queue and taxpayer profile

## What changed

### Saved views with real counts
- **Sidebar views:** the sidebar's Cases section now lists My cases, Awaiting evidence,
  Awaiting approval and Overdue (`/auditor/cases?view=…`). Each count is the number of
  rows that view shows.
- **One shared list:** the sidebar, the overview and the queue read the same case list
  (`useCaseList`, `GET /api/v1/cases`), so their numbers match.
- **Where the data comes from:** Awaiting approval and Overdue need data the case list
  did not carry, so the contract gains an optional, server-computed `Case.attention`:
  - `decision_awaiting_approval`;
  - `outstanding_evidence_requests`;
  - `evidence_due_date`;
  - `overdue`: an outstanding request is past due.
- **No guessed numbers:** a server that omits `attention` gets no count for those views,
  and the view says it is not available.
- **Fixture data:** the fixture server computes `attention` from its review state, with
  two synthetic evidence requests seeded in `case_review.json`: SYN-004 three days past
  due, SYN-002 due in nine days.
- **Tests:**
  - `tests/contracts/test_case_attention.py` checks the fixture responses against the
    contract.
  - `domain/queue.test.ts` checks the counts, and that a view without `attention` gives
    no count.

### Queue
- **Toolbar** (one row at 1440): search, status and refund-lane filters, Display options
  (columns shown, kept in this browser), Export CSV (exactly the visible columns, values
  quoted, a leading formula character neutralised) and one primary action, "Open first
  case".
- **Usage line:** "Showing 7 of 7 cases · 1 overdue · 2 awaiting approval".
- **Table:** the kit `DataTable` is dense and sortable, with `aria-sort`, column help on
  Why selected, Status and Evidence due, muted dashes for empty values, and an "Open" row
  action.
- **URL state:** view, search, filters, sort and page live in the URL and survive a
  reload.

### Overview
- **Needs your action:** four action items (Overdue, Awaiting approval, Awaiting
  evidence, Processing failed), each with its count, the first three cases and "View all"
  to the matching queue view.
- **By status:** below them, the assigned cases by status.

### Taxpayer profile
- **Key facts:** type, masked TIN, registration date, sector and the open-case count.
- **Open cases:** a toolbar, a usage line and a table with an "Open" row action.

## Results

**Page length at 1440×900** (budget 1.5 viewports):

| Page | Viewports |
|---|---|
| Overview | 1.00 |
| Queue | 1.00 (the table scrolls inside its labelled region) |
| Taxpayer profile | 1.00 |

**`tests/frontend/queue.spec.ts`:** 17 tests pass at 1440, 1024 and 390. One is skipped
at 390 by design: the download check runs once, at desktop and tablet. It covers:
- every sidebar view count equals its rows and the usage line;
- one toolbar row at 1440 (it wraps when narrower, as the kit toolbar is designed to);
- search and sort survive a reload;
- column help;
- the Open row action reached by keyboard, with a visible focus;
- Display options and the CSV header;
- the CSV row count;
- the overview action items match the views;
- the taxpayer table;
- loading, empty, error and denied states;
- axe, product names and the page-length budget.

## Screenshots (1440, 1024 and 390)

| What | Files |
|---|---|
| Each saved view | `view-{my-cases,awaiting-evidence,awaiting-approval,overdue}-{w}.png` |
| Display options | `display-options-{w}.png` (1440 and 1024) |
| Overview | `overview-{w}.png` |
| Taxpayer profile | `taxpayer-{w}.png` |
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
- **`dev.py check`:** passes, including the contract tests and the new
  `test_case_attention`.
- **Auditor end-to-end:** 66 passed. `smoke.spec.ts` was updated for the sidebar view
  name ("My cases") and the overview's Processing failed action item.
- **Full frontend suite:** 323 passed, 22 skipped and 9 failed. All 9 failures are the
  demo recording harness, which needs a separately started demo stack.
- **Spec changes:**
  - `auditor`, `shell` and `onboarding` specs: the sidebar link is now "My cases".
  - Onboarding: the usage line now reads "Showing 1 of 1 cases".
