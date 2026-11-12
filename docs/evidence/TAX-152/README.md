# TAX-152 evidence: the taxpayer portal's evidence-request inbox

The re-centred portal (TAX-125) is now an inbox, on the Vati Workspace shell (TAX-146).

## What changed

### Inbox
- **Path:** `/taxpayer/requests` lists the taxpayer's own evidence requests like
  messages, grouped by reply-by date (`src/lib/inbox.ts`, with unit tests):
  1. past the date;
  2. within 7 days;
  3. later;
  4. no date;
  5. answered;
  6. no longer needed.
- **Each row:** the requested documents ("Tax invoice and 1 more"), the item count, the
  reply-by date and a taxpayer-facing status.

### Reading pane
- **Desktop:** the list stays beside a reading pane, and `/taxpayer/requests/:id` opens
  one request there. The open request is marked `aria-current` in the list.
- **Below 1024px:** the list and the request take turns, with an "All evidence requests"
  link back.

### Response steps
- **Steps:** a response is a short sequence of steps: one per requested item (upload the
  document, or say why it cannot be provided), then "Review and send" (what each item will
  send, an optional message, Send).
- **Step buttons:** the steps are buttons with `aria-current="step"`, plus a tick for an
  answered item and an alert icon for an item with a problem.
- **Fits one viewport:** each step fits one viewport at every width.
- **Nothing is lost:**
  - every step stays mounted, so moving between steps keeps what was entered;
  - sending checks every item and opens the first item that still needs an answer,
    with focus on its heading and the problem announced;
  - the other answers and the message are kept.
- **Unchanged:** the TAX-045 rules, the idempotent send and the server's refusals. The
  TAX-126 pre-send checks will run in the review step.

### Unchanged parts of the portal
- **My cases:** still read-only. A claim read from RSL shows RSL's status, labelled as
  RSL's (TAX-125).
- **Account:** unchanged.
- **Wording and layout:** the portal has no starter copy, hero titles or card grids. The
  content area is wider so the list and reading pane fit side by side; My cases, a case
  and the notices keep their earlier width.

### What taxpayers see
- **Hidden from taxpayers:** officer navigation, risk signals and component names (the
  TAX-150 naming check, and this spec).
- **Footer:** links the open-source notices.
- **Login and fixture mode:** the portal uses the live Frappe login (the `/taxpayer` page
  guard, TAX-029). Fixture mode shows only as the "Synthetic data" strip in the header.

## Results

**Page length at 1440×900** (budget 1.5, `layout.spec.ts`):

| Page | Viewports |
|---|---|
| Inbox | 1.00 |
| Open request | 1.00 |
| My cases | 1.00 |
| Case status | 1.00 |
| Account | 1.00 |
| Notices | 1.23 |
| Online Refunds simulator (fixture tool) | 1.44 |
| Not found | 1.00 |

No portal screen shows a component name.

**`tests/frontend/inbox.spec.ts`** passes all 9 tests at 1440, 1024 and 390. It covers:
- the grouping;
- the fixture strip and footer link;
- no officer navigation, risk wording or component names;
- a request opened by keyboard with visible focus;
- the list beside the pane on desktop, and list then detail on a phone;
- each step within one viewport;
- values kept after sending with an unanswered item;
- loading, empty, error and denied states;
- axe and the budget.

## Screenshots (1440, 1024 and 390)

| What | Files |
|---|---|
| Inbox | `inbox-{w}.png` |
| An open request | `request-{w}.png` |
| Each step | `step-{item-1,item-2,review-and-send}-{w}.png` |
| An error that kept the values | `step-error-{w}.png` |
| Loading, empty, error and denied states | `state-{loading,empty,error,denied}-{w}.png` |

## Verification

```bash
npm run check
```

```bash
python3 scripts/dev.py check
```

```bash
npx playwright test --config tests/frontend/playwright.config.ts --trace off
```

- **`npm run check`:** passes. Auditor vitest has 143 tests and taxpayer vitest 79
  (including `inbox.test.ts`).
- **`dev.py check`:** passes.
- **Full frontend suite:** 335 passed, and the only remaining failures are the 9 demo
  recording harness tests, which need a separate demo stack.
  - That run first failed 9 taxpayer journeys: onboarding with a pointer, onboarding by
    keyboard, and the taxpayer scenario, each at three widths. They answered a request on
    the old one-page form.
  - They now move through the steps, with the same assertions, and pass at all widths.
  - `responses.spec.ts` was updated in the same way and passes all 24 tests.
- **After moving onto the latest main:** the inbox, response, taxpayer and notices specs
  (120 tests) pass again.
