# taxpayer-frontend

## Vati Workspace shell (TAX-146)

The portal runs inside the shared Vati Workspace shell (`apps/shared/components/WorkspaceShell.vue`)
in its simpler variant: the "Vati Taxpayer Portal" header (no app tabs, no search), the
fixture strip when the data is synthetic, and an account menu with Log out. The sidebar
lists Evidence requests, My cases and Account (and the Online Refunds simulator under
"Fixture tools" where enabled). It collapses to an icon rail; below 1024px it opens as a
drawer from the header's Menu button, replacing the former bottom bar.

## Evidence first (TAX-125, ADR-043)

Taxpayers file refund claims in RSL's Online Refunds Application. This portal is where they
answer RSL's evidence requests.

- **Home and first menu item: Evidence requests** (`/taxpayer/` opens `/taxpayer/requests`).
  RSL's screens can link straight to one request, `/taxpayer/requests/:id`. A signed-out
  taxpayer is sent to login and returned to that request.
- **My cases** (`/taxpayer/submissions`) is read-only. A claim read from RSL (TAX-122)
  shows RSL's claim reference and the status RSL reports for it, read from the site's RSL
  source when the page opens. The status is labelled with its source; a recorded fixture
  is never called live. If RSL does not answer, the page says so instead of showing a
  status. A link to RSL's dashboard appears only when the site configures one
  (`vati_rsl_links.online_refunds_dashboard`, https only). There are no risk, case or
  officer details.
- **Online Refunds simulator** (`/taxpayer/submissions/new`): the former claim form
  (TAX-043/044), labelled as a synthetic stand-in for RSL's system. It exists only on
  sites that set `vati_online_refunds_simulator` (development, test and demo sites;
  `scripts/frappe_dev.py init` sets it on the developer site). Elsewhere the route is
  "Page not found", the menu has no link to it, and `submit_refund_claim` and
  `review_refund_claim` answer 403. The browser harness turns it on by default because it
  is fixture tooling; `mockTaxpayerSession(page, persona, { simulatorEnabled: false })`
  checks the production portal.

The sections below describe the screens and the simulator's claim flow as built in
TAX-021 to TAX-092.

## Live submissions (TAX-092)

The session, the submissions list and the submission status are **live**: the
signed-in taxpayer's own Vati Submission and Vati Refund Claim records, with the
taxpayer-facing review status mirrored from Jube by TAX-039 (Under review,
Evidence requested, Completed; Frappe's own Draft, Correction needed and
Withdrawn). A claim sent from the form appears in the list. Another taxpayer's
submission is "not found", and case, risk and officer detail is never sent to
the browser (`vati_tax.portal.submissions` is an allowlist).

Every read says where its data comes from (`mode`). The portal accepts `live`,
or `fixture` only when it is also labelled `synthetic: true`, and refuses
anything else. Since TAX-146 fixture data is labelled by a slim "Synthetic data"
strip in the header; live data has no strip.

## VAT refund claim (TAX-043; the Online Refunds simulator since TAX-125)

`/taxpayer/submissions/new` is the VAT refund claim form (refund claims only;
VAT returns are out of scope). Three steps, then a confirmation:

1. **Claim details**: monthly tax period, amount claimed (more than 0.00),
   reason, explanation (required for "another reason"), optional contact email.
2. **Supporting documents**: at least one. Each file is checked in the browser
   (PDF, PNG, JPEG, CSV or XLSX, up to 25 MB), then uploaded as private
   evidence through `vati_tax.evidence.api.upload_evidence` (TAX-032) with the
   `X-Vati-Tenant-ID` header from the session's taxpayer scope. A failed upload
   can be retried under the same idempotency key, or removed.
3. **Review and send**: the declaration, then
   `vati_tax.portal.api.submit_refund_claim`, which stores the `refund_claim`
   submission and its `Vati Refund Claim` (TAX-031) in one transaction.

One idempotency key covers the whole draft, so sending again after a lost
connection replays the stored claim instead of creating a second one; changed
answers under the same key are refused as a conflict. Server field errors are
placed on the matching field and step. Rules: `src/lib/validation.ts` and
`src/lib/documents.ts` mirror `vati_tax/portal/claims.py` and the evidence
limits; the server re-checks everything.

## Purchase invoices and pre-submission checks (TAX-044)

Claim details also ask for the period's output and input VAT. A **Purchase
invoices** step lists each invoice behind the claim (number, date, supplier,
amounts excluding VAT, VAT and total, optional amount paid and contract), each
linked to an uploaded document; totals are added in whole cents and the server
recomputes them. On **Review and send**, a "Before you send" panel shows the
claimed amount, the VAT on the listed invoices, what the documents support and
the rest, with correction prompts that link back to the right step. The checks
come from `vati_tax.portal.api.review_refund_claim` (the TAX-048 reconciliation);
they are guidance only and never block sending. Rules: `src/lib/schedule.ts`
mirrors `vati_tax/submissions/invoices.py`.

## Correction reminders (TAX-078)

On the first step of a new claim, a "From your earlier claims" panel lists the
taxpayer's own reminders from reviewed omissions in earlier claims
(`vati_tax.portal.api.get_correction_reminders`). It shows a title, the
officer's explanation and when it was noted or last updated, and it is absent
for a first-time claimant. On **Review and send**, a prompt that a reminder
covers is labelled "Also noted on an earlier claim". Reminders are guidance
only: they never block sending, and if they cannot be loaded the claim can
still be filled in and sent. Helpers: `src/lib/reminders.ts`.

## Evidence requests (TAX-045)

`/taxpayer/requests` lists the taxpayer's own evidence requests with
taxpayer-facing statuses only (Response needed, Response sent, No longer
needed). `/taxpayer/requests/:id` shows the officer's message and the requested
items. The taxpayer answers every item with uploaded documents (one upload panel
per item) or a reason it cannot be provided, then sends the response once (one
idempotency key per response; a repeated send is replayed). Answered and
withdrawn requests are read-only. Rules: `src/lib/responses.ts` mirrors
`vati_tax/responses/rules.py`.

## Session boundary

- `backend/vati_tax/vati_tax/www/taxpayer.py` guards every `/taxpayer/...` page
  (route rule in `hooks.py`). Guests are redirected to
  `/login?redirect-to=/taxpayer/...`; the return path is restricted to the
  portal (`vati_tax/portal/access.py`).
- `vati_tax.portal.api.*` methods are not guest-whitelisted, so Frappe answers
  an unauthenticated call with HTTP 403.
- The Vue router re-checks the session on navigation and sends an expired
  session to login. Log out calls `POST /api/method/logout`.
- TAX-029: only users with an enabled taxpayer membership reach the shell
  (others get 403), and the portal API returns only their own taxpayer's
  submissions; another taxpayer's submission is reported as not found. To view
  the fixtures locally, grant your user a taxpayer membership in the fixture
  tenant (see `docs/PERMISSIONS.md`).

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
