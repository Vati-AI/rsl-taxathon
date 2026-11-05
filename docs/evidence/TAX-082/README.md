# TAX-082 review evidence: persona onboarding walkthroughs

Can a first-time taxpayer, auditor, supervisor or specialist reach their goals
through the screens alone? Six scripted proxy personas were measured at the
three design-standard widths. All data is synthetic. No real participants
took part and no personal data was collected. Screenshots are review evidence,
not approved baselines.

## Method

- **Scripted proxies, not people.** Each persona (`tests/frontend/onboarding.spec.ts`)
  starts at its portal's entrance (`/taxpayer/` or `/auditor/`). After that it
  never types a URL or uses an internal ID: it only uses links, buttons and
  fields found by their accessible names, as a first-time user would.
- **Measured per goal:**
  - completion
  - actions (clicks, fills, selections, uploads, key activations)
  - Tab stops (keyboard personas)
  - characters typed
  - a Keystroke-Level Model estimate (`tests/frontend/support/proxy.ts`).
- **What the estimate means.** It uses typical expert operator times. It leaves
  out reading and system response, so it compares walkthroughs and doesn't
  predict real task time.
- **Friction is recorded, not asserted.** A walkthrough fails only when a goal
  can't be completed. Friction is either detected by the script (for example
  more than 15 Tab stops to reach a control, or a page that says nothing about
  an open request) or observed in the run.
- **Sources:**
  - Taxpayer: the session mocks (`support/sessions.ts`).
  - Auditor: the fixture API (`scripts/dev.py`), a fresh instance per walkthrough.
  - axe (WCAG 2.1 A/AA, serious or critical) and the reflow check run on the
    main states. A violation would be recorded as friction; none were found.
    TAX-104's known contrast defect stays filtered as before.

Raw measurements: `metrics/<persona>-<width>.json`.

## Personas and results (2026-11-05, Windows 11, Microsoft Edge)

| Persona                     | Who                                                                                        |
| --------------------------- | ------------------------------------------------------------------------------------------ |
| `taxpayer-first-visit`      | A business owner using the portal for the first time, with a mouse or touch                |
| `taxpayer-keyboard`         | A taxpayer who uses the keyboard only (a proxy for screen-reader and motor-impaired users) |
| `auditor-first-case`        | A newly appointed auditor working a first case                                             |
| `auditor-keyboard`          | An auditor who works with the keyboard only                                                |
| `supervisor-first-day`      | A supervisor who must find and approve a waiting decision, then check workload             |
| `specialist-first-referral` | A specialist asked for an opinion                                                          |

**Every goal was completed at every width (18 of 18 walkthroughs).**

| Persona                   | Goal                                                                  | Done (1440/1024/390) | Actions | Tab stops | KLM estimate (s) |
| ------------------------- | --------------------------------------------------------------------- | -------------------- | ------- | --------- | ---------------- |
| taxpayer-first-visit      | Find out where my submission is and what happens next                 | yes/yes/yes          | 1       | –         | 2.7              |
| taxpayer-first-visit      | Answer the evidence request an officer sent                           | yes/yes/yes          | 6       | –         | 32.9             |
| taxpayer-first-visit      | Send a VAT refund claim with one invoice                              | yes/yes/yes          | 18      | –         | 90.3             |
| taxpayer-first-visit      | Find the claim I just sent in my submissions                          | yes/yes/yes          | 1       | –         | 2.7              |
| taxpayer-first-visit      | Find my account details and how to log out                            | yes/yes/yes          | 1       | –         | 2.7              |
| taxpayer-keyboard         | Open my first submission and read what happens next                   | yes/yes/yes          | 2       | 3         | 4.5              |
| taxpayer-keyboard         | Answer an evidence request without a mouse                            | yes/yes/yes          | 5       | 23/23/21  | 35.3/35.3/34.8   |
| auditor-first-case        | Find the case for Synthetic Border Retail and see why it was selected | yes/yes/yes          | 3       | –         | 12.4             |
| auditor-first-case        | Open the evidence behind the case                                     | yes/yes/yes          | 3       | –         | 8.0              |
| auditor-first-case        | Look at the taxpayer's profile                                        | yes/yes/yes          | 2       | –         | 5.3              |
| auditor-first-case        | Ask the taxpayer for the missing service agreement                    | yes/yes/yes          | 6       | –         | 44.9             |
| auditor-keyboard          | Search for Synthetic Highland Imports and open its case               | yes/yes/yes          | 2       | 20        | 15.1             |
| auditor-keyboard          | Request the missing import records                                    | yes/yes/yes          | 4       | 44        | 50.9             |
| supervisor-first-day      | Find a decision that is waiting for my approval                       | yes/yes/yes          | 4       | –         | 11.9             |
| supervisor-first-day      | Approve the decision                                                  | yes/yes/yes          | 2       | –         | 5.3              |
| supervisor-first-day      | Find how many evidence requests are overdue                           | yes/yes/yes          | 1       | –         | 2.7              |
| specialist-first-referral | Find cases referred to me and understand what I can do                | yes/yes/yes          | 4       | –         | 11.9             |

The taxpayer portal is efficient for these goals:

- Status is one action from the entrance, or 3 Tab stops by keyboard.
- Every portal goal is reachable from the navigation at all widths, including
  the mobile bottom bar.
- The keyboard proxy reached every control and saw visible focus.

## Findings and proposed remediation

These are proposals for the team lead to turn into tasks. This PR doesn't
create tasks or assign owners. Where an existing task already covers a finding,
it is named.

| ID  | Priority | Finding                                                                                                                                                                                                                                                                                                                                                                                   | Proposed remediation                                                                                                                                                                                        | Area / task                                 |
| --- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| F1  | P1       | **Clicking a search result can be ignored.** On the auditor queue, an officer types a search and clicks a result, but stays on the queue. Reproduced 8/8 with 300 ms or 600 ms emulated network latency, 0/8 at 0 or 150 ms (details below). The proxy also hit it in 4 of 5 development runs at 1440 px under parallel load. It didn't hit it at 1024 or 390 px, or in the recorded run. | In `CaseQueueView.vue`, ignore a search update whose value equals the current `q` (the blur repeats the last value). Add a regression test that clicks a result under emulated latency.                     | auditor-web (queue, TAX-040 area); new task |
| F2  | P1       | **Supervisors can't see which cases wait for their approval.** The overview and queue show all three cases as "Awaiting officer review", whether or not a decision is waiting for approval. The supervisor has to open each case's review page to find out. The proxy found one first only because SYN-001 sorts first; in the worst case that is 3 cases and 6 page loads.               | Add a "Waiting for approval" signal for supervisors: a count on the overview and a queue filter or column. It needs a field from the case API (TAX-090), then the screen change.                            | auditor-web + TAX-090; new task             |
| F3  | P2       | **The taxpayer landing page doesn't mention an open evidence request.** "Your submissions" lists submissions only. A first-time taxpayer isn't told that a request needs a response unless they open "Evidence requests".                                                                                                                                                                 | Show a "Response needed" notice on "Your submissions" (count, earliest reply-by date, link to Evidence requests). Use the same allowlisted request statuses (TAX-045).                                      | taxpayer portal; new task                   |
| F4  | P2       | **A specialist has no "referred to me" view.** A specialist sees the same queue as an auditor and has to already know which case was referred.                                                                                                                                                                                                                                            | Add a "Referred to me" filter or overview entry with the specialist-opinion work.                                                                                                                           | TAX-093 (suggest an added criterion)        |
| F5  | P2       | **Long keyboard path on the review page.** From the top of "Review and decide", a keyboard user needs 21 Tab stops to reach the first "Use as a starting point", and 44 Tab stops for a whole evidence request. Same at every width.                                                                                                                                                      | Add in-page skip links or a short contents list ("Suggestions", "Propose a decision", "Request evidence"), or move the suggestions earlier in the reading order. Keep the visible focus that already works. | auditor-web (review); new task              |
| F6  | P3       | **"Component showcase" is in the operational sidebar.** The design page sits under "Design" next to case work. `docs/UI_INTEGRATION_SCOPE.md` keeps it out of normal operational navigation.                                                                                                                                                                                              | Hide the entry outside development builds when the app is hosted for real users.                                                                                                                            | TAX-091                                     |
| F7  | tracked  | **Original documents can't be opened.** The evidence page says to "open the original in the restricted evidence store", but offers no link or button, and says content is never shown in the workspace.                                                                                                                                                                                   | Already planned.                                                                                                                                                                                            | TAX-109                                     |

### F1 details

- **Reproduction.** On `/auditor/cases`:
  1. Type "Border Retail" in Search.
  2. Wait until "Showing 1–1 of 1 cases" appears, then one more second.
  3. Set network latency with Chrome DevTools Protocol `Network.emulateNetworkConditions`.
  4. Click SYN-002.
- **Results.** Run 4 times per latency in Microsoft Edge:

  | Latency        | Case page opened                                  |
  | -------------- | ------------------------------------------------- |
  | 0 ms, 150 ms   | 8/8                                               |
  | 300 ms, 600 ms | 0/8: the queue stays open with the filter applied |

  Without latency, 30/30 clicks opened the case (1440 and 390 px).

- **Cause, from the code:**
  - frappe-ui's `TextInput` emits on both `input` and `change`, debounced
    200 ms (`node_modules/frappe-ui/src/components/TextInput/TextInput.vue`).
  - Clicking the result blurs the search box, so `change` fires. 200 ms later
    `CaseQueueView.update()` calls `router.replace({ query })`.
  - When the case page hasn't finished loading by then, that replacement
    cancels the navigation.
- **Who it affects.** A second click works, so officers on slower links would
  see "the first click does nothing". The temporary repro script wasn't
  committed; the walkthrough clicks again and records the friction when it
  happens.

## Keyboard and accessibility evidence

- **Keyboard walkthroughs.** Both keyboard personas completed every goal by
  Tab, Shift+Tab, Enter, Space and typing alone. The count of each Tab stop is
  in `metrics/*-keyboard-*.json`.
- **File uploads.** The browser can't drive the operating-system file dialog,
  so uploads are counted as one action after the file chooser was reached by
  keyboard.
- **Mobile bottom bar.** At 390 px the taxpayer bottom bar is reached with
  Shift+Tab, because it comes after the page content.
- **Visible focus.** Shown in `auditor-keyboard-confirm-*.png` (the confirm
  button in the dialog) and `taxpayer-keyboard-send-focused-*.png`.
- **axe and reflow.** Scans on the overview, case, evidence, taxpayer profile,
  request form, approval, dashboard, submission status, evidence response,
  invoice schedule, claim sent and account states found no new serious or
  critical violations, and no page-wide sideways scrolling at any width.

## Screenshots (`CAPTURE_SCREENSHOTS=1`, viewport, at 1440, 1024 and 390 px)

| File                                                              | State                                                                  |
| ----------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `taxpayer-1-landing-*`                                            | Entrance: "Your submissions", with no mention of the open request (F3) |
| `taxpayer-2-respond-*`                                            | Evidence response filled in, before sending                            |
| `taxpayer-3-invoices-*`, `taxpayer-4-sent-*`                      | Claim invoice schedule; claim sent                                     |
| `taxpayer-keyboard-send-focused-*`                                | Keyboard focus on "Send response"                                      |
| `auditor-1-overview-*`                                            | Auditor entrance, with "Component showcase" in the sidebar (F6)        |
| `auditor-2-case-*`, `auditor-3-evidence-*`, `auditor-4-request-*` | Case, evidence item (F7), evidence request form                        |
| `auditor-keyboard-confirm-*`                                      | Keyboard focus on "Record evidence request" in the dialog              |
| `supervisor-1-waiting-*`, `supervisor-2-dashboard-*`              | Decision waiting for approval (F2); management dashboard               |
| `specialist-1-review-*`                                           | Specialist sees Approve disabled, with the reason                      |

The taxpayer screenshots show "Live data" in the sidebar because the harness
session mocks answer `mode: live` since TAX-092. The data is still the
synthetic mock.

## Limits

- **Proxies aren't people.** They show that each goal is reachable and how much
  effort it takes, not whether people understand the wording or trust the
  result. Moderated sessions with human participants would need their own team
  decision. No real taxpayer data may be used (`docs/V1_ACCEPTANCE.md`).
- **Fixture mode only.** The auditor app is not yet hosted in Frappe with real
  login (TAX-091), so the first login, password and session-expiry steps are not
  measured. The live re-run of the end-to-end journeys is TAX-103.
- **The portals are not connected.** An evidence request recorded on the
  auditor side doesn't reach the taxpayer mock (see `tests/frontend/README.md`).

## Reproduce

```sh
npm run build:auditor
PLAYWRIGHT_CHANNEL=msedge RECORD_DIR=docs/evidence/TAX-082/metrics CAPTURE_SCREENSHOTS=1 \
  npm run test:frontend:browser -- onboarding
```
