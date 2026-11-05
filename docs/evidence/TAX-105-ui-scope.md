# TAX-105 verification — 2026-11-05

Inspected GitHub main c1ec368579d08b48b184d31c3d272b6aa2adfef8 in an independent
checkout. This is source/configuration verification, not live acceptance.

| Finding | Evidence | Conclusion |
|---|---|---|
| Native tools were part of the original plan | docs/reference/TECHNICAL_SUMMARY.md sections 6–8; docs/ARCHITECTURE.md | Full officer/taxpayer screens and access to Jube/OpenAleph were intended |
| Auditor still uses fixtures | apps/auditor-web/vite.config.mts disables Frappe proxy/boot data and proxies to fixture server; App.vue always includes FixtureBanner; session.ts stores fixture role | Real login/live hosting remain TAX-090/091, not completed by a role switcher |
| Missing native links | router.ts has only local routes; CaseDetailView.vue prints service_links.external_id as text | No case-specific Jube/OpenAleph navigation was found in auditor source |
| Relationship view is not an interactive graph | components/CaseRelationshipsTab.vue renders party cards, ownership/payment tables | Reuse OpenAleph graph exploration and retain accessible Vati summaries |
| Evidence view does not open originals | views/EvidenceView.vue explicitly says content is never shown and prints restricted file reference | Protected document access is a separate missing delivery requirement |
| Taxpayer flow is further along | apps/taxpayer-web/README.md, portal API and www/taxpayer.py; TAX-092 | Frappe session and live submission reads implemented; not proof of all-service connectivity |
| Jube offers configurable native surfaces | Local external/jube HEAD equals lock 01c4afdbcdb186cc36b128ed79283d544cfb9ec9; Case.cshtml, Case.cshtml.cs and CaseWorkflowForm/Display/Status pages | Configuration surface exists; tax-specific usability and permission enforcement need authenticated tests |
| OpenAleph has collection grants | Local external/openaleph HEAD equals lock f89ff18d83fcfebdadc6b2db83fb9e2fbb5397c2; aleph/views/permissions_api.py | Must map case authorization to collections, not rely only on Vati link visibility |
| Prior smoke tests are historical | integration/jube/README.md; integration/openaleph/README.md | Authentication/ingest smoke evidence exists; it is not a native-screen usability review |
| Current local reachability is inconclusive/unavailable | Unauthenticated GET / with 3-second timeout: Frappe :58000 TimeoutError; Jube :55501 URLError; OpenAleph :55580 URLError | No current authenticated UI readiness established. This machine is not the Windows screenshot machine |
| Demo profile has a capability mismatch | TAX-095 allows recorded OpenAleph outputs | Recorded data does not supply live native search/graph exploration; full and reduced acceptance must differ |
| Some readiness summaries are stale | services.json still calls OpenAleph adapter not_implemented despite integration/openaleph implementation and README | Do not treat service registry or DONE counts alone as runtime proof; TAX-095 retains status-refresh responsibility |

No credentials, taxpayer documents or environment files were read. No services were
started, reconfigured or stopped. No payment, SSO, role or permission changes were made.
Public Jube/OpenAleph docs were consulted for capability categories; actual routes and
permission enforcement must be verified against deployed versions in TAX-106–109.

Scope-change validation: `python3 scripts/validate.py`, `python3 scripts/dev.py check`
and `python3 scripts/quality.py` passed in the isolated checkout. Existing
Frappe/dependency-gated skips remain; no authenticated native UI or new product behavior
was tested. Quality used the unchanged locked dev dependencies and Node 24.20.0.
No documentation-wording tests were added.
