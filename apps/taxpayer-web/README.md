# taxpayer-frontend

Owner: Kananelo. See `tasks/README.md` for work and `docs/ARCHITECTURE.md` for boundaries.

Vue 3 + TypeScript + Vite + Frappe UI taxpayer portal (ADR-002), served by the
`vati_tax` Frappe app at `/taxpayer`. TAX-021 delivers the authenticated shell:
routes, login/logout, submissions list, submission status and a two-step
submission form. Data is **explicitly synthetic fixture data** from
`vati_tax.portal.api` until TAX-031 adds Submission DocTypes, so sending a
submission is disabled with a visible reason.

## Session boundary

- `backend/vati_tax/vati_tax/www/taxpayer.py` guards every `/taxpayer/...` page
  (route rule in `hooks.py`). Guests are redirected to
  `/login?redirect-to=/taxpayer/...`; the return path is restricted to the
  portal (`vati_tax/portal/access.py`).
- `vati_tax.portal.api.*` methods are not guest-whitelisted, so Frappe answers
  an unauthenticated call with HTTP 403.
- The Vue router re-checks the session on navigation and sends an expired
  session to login. Log out calls `POST /api/method/logout`.
- Role and tenant restrictions are TAX-029; they extend `portal_access`.

## Commands (repository root)

```sh
npm ci
npm run build:taxpayer     # writes vati_tax/public/taxpayer and www/taxpayer.html (ignored)
npm run test:taxpayer      # vitest: routing/session/validation/format
npm run typecheck:taxpayer # vue-tsc
npm run dev:taxpayer       # http://127.0.0.1:58082/taxpayer/ proxying the Frappe site on :58000
```

The app follows the auditor app's stack and shared visual contract (ADR-004):
frappe-ui 1.1.0, `apps/shared/design/tokens.css` and the shared Tailwind preset,
system font. `npm run check` covers both apps.

With the Frappe developer site running (docs/FRAPPE_DEV.md), build once and open
http://127.0.0.1:58000/taxpayer. If `/assets/vati_tax/...` returns 404, run
`bench build --app vati_tax` in the container once to link the app's public
folder.

`starter.html` is the dependency-free page that `scripts/dev.py serve` still
shows at http://127.0.0.1:58080/taxpayer/.
