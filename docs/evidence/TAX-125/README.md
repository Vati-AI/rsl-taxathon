# TAX-125 evidence: evidence-first taxpayer portal

Screenshots from `CAPTURE_SCREENSHOTS=1 npx playwright test -c tests/frontend/playwright.config.ts taxpayer.spec.ts -g "TAX-125"`
at the three design-standard widths (1440, 1024, 390). The data is synthetic: the browser
harness answers the portal API with session mocks (`tests/frontend/support/sessions.ts`), and
the RSL claim comes from `backend/vati_tax/vati_tax/portal/fixtures.py`. The RSL dashboard
link points to an `example.test` address.

| File | Shows |
|---|---|
| `home-requests-*.png` | The portal opens on Evidence requests, the first menu item |
| `my-cases-*.png` | Read-only My cases: RSL claim reference, RSL status and a link to RSL's Online Refunds Application (configured link) |
| `rsl-case-*.png` | One case filed with RSL: RSL's reference and status labelled "recorded synthetic data, not a live RSL connection", and no RSL link because none is configured for this view |
| `simulator-*.png` | The Online Refunds simulator (former claim form), labelled and under "Fixture tools", on a site that enables it |
| `simulator-absent-*.png` | The same route on a site without the simulator: "Page not found", with no menu link |

The automated checks run axe (WCAG 2.1 A/AA, serious and critical) and a reflow check on the
home, My cases and case pages in the same test. Keyboard behaviour (skip link, navigation by
Enter) is covered by the existing taxpayer keyboard tests, which still pass. A second person
has not reviewed these screens visually (optional under ADR-038).
