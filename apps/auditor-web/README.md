# auditor-frontend

Owner: Leseli. See `tasks/README.md` for work and `docs/ARCHITECTURE.md` for boundaries.

Officer-facing Vue 3 + TypeScript + Frappe UI app (ADR-003). It reads the
synthetic fixture contract only (docs/API_CONTRACTS.md); live services and
investigation actions arrive with later tasks.

Screens: overview (review lanes), case queue (search/filter/sort/pagination),
case detail skeleton (evidence, entities and findings shown as not available in
fixture mode), component showcase (`/auditor/showcase`). Shared tokens live in
`apps/shared/design`.

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
`docs/evidence/TAX-020`.
