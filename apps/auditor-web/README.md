# auditor-frontend

Owner: Leseli. See `tasks/README.md` for work and `docs/ARCHITECTURE.md` for boundaries.

Officer-facing Vue 3 + TypeScript + Frappe UI app (ADR-004). It reads the Vati
`/api/v1` contract (`listCases`, `getCase`, `getTaxpayer`; ADR-005) through
`src/api/vati.ts`, `listEvidence` and `listEntities` (TAX-041) through
`src/api/caseRecords.ts`, and the officer review operations (`getCaseReview`,
`listFindings`, `proposeDecision`, `reviewDecision`, `requestEvidence`; ADR-018)
through `src/api/review.ts`, all served from synthetic fixtures by
`scripts/dev.py serve`. Live services arrive with later tasks.

Screens: overview (assigned cases by status, failed-processing alert), my case
queue (search, status and refund-lane filters, sorting, pagination, selection
reasons and processing state), case detail (selection reasons, taxpayer
summary, per-service processing; evidence, relationships and findings tabs),
evidence record (`/auditor/cases/{id}/evidence/{evidenceId}`: SHA-256, version
history, extraction state, the cited page or record and the findings citing it),
review and decide (`/auditor/cases/{id}/review`: propose a decision, approve,
reject or withdraw, refer to a specialist, request evidence, agent suggestions
and the review trail; every action is confirmed in a dialog first), taxpayer
profile, component showcase (`/auditor/showcase`). Shared tokens live in
`apps/shared/design`.

The request tenant comes from `src/session.ts` (fixture tenant unless
`VITE_VATI_TENANT_ID` is set); the server, not this header, decides access. In
fixture mode the banner's "Acting as" switch picks the synthetic officer
(auditor, specialist or supervisor) through the fixture-only
`X-Vati-Fixture-Role` header; review actions change fixture state until the
server restarts.

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
`-- screenshots-evidence` writes the TAX-041 evidence and relationship screens to
`docs/evidence/TAX-041`, and `-- review-screenshots` writes the review page states
to `docs/evidence/TAX-042`. Review actions run in their own Playwright project
against a second fixture server (port 58095, `AUDITOR_REVIEW_E2E_PORT`), so they
never change the data the other specs check.
