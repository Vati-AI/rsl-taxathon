# TAX-077 evidence: policy-gap and integrity management views

Synthetic data only (`integration/api/fixtures/integrity.json`). Screens are served by the fixture API through the same `vati_tax.integrity.aggregate` rules as the Vati service. Captured in Edge with `CAPTURE_SCREENSHOTS=1 npm run test:auditor:browser -- integrity`.

| File | State |
|---|---|
| `integrity-1440.png`, `integrity-1024.png`, `integrity-390.png` | Supervisor view at three widths |
| `denied-auditor-1440.png`, `denied-specialist-1440.png` | The server refuses with 403; no figures loaded; nav entry hidden |
| `insufficient-volume-1440.png` | Too few people covered: no counts at all |
| `unavailable-1440.png` | Sources unavailable: shown as unavailable, never as zero |

Checks (`apps/auditor-web/e2e/integrity.spec.ts`):

- No serious or critical axe violations (WCAG 2 A/AA) and no page-wide horizontal scroll at 1440, 1024 and 390 px.
- Keyboard: Tab reaches the reporting window and both table regions, with a visible focus ring.
- Chart bars are decorative (`aria-hidden`); each table carries the figures and is the accessible fallback. Bars are hidden below 640 px so counts stay visible.
- No login or e-mail-like text and no accusatory term appears on screen. A response that would reveal a login is refused ("cannot be shown safely").

Visual approval belongs to the reviewer; passing tests do not grant it.
