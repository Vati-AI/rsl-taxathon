# TAX-104 evidence: auditor defects found by TAX-055

Browser: Microsoft Edge (Playwright `PLAYWRIGHT_CHANNEL=msedge`), Windows 11.
Data: synthetic fixture cases only (`scripts/dev.py serve`, fixture mode).
Recorded 2026-11-05.

## 1. Field error contrast (TAX-055 issue 1)

**Cause.** frappe-ui renders field errors (`[data-slot='error']`, `InputError.vue`)
with `text-ink-red-5`, `#e03434`. On white that is 4.45:1, below the 4.5:1 that
WCAG 1.4.3 requires.

**Fix.** `apps/auditor-web/src/style.css` now sets
`#app [data-slot='error'] { color: var(--ink-red-8); }`, the same rule the
taxpayer portal uses (`apps/taxpayer-web/src/styles.css`, TAX-021). The
`KNOWN_A11Y_ISSUES` entry for `#reject-reason-error` is removed from
`tests/frontend/support/checks.ts` (and from `tests/frontend/README.md`), so the
TAX-055 SYN-005 axe scan now checks that element again.

**Measured in the browser** (axe-core `color-contrast` and computed style):

| Field error                                        | Colour    | Background | Ratio  | axe  |
| -------------------------------------------------- | --------- | ---------- | ------ | ---- |
| SYN-005 "Reason for rejecting" (1440, 1024, 390)   | `#941f1f` | `#ffffff`  | 8.48:1 | pass |
| Propose a decision: rationale                      | `#941f1f` | `#ffffff`  | 8.48:1 | pass |
| Request evidence: message to the taxpayer          | `#941f1f` | `#ffffff`  | 8.48:1 | pass |
| Showcase: case reference, refund lane, confirm box | `#941f1f` | `#ffffff`  | 8.48:1 | pass |
| Same rule on the page canvas (worst case)          | `#941f1f` | `#f8fafc`  | 8.1:1  | pass |
| Before the fix, `text-ink-red-5` on the canvas     | `#e03434` | `#f8fafc`  | 4.26:1 | fail |

Other auditor error text does not use frappe-ui's slot: "Choose an outcome.",
"A clearance must cite at least one finding." and the evidence-request
finding and document errors use `text-ink-red-7`,
`#b41d1d`, measured 6.4:1 on the canvas (higher on white). They already pass and
are unchanged. The two reds differ slightly in shade (visible in
`field-error-propose-1440.png`); both pass, and aligning them is a possible
tokens follow-up, not part of this fix.

Screenshots:

- `field-error-reject-reason-1440.png`, `-1024.png`, `-390.png`: SYN-005, acting
  as supervisor, "Review rejection…" with no reason.
- `field-error-propose-1440.png`: empty "Review and propose…" (both error styles).
- `field-error-evidence-request-1440.png`: empty "Review request…".

New regression check: `apps/auditor-web/e2e/smoke.spec.ts`, "field errors meet
4.5:1 text contrast (TAX-104)", runs axe `color-contrast` on the three showcase
field errors. With the `style.css` rule removed it fails; with it, it passes.

## 2. Disabled suggestion reason on a resolved case (TAX-055 issue 6)

**Cause.** `DecisionPanel.vue` passed `canPropose = !openDecision && !resolved`,
and `SuggestionList.vue` explained every `false` as "Another decision on this
case is waiting for approval."

**Fix.** `DecisionPanel.vue` computes the reason (`proposeBlocked`): "This case is
resolved." when the case is resolved, otherwise "Another decision on this case is
waiting for approval." when one is open, otherwise none. `SuggestionList.vue`
shows that reason (prop `proposeBlocked: string | null` replaces `canPropose`).
Permission reasons still come first, as before.

Screenshots:

- `resolved-suggestion-1440.png`, `-1024.png`, `-390.png`: SYN-001 after a
  different supervisor approves the clearance. The button is disabled and says
  "This case is resolved."
- `waiting-suggestion-1440.png`: SYN-003 after an auditor proposes a referral.
  The button still says "Another decision on this case is waiting for approval."

Tests asserting the text (accessible description of the disabled button):

- `apps/auditor-web/src/components/DecisionPanel.test.ts`: two new unit tests,
  one per reason.
- `apps/auditor-web/e2e/review.spec.ts`: after the approval that resolves case 1.
- `tests/frontend/scenarios.spec.ts`: SYN-001 after approval (TAX-055 suite), at
  1440, 1024 and 390.

## Commands and results

| Command                                                                  | Result                                                          |
| ------------------------------------------------------------------------ | --------------------------------------------------------------- |
| `npm run check` (lint, Prettier, typechecks, unit tests, builds)         | pass; auditor 113 unit tests, taxpayer 72                       |
| `npm run test:auditor:browser` (Edge)                                    | 66 passed, 17 skipped (screenshot-only specs)                   |
| `npm run test:frontend:browser -- scenarios --workers=2` (TAX-055 suite) | 21 passed                                                       |
| `npm run test:frontend:browser -- --workers=2` (whole tests/frontend)    | 195 passed, 9 skipped (visual baselines and capture-only specs) |
| `python scripts/validate.py`                                             | pass                                                            |

With the default worker count, one scenarios run had two timeouts in SYN-003
(1440) and SYN-004 (390) while waiting for dialogs; neither reaches the changed
states (resolved case, field errors), and the same suite passed 21/21 with two workers. mypy and `scripts/dev.py check` were
not run locally (Smart App Control blocks mypy; `dev.py` calls `python3`, which is
not on PATH here); no Python changed.

Screenshots were taken by a temporary Playwright script against the built app
and the review fixture server; the script is not committed.

A second look at these screens (Kananelo or Keletso) is requested on the PR;
under ADR-038 it is optional and does not block merge. This record does not
claim visual approval.
