# TAX-092 review evidence

The taxpayer portal's session, submissions list and submission status are now
live. Synthetic data only. Screenshots are review evidence, not approved
baselines.

## What changed

- `vati_tax.portal.api.get_session`, `get_submissions` and `get_submission`
  answer `mode: live`. They read the caller's own Vati Submission records,
  filtered by `tenant_id` and `taxpayer_id` in the query, with the claimed
  amount from Vati Refund Claim and the taxpayer's display name.
- **Status:** each submission's status is the TAX-039 taxpayer-facing word
  (`bridge.rules.taxpayer_status`): Under review, Evidence requested or
  Completed once sent, otherwise Frappe's own Draft, Correction needed or
  Withdrawn.
- **Allowlist:** `vati_tax.portal.submissions` (new, Frappe-free) builds the
  view. Case IDs, tenant IDs, Jube state, risk, lanes and findings are never
  read, so they cannot be returned.
- **Not found:** another taxpayer's submission is "not found". Fixture records
  (`SUB-SYN-*`) are no longer served.
- **Portal labels:** the portal accepts `live`, or `fixture` only when it is
  also labelled `synthetic: true`. The "Synthetic fixture data" notice and the
  "Synthetic fixture" sidebar label appear only in fixture mode; live data is
  labelled "Live data". Unsent drafts show "Not sent yet", and Withdrawn has a
  label.

## Results (2026-11-05, Windows 11, branch `task/TAX-092`)

| Check                                                                        | Result                                                                              |
| ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `portal/test_submissions.py` (new, Frappe-free)                              | 6/6                                                                                 |
| Backend Frappe-free suite (`unittest discover -s backend/vati_tax`)          | 395 OK (42 skipped)                                                                 |
| `scripts/frappe_dev.py verify`, fresh disposable site                        | install OK, migrate ×2 OK, **167 bench tests OK** (6 new for TAX-092), site dropped |
| Live HTTP on the isolated stack (real login, real stored claims)             | **16/16** (below)                                                                   |
| `npm run test:taxpayer` (vitest)                                             | 67/67                                                                               |
| Browser: taxpayer, claim, scenarios, responses × 3 widths                    | 126 passed, 0 failed                                                                |
| Full browser harness (`npm run test:frontend:browser`), all specs × 3 widths | **159 passed, 0 failed**, 9 skipped (opt-in visual)                                 |
| Ruff, Ruff format, mypy (203 files), ESLint, both typechecks                 | pass                                                                                |
| `python3 scripts/validate.py`                                                | pass                                                                                |

**New bench tests** in `portal/test_refund_claim_bench.py`:

- the session and lists are live, and fixture IDs are not found
- a sent claim appears with its reference, type, period, amount, status
  "under_review" and taxpayer name, and its detail matches the list item
- a mirrored `evidence_requested` and then `completed` is what the taxpayer
  sees
- taxpayer B (same tenant) and taxpayer C (another tenant) never see it in
  their list and get "not found"; B sees only B's own claim
- an auditor cannot use the taxpayer list
- no case, risk, Jube, finding or tenant detail appears, and the detail keys
  are exactly the allowlist

**Live HTTP** (`taxathon.localhost` on the isolated stack, migrated to this
branch; synthetic users A, B and C):

- the session is labelled live
- A sends a claim (`REF-2026-000017`), which is first in A's live list as
  "under_review" with LSL 15,000.00
- A's list has no internal detail and only A's own taxpayer
- A opens the detail
- B and C don't see it and get HTTP 404; a guest gets 403
- after a direct database write of `evidence_requested` and then `completed`,
  the same way the TAX-039 bridge mirrors Jube, A sees each status in the
  detail and the list

**Browser:** two new tests in `taxpayer.spec.ts`, at every width:

- fixture notice only in fixture mode
- a claim sent in the portal appears first in the list as "Under review" and
  opens its status page, with axe and reflow checks

The harness mocks now answer `mode: live`. In the mock, the list also contains
the synthetic `portal/fixtures.py` record; the live server does not serve it.

**Not run / notes:**

- `dev.py check` stops on the two existing Windows-only tooling tests
  (`test_jube_dev`, `test_openaleph_dev`); the remaining checks were run by
  hand.
- Two screenshot runs failed in the harness warm-up, before any test ran
  (`page.goto` timeout on the Vite dev server's first start); the third run
  passed 6/6.
- GitHub Actions is blocked by the organisation's billing limit, so CI has not
  confirmed these results.

## Screenshots (`CAPTURE_SCREENSHOTS=1 … taxpayer -g TAX-092`)

At 1440, 1024 and 390 px:

- `list-live-*`: no fixture notice, "Live data" in the sidebar
- `list-fixture-mode-*`: the notice and "Synthetic fixture", shown only when
  the server labels its data as fixture
- `list-with-sent-claim-*`: the claim just sent, first, "Under review"
- `sent-claim-status-*`: its status page and progress
