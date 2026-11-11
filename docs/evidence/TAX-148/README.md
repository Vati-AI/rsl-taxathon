# TAX-148 evidence: the case workspace

The case detail, evidence and review pages are now one case workspace
(`apps/auditor-web/src/views/CaseWorkspaceView.vue`).

## What changed

- **Header.** The page header shows the case title, the status chip and one primary
  action ("Review and decide", not repeated on the Review tab). Below it, a key-facts
  strip shows the taxpayer, refund claimed, refund lane, opened, last updated and case
  version. The case and its records load once and are passed to the open tab.
- **Routed tabs, each with its own URL and a back-button history entry:**

  | Tab | URL | Content |
  |---|---|---|
  | Summary | `/cases/:id` | Why the case was selected, the taxpayer, processing by service |
  | Evidence | `/cases/:id/evidence` | List of documents beside a preview pane |
  | Evidence, one document | `/cases/:id/evidence/:evidenceId` | That document open in the preview pane |
  | Relationships | `/cases/:id/relationships` | Relationship view |
  | Findings | `/cases/:id/findings` | Findings |
  | Review | `/cases/:id/review` | Review actions and trail |

  - Each tab's content is a region named after the tab.
  - Links from before TAX-148 (`?tab=evidence`) redirect to the new tab, and the
    `finding` query is kept.
- **Evidence tab.**
  - The documents list (version, extraction state, fingerprint, citation count, "replaced
    by") sits beside the preview pane.
  - The preview shows the document record, SHA-256, versions, the cited location and the
    findings that cite the document. It was the separate evidence page before.
  - Below 1024px the list and the preview take turns, with an "All evidence" link back.
- **Review tab.**
  - Layout: the decision state, evidence requests and the review trail are on the left;
    the suggestions to consider are on the right.
  - **Propose a decision…** and **Request evidence…** open right-side drawers
    (`SideDrawer`, size `lg`) that hold the existing forms.
  - "Use as a starting point" opens the matching drawer, prefilled, with focus on the
    prefilled part. The drawer's open-auto-focus hook places it, rather than a timer, so
    it cannot take focus away while someone is already typing.
  - The existing confirmation dialogs open over the drawer. Escape closes only the
    confirmation and the drawer keeps the draft. After the action is recorded, the drawer
    closes.
  - Closing a drawer discards its draft, like closing any dialog.
  - Each suggestion shows its explanation and next step; the rest of the finding is under
    "Finding details".
- **Findings tab.** Groups sit two per row on wide screens. "Other possible
  explanations" is behind a disclosure on each card.
- **Review rules are unchanged** (DecisionPanel logic, API calls, idempotency keys,
  expected versions):
  - separation of duties holds: the proposer cannot approve;
  - every action is confirmed in a dialog first;
  - a disabled action states its reason;
  - no decision is made without a human.
- **Names.** Selection reasons, processing state and sources use the TAX-150 register;
  RSL values keep RSL's labels. Service identifiers are shown as `ProvenanceValue`.

## Results

**Page length at 1440×900** (TAX-144 budget 1.5 viewports), from
`tests/frontend/layout.spec.ts`. The TAX-148 entries are removed from
`support/layout-baseline.json`.

| Tab | Before | After |
|---|---|---|
| Summary | 1.00 | 1.00 |
| Review | 2.26 (2031px) | 1.46 (1311px) |
| Review, as supervisor | 2.26 (2031px) | 1.46 (1311px) |
| Findings | 1.95 (1751px) | 1.42 (1281px) |
| Evidence | — | 1.03 |
| Evidence, one document | — | 1.31 |
| Relationships | — | 1.48 |

Relationships and Review are within the budget but close to it. More relationship
records would push Relationships over; TAX-144's check will catch that.

**Product names:** none appear on any case tab (TAX-150 check).

`tests/frontend/case-workspace.spec.ts` passes all 15 tests at 1440, 1024 and 390. It
covers:
- the tab URLs and `aria-current`;
- back-button history;
- key facts and the status chip;
- the list and preview arrangement, side by side or taking turns below 1024px;
- the drawers: focus moves in, and Escape returns focus to the button that opened them;
- the prefilled evidence request;
- the confirmation over the drawer keeping the draft;
- loading, empty (SYN-007), error (records service 503) and denied states;
- axe with no serious violations;
- product names;
- the page-length budget at 1440.

## Screenshots (1440, 1024 and 390)

| What | Files |
|---|---|
| Summary, Evidence, Relationships, Findings and Review tabs | `tab-{summary,evidence,relationships,findings,review}-{w}.png` |
| Evidence preview | `evidence-preview-{w}.png` |
| Review tab | `review-{w}.png` |
| Propose drawer | `drawer-propose-{w}.png` |
| Confirmation over the request drawer | `drawer-confirm-{w}.png` |
| Loading, empty, error and denied states | `state-{loading,empty,error,denied}-{w}.png` |

## Verification

```bash
npm run check
```

```bash
npm run test:auditor:browser -- --trace off
```

```bash
npx playwright test --config tests/frontend/playwright.config.ts --trace off
```

- **`npm run check`:** passes. Auditor vitest has 137 tests; `DecisionPanel.test.ts` was
  updated for the drawers with the same assertions. Taxpayer vitest has 77.
- **Auditor end-to-end:** 66 passed. Updated specs:
  - `evidence.spec.ts`: tab links, the preview pane, headings now level 2;
  - `review.spec.ts`: opens the propose drawer before using the form;
  - `smoke.spec.ts`: tab links and URLs;
  - `review-mobile-a11y.spec.ts`: the case tabs replace "Back to case".
- **Full frontend suite (last run):** 304 passed and 11 failed.
  - 9 are the demo recording harness, which needs a separately started demo stack on
    ports 58080/58082 and fails before loading a page.
  - 2 failed once at 390px under full-suite load: officer onboarding (keyboard only) and
    SYN-003 (axe ran while a dialog was still fading out). Both passed 3 of 3 when re-run
    alone, and both passed at 390 in the previous full run.
  - The SYN-004 failure from earlier in this task (focus moving while text was typed)
    was fixed through the drawer's open-auto-focus hook. Afterwards it passed 9 of 9 runs
    (3 repeats at each width).
