# TAX-144: UI audit baseline, page-length and naming checks

Recorded 2026-11-08 on the fixture build at 1440×900. The browser was Chromium 1243, via
`PLAYWRIGHT_EXECUTABLE_PATH`. The rules come from DESIGN_STANDARD.md v2 and ADR-044.

## How to reproduce

```sh
npm run build:auditor
LAYOUT_REPORT=1 CAPTURE_SCREENSHOTS=1 npm run test:frontend:browser -- --project=desktop-1440 layout
```

## What is here

| Files | What they contain |
|---|---|
| `inventory-auditor.md`, `inventory-auditor.json` | Every auditor route at 1440×900, as auditor and as supervisor. Includes the three case tabs and one evidence record. Shows measured height, viewports, component names and the check's verdict. |
| `inventory-taxpayer.md`, `inventory-taxpayer.json` | Every taxpayer route |
| `before/` | Full-length "before" screenshots of all 23 screens, as the starting point for TAX-145 to TAX-152 |

**Taxpayer portal.** The routes were measured in the TAX-023 harness. That harness answers
`vati_tax.portal.api.*` and `/login` in the browser with the Frappe v16 response shapes and
TAX-029 ownership rules (`support/sessions.ts`). No Frappe site was running in this
environment, so the live-site measurement is left to TAX-152, which runs against the Frappe dev
site.

## Findings

**Page length.** The budget is 1.5 viewports, which is 1350px.

| Screen | Height | Viewports | Fixed by |
|---|---|---|---|
| Review and decide (auditor and supervisor) | 2031px | 2.26 | TAX-148 |
| Case › Findings tab | 1751px | 1.95 | TAX-148 |
| Management dashboard (supervisor) | 1743px | 1.94 | TAX-149 |
| Integrity review (supervisor) | 1479px | 1.64 | TAX-149 |
| Component showcase | 2654px | 2.95 | Exempt (development only); leaves the menu in TAX-146 |

Every taxpayer route is within budget. The longest are the evidence request at 1.2 and the
claim form at 1.14.

**Component names.** There are 19 visible occurrences on 9 auditor screens; no taxpayer screen
shows one. Examples:
- "Signal from Case management (Jube)"
- "Taxpayer portal (Frappe)"
- "Investigation workflow (LangGraph)"
- "Document extraction (Docling) timed out"

External record IDs also expose component names (`syn-jube-3002`, `syn-yente-0042`,
`docling-serve@locked`). Under the register these move to technical details. All of them are
listed in the inventory and assigned to TAX-150.

## How the check behaves

`tests/frontend/layout.spec.ts` runs with the rest of the harness at the desktop-1440 width. It
is a ratchet against `tests/frontend/support/layout-baseline.json`.

- **Fails** in three cases:
  - an unlisted screen goes over budget;
  - a listed screen grows by more than 48px;
  - a screen shows more component names than its recorded count.
- **Notes**, without failing, when a listed screen improves. The fixing task then removes or
  lowers its entry.

TAX-150 empties the naming list, so from then on any component name fails.

**Negative test** (2026-11-08). I temporarily edited the baseline in three ways:
- recorded case-review at 1500px;
- removed the integrity entry;
- lowered the queue's name count to 1.

The check then failed with exactly three messages:
- "auditor/queue: FAIL 2 component names shown (baseline 1): Docling, Jube"
- "auditor/case-review: FAIL grew from 1500px to 2031px"
- "auditor/integrity-supervisor: FAIL 1.64 viewports exceeds the 1.5 budget"

The baseline was then restored.
