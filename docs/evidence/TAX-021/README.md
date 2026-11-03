# TAX-021 review evidence

Screenshots and results for human visual review of the taxpayer portal shell
(docs/DESIGN_STANDARD.md). These are review evidence, **not approved baselines**:
Jane (TAX-023) captures baselines after the human frontend owner accepts them.

## How it was produced (2026-11-03)

- Live local Frappe developer site from docs/FRAPPE_DEV.md (Frappe v16.54.0,
  `taxathon.localhost`, loopback port 58000) with the built portal
  (`npm run build:taxpayer`, then `bench build --app vati_tax` once to link assets).
- Signed in through Frappe's real `/login` page as a synthetic Website User,
  `taxpayer.a@example.test`, created only in the local site. Its password lives in
  the ignored `.local/` folder and is never written to these files.
- Microsoft Edge 156.0.4314.50 driven by Playwright 1.64.1; accessibility scans
  with axe-core 4.14.0 (WCAG 2 A/AA, 2.1 AA and best-practice rules).
- Script: `capture-portal-evidence.mjs` (usage in its header). Loading, empty,
  error and denied states are produced by intercepting the portal API in the
  browser only; the server data is unchanged.

## Results (`results.json`)

- 80/80 functional checks passed: guest redirects and API rejection, login
  return path, list, status, missing amount, not-found, form validation, review,
  disabled send with reason, account, all states, active navigation, keyboard
  order and focus, 320px reflow, logout and lock-out after logout.
- 0 axe violations across 18 scans (6 screens/states at 3 widths).
- No page-wide horizontal scrolling at 1440, 1024, 390 or 320px.

## Files

`<screen>-<width>.png` at 1440, 1024 and 390px: `list`, `detail`,
`detail-notfound`, `form-empty`, `form-errors`, `form-review`, `account`,
`notfound`, `state-loading`, `state-empty`, `state-error`, `state-denied`.
Also `guest-login-redirect-1440.png`, `keyboard-skip-link-1440.png` and
`keyboard-focus-1440.png`.
