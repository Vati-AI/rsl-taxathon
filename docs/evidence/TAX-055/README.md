# TAX-055 review evidence

End-to-end browser regression across both personas (`tests/frontend/scenarios.spec.ts`).
Screenshots are **review evidence, not approved baselines**; visual approval is
the human reviewer's call (docs/DESIGN_STANDARD.md). Synthetic data only; no
Frappe site, account or password was used.

## Environment (2026-11-05)

- Windows 11, installed Microsoft Edge (`PLAYWRIGHT_CHANNEL=msedge`),
  Playwright 1.64.1, axe-core via @axe-core/playwright.
- Branch `task/TAX-055` with current `main` merged (`f06d781`).
- Auditor: built app (`npm run build:auditor`) on `vite preview`. The five
  scenario tests and the forced-colours test each start **their own synthetic
  fixture API** (`scripts/dev.py serve`, unchanged) on a free port, because
  approvals and evidence requests change fixture state
  (`tests/frontend/support/fixture-api.ts`).
- Taxpayer: Vite dev server; portal API answered by the session mocks
  (`tests/frontend/support/sessions.ts`).

## What is covered, at 1440, 1024 and 390 px

Every state below is scanned with axe (WCAG 2.0/2.1 A and AA, serious and
critical) and checked for page-wide horizontal scrolling.

| Scenario (cases.json)            | Expected route              | Route walked                                                                                                                                                                                                                                                                      |
| -------------------------------- | --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SYN-001 `legitimate_refund`      | `human_clearance`           | Queue → case → Evidence → Findings → Review. The proposing auditor's Approve is disabled and described ("a different supervisor must approve"); acting as Supervisor, Approve → confirm dialog → "Decision approved", case **Resolved**, no further decision or request possible. |
| SYN-002 `related_party_risk`     | `targeted_evidence_request` | Same walk. "Use as a starting point" on the missing-agreement finding fills the request with that finding cited → confirm dialog → "Evidence request recorded", status stays **Investigating**, review trail updated.                                                             |
| SYN-003 `ambiguous_service`      | `specialist_review`         | Same walk. Finding shows "No verified legal route"; its suggestion pre-selects "Refer to a specialist"; officer writes the rationale → "Decision proposed". As Specialist, Approve/Reject are disabled with the reason; as Supervisor, approved.                                  |
| SYN-004 `imported_services_gap`  | `targeted_evidence_request` | Same walk. "Wait for further evidence" is disabled ("already in Awaiting evidence"). **Keyboard only**: suggestion → form → dialog; focus is inside the dialog; Escape cancels and keeps the draft; Enter confirms → recorded.                                                    |
| SYN-005 `unverified_legal_route` | `policy_gap_review`         | Same walk. Finding shows "No verified legal route" and "Review the policy gap with a supervisor". As Supervisor, Reject without a reason is refused and focus moves to the reason; with one → "Decision rejected… Nothing was sent to Jube", case unchanged and open again.       |

All five also check that the case summary shows the scenario's claim, that the
selection reasons are labelled as "not findings, assessments or decisions", that
suggestions are marked "(not legal authority)", and that no accusatory wording
(fraud, evasion, guilty, criminal, penalty, suspicious) appears in findings or
suggestions.

**Taxpayer journey** (the SYN-001 legitimate refund from the taxpayer side):
submissions list → New submission (via the sidebar, or the bottom bar at 390 px)
→ claim LSL 15,000.00 → two uploads (invoice and contract) → one invoice with
amount paid and contract → "Before you send" says "Your figures add up", with no
accusation or lane wording → declaration → "Claim sent" → Evidence requests →
answer the open request (one document, one reasoned decline) → "Response sent".

**Accessibility states**: forced colours (Windows high contrast) with reduced
motion: the supervisor approval dialog opened and confirmed by keyboard, focus
indicator visible on Approve and on the confirm button; the taxpayer "Before you
send" prompts with a visible focus indicator on "Change"; axe on both.

Not connected: in fixture mode the evidence request an officer records is not
delivered to the taxpayer mock; each portal is tested against its own synthetic
source. A connected run needs the Frappe and Vati services (TAX-058).

## Results

Full harness on the merged branch (`npm run test:frontend:browser -- --workers=2`,
all specs × 1440/1024/390): **153 passed, 0 failed, 0 flaky**, 9 skipped (the
opt-in visual tests). Before this task: 132 tests. The 21 new ones are
`scenarios.spec.ts`: 7 tests × 3 widths. `claim.spec.ts` now imports its steps
from `support/claim-flow.ts` and still passes unchanged at every width.

`results.json` is the Playwright report of the screenshot run, summarised:
**21/21 passed**, 0 flaky.

| Test                                     | 1440px | 1024px | 390px |
| ---------------------------------------- | ------ | ------ | ----- |
| SYN-001 legitimate refund                | pass   | pass   | pass  |
| SYN-002 related-party risk               | pass   | pass   | pass  |
| SYN-003 ambiguous service                | pass   | pass   | pass  |
| SYN-004 imported-services gap (keyboard) | pass   | pass   | pass  |
| SYN-005 unverified legal route           | pass   | pass   | pass  |
| Taxpayer journey                         | pass   | pass   | pass  |
| Forced colours and reduced motion        | pass   | pass   | pass  |

Lint, Prettier and `typecheck:frontend-tests` pass.

## Failures found and fixes

| #   | Found                                                                                                                                                                                                                                      | Cause                                                                                                                                                        | Fix                                                                                                                                                                                                                                                                                                                                                                  |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **Product (auditor), open.** SYN-005: axe `color-contrast` on the "Reason for rejecting" error, `#e03434` on white = **4.45:1** (needs 4.5:1, WCAG 1.4.3). All three widths.                                                               | frappe-ui's field error colour (`text-ink-red-5`). The taxpayer portal overrides it (TAX-021, `apps/taxpayer-web/src/styles.css`); the auditor app does not. | **Not changed here** (auditor frontend owns `apps/auditor-web`). Recorded in `KNOWN_A11Y_ISSUES` for this one element so the rest of the page stays checked. Proposed fix for the owner: add `#app [data-slot='error'] { color: var(--ink-red-8); }` to `apps/auditor-web/src/style.css`, then remove the entry. Other auditor field errors are likely affected too. |
| 2   | Harness: the outcome message was not found as an `alert`.                                                                                                                                                                                  | The review panel announces results through a polite live region (`role="status"`), which is correct for non-urgent confirmations.                            | Test now waits for the `status` region.                                                                                                                                                                                                                                                                                                                              |
| 3   | Harness: at 390 px the "Evidence requests" link was not found.                                                                                                                                                                             | The bottom bar uses shorter labels ("New", "Requests").                                                                                                      | Test uses the visible "Taxpayer portal" navigation with the label for that width.                                                                                                                                                                                                                                                                                    |
| 4   | Harness: no visible focus on "Change" under forced colours.                                                                                                                                                                                | The test focused it by script right after a mouse click, so `:focus-visible` did not apply.                                                                  | Reached by keyboard (Shift+Tab, Tab) as a keyboard user would; the indicator is visible.                                                                                                                                                                                                                                                                             |
| 5   | Harness risk: axe run while a dialog fades in can report false contrast failures (seen and fixed in the auditor's own e2e, #183).                                                                                                          | Mid-transition colours.                                                                                                                                      | `seriousAxeViolations` now waits until no finite animation is running, for every spec. The first version also waited for looping ones and timed out on `/auditor/showcase` (spinners, skeletons) at all widths; looping animations are now ignored.                                                                                                                  |
| 6   | **Product (auditor), open.** SYN-001 after approval (visible in `syn-001-7-resolved-*`): the disabled "Use as a starting point" says "Another decision on this case is waiting for approval." The case is resolved and nothing is waiting. | `SuggestionList.vue:30` gives one reason whenever `canPropose` is false, but `DecisionPanel.vue` passes `!openDecision && !resolved`.                        | **Not changed here** (auditor frontend). Not asserted, so the test does not lock in the wrong text. Proposed fix for the owner: pass the reason (or `resolved`) to `SuggestionList` and say "This case is resolved." when it is.                                                                                                                                     |

## Screenshots

`CAPTURE_SCREENSHOTS=1 npm run test:frontend:browser -- scenarios` writes 132
PNGs here, suffixed `-1440`, `-1024` and `-390`:

- `syn-00N-1-case`, `-4-review` and the final outcome (`syn-001-7-resolved`,
  `syn-002-7-recorded`, `syn-003-8-referred`, `syn-004-5-recorded`,
  `syn-005-7-rejected`): whole page.
- `-2-evidence`, `-3-findings`: the tab panel.
- In-progress states (`syn-001-5-supervisor`, `syn-002-5-request-form`,
  `syn-003-5-propose-form`, `syn-003-7-specialist`, `syn-005-5-reject-reason`):
  the panel being used.
- Confirm dialogs (`*-dialog`): the dialog.
- `taxpayer-1` to `taxpayer-7`: the journey, whole page.
- `forced-colors-approve-dialog`, `forced-colors-claim-checks`: forced colours.
