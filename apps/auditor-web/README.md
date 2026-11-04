# auditor-frontend

Owner: Leseli. See `tasks/README.md` for work and `docs/ARCHITECTURE.md` for boundaries.

Officer-facing Vue 3 + TypeScript + Frappe UI app (ADR-004). It reads the Vati
`/api/v1` contract (`listCases`, `getCase`, `getTaxpayer`; ADR-005, and `listEvidence`,
`listEntities`, `listFindings` for TAX-041) through `src/api/vati.ts` and
`src/api/caseRecords.ts`, served from synthetic fixtures by `scripts/dev.py serve`.
Live services and investigation actions arrive with later tasks.

Screens: overview (assigned cases by status, failed-processing alert), my case
queue (search, status and refund-lane filters, sorting, pagination, selection
reasons and processing state), case detail (selection reasons, taxpayer
summary, per-service processing; evidence, relationships and findings tabs),
evidence record (`/auditor/cases/:caseId/evidence/:evidenceId`: SHA-256, version
history, extraction state, cited page/record and the findings citing it),
taxpayer profile, component showcase
(`/auditor/showcase`). Shared tokens live in `apps/shared/design`.

The request tenant comes from `src/session.ts` (fixture tenant unless
`VITE_VATI_TENANT_ID` is set); the server, not this header, decides access.

```sh
npm ci
python3 scripts/dev.py serve            # fixture API on 127.0.0.1:58080
npm run dev:auditor                     # hot reload on http://127.0.0.1:58081/auditor/
npm run build:auditor                   # then dev.py serve shows it at :58080/auditor/
npm run test:auditor                    # unit + component tests
npm run test:auditor:browser            # Playwright smoke + axe (needs a build)
```

Browser tests: install a browser once with `npx playwright install chromium`,
or on Windows use the installed Edge with `PLAYWRIGHT_CHANNEL=msedge`. Set
`PYTHON` if `python3` is not on PATH. Review screenshots:
`CAPTURE_SCREENSHOTS=1 npm run test:auditor:browser -- screenshots` writes to
`docs/evidence/TAX-040` (TAX-020's first baseline stays in `docs/evidence/TAX-020`);
`-- screenshots-evidence` writes the TAX-041 screens to `docs/evidence/TAX-041`.
