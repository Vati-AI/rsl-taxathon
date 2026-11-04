# TAX-042 review evidence

These screenshots are evidence for visual review (docs/DESIGN_STANDARD.md), not approved baselines. They were captured on 2026-11-04 from the built auditor app against the synthetic fixture server, in Microsoft Edge 156.0.4314.58 through Playwright (`PLAYWRIGHT_CHANNEL=msedge`):

```sh
CAPTURE_SCREENSHOTS=1 npm run test:auditor:browser -- review-screenshots
```

## Screens (1440, 1024 and 390px, full page)

| File | What it shows |
|---|---|
| `propose-referral-suggestion-*` | SYN-003 as an auditor: outcome choices, rationale, cited findings, investigator rating, the evidence request form, the agent suggestion and an empty review trail |
| `awaiting-approval-proposer-*` | SYN-001 as the auditor who proposed the clearance: Approve and Reject disabled, with the reason |
| `case-findings-tab-*` | SYN-002 case detail, Findings tab (missing evidence reported apart from a discrepancy) |

## States (1440 and 390px)

| File | What it shows |
|---|---|
| `state-confirm-referral-*` | Confirmation dialog for a specialist referral started from the agent suggestion |
| `state-refused-stale-view-*` | The same dialog after the server refused a stale view (`stale_case_version`), offering a reload and keeping the text |
| `state-validation-errors-*` | Submitting an empty proposal: errors next to the fields, focus on the first problem |
| `state-supervisor-approve-*` | SYN-001 as a different supervisor: Approve and Reject available |
| `state-supervisor-reject-reason-*` | The reject reason field that a rejection requires |
| `state-specialist-permissions-*` | SYN-002 as a specialist: no evidence requests or referrals, with the reasons |

The refusal in `state-refused-stale-view` is simulated in the browser so that capturing screenshots never changes fixture state. `e2e/review.spec.ts` produces the same refusal against the real fixture.

## Keyboard and accessibility checks

- `e2e/review.spec.ts` runs the referral flow by keyboard only: Enter on "Use as a starting point" fills the form; Enter on "Review and propose…" opens the dialog; Escape closes it and nothing is sent; Enter on "Propose decision" sends exactly one request.
- axe (WCAG 2 A/AA) finds no violations on the review page and with the confirmation dialog open (`e2e/review.spec.ts`).
- The TAX-023 harness (`tests/frontend`) checks the review page for serious axe violations and page-wide horizontal scroll at 1440, 1024 and 390px.
- Validation moves focus to the first invalid field; a refusal is announced in the dialog (`aria-live`); disabled actions are described by a visible reason (`aria-describedby`).

Not done: a zoom/reflow check at 200% beyond the 390px width, and screen reader testing. Visual approval is for the area owner, not the agent.
