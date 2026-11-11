# auditor-frontend

Owner: Leseli. See `tasks/README.md` for work and `docs/ARCHITECTURE.md` for boundaries.

Officer-facing Vue 3 + TypeScript + Frappe UI app (ADR-004). It reads the Vati
`/api/v1` contract (`listCases`, `getCase`, `getTaxpayer`; ADR-005) through
`src/api/vati.ts`, `listEvidence` and `listEntities` (TAX-041) through
`src/api/caseRecords.ts`, the officer review operations (`getCaseReview`,
`listFindings`, `proposeDecision`, `reviewDecision`, `requestEvidence`; ADR-018)
through `src/api/review.ts`, and the supervisor-only `getManagementSummary`
(ADR-020) through `src/api/management.ts`, all served from synthetic fixtures by
`scripts/dev.py serve`. Live services arrive with later tasks.

Screens: overview (TAX-147: action items for Overdue, Awaiting approval,
Awaiting evidence and Processing failed, each with its count, first cases and a
link to the queue view, then cases by status), my case queue (TAX-147: saved
views in the sidebar with counts from the same case list, one toolbar row with
search, status and refund-lane filters, display options kept in the browser,
CSV export of the visible columns and "Open first case", a usage line and a
dense sortable table with column help, muted empty values and an "Open" row
action; view, search, filters, sort and page live in the URL), the case workspace (TAX-148: one header with
the status chip and key facts, and routed tabs, each its own URL: Summary
`/auditor/cases/{id}` with selection reasons, taxpayer and per-service
processing; Evidence `/auditor/cases/{id}/evidence` with the documents beside a
preview pane, `/evidence/{evidenceId}` opening one: SHA-256, version history,
extraction state, the cited page or record and the findings citing it;
Relationships `/relationships`; Findings `/findings`; Review `/review`: the
decision state, evidence requests, agent suggestions and the review trail, with
the propose and request-evidence forms in right-side drawers and every action
confirmed in a dialog first; links from before TAX-148 with `?tab=` redirect),
taxpayer profile (key facts and an open-cases table), management dashboard (`/auditor/management`, supervisors only:
workload, elapsed time, unresolved evidence, closures with no further action,
legal-route coverage and audit ledger assurance), integrity review
(`/auditor/management/integrity`, supervisors only: policy exposure by policy
area, overrides by control and review signals by kind, aggregates only with
small groups combined; ADR-036), component showcase
(`/auditor/showcase`). Shared tokens live in `apps/shared/design`.

The request tenant comes from `src/session.ts` (fixture tenant unless
`VITE_VATI_TENANT_ID` is set); the server, not this header, decides access. In
fixture mode the banner's "Acting as" switch picks the synthetic officer
(auditor, specialist or supervisor) through the fixture-only
`X-Vati-Fixture-Role` header; review actions change fixture state until the
server restarts. Choose "supervisor" to see the management dashboard.

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
`docs/evidence/TAX-041`, `-- review-screenshots` writes the review page states
to `docs/evidence/TAX-042`, and `-- management` writes the management dashboard
screens to `docs/evidence/TAX-060`; `-- integrity` writes the integrity review
screens to `docs/evidence/TAX-077`. Review actions run in their own Playwright
project against a second fixture server (port 58095, `AUDITOR_REVIEW_E2E_PORT`),
so they never change the data the other specs check. The management dashboard
only reads, so its spec uses the main fixture server.
