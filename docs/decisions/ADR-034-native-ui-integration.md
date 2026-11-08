# ADR-034: Reuse native investigation screens with case-aware navigation

Status: proposed for human review; records the user's requested scope clarification.
Owner and task: Keletso, TAX-105
Date: 2026-11-05
Revisited by: [ADR-043](ADR-043-vati-downstream-of-rsl-selection.md) (2026-11-07). RSL already runs WSO2 Identity Server, so TAX-136 tests one sign-in for Jube and OpenAleph; the no-fork rule stands.

Context and smallest prototype need: The original technical summary section 7 calls
for full officer/taxpayer experiences and advanced access to Jube/OpenAleph. The auditor
app currently has fixture navigation and text external IDs, not native-screen links.
The user explicitly wants the premade interfaces adapted to the tax project.

Decision: Apply docs/UI_INTEGRATION_SCOPE.md. Frappe hosts Vati login and custom portals;
Frappe Desk supplies permitted administration. Reuse configured Jube and OpenAleph screens
via authorized record-specific links in new tabs. Retain separate native sessions for
this iteration. FollowTheMoney supplies schemas; do not invent an FtM standalone UI.
Native mutation permissions must preserve ADR-001's service/approval authorities.

Alternatives and tradeoff: Rebuilding every upstream screen duplicates mature features.
Embedding via iframes or requiring shared SSO increases integration work and was not
part of the agreed initial navigation. Separate tabs and sessions are less seamless but
can deliver the complete functional workflow sooner. Configuration-first adaptation
avoids a fork unless verified requirements demand one.

Official sources and version/date checked:
- Original product reference: docs/reference/TECHNICAL_SUMMARY.md, sections 6–8.
- [Jube docs](https://www.jube.io/docs/): workflow forms, display, status, security,
  navigation and user/role configuration; checked 2026-11-05. Implementation authority
  remains locked source 01c4afdbcdb186cc36b128ed79283d544cfb9ec9, especially
  Jube.App/Pages/Case/Case.cshtml and model CaseWorkflow pages.
- [OpenAleph docs](https://openaleph.org/docs/): search, datasets, network diagrams,
  permissions and access; checked 2026-11-05. Locked API source
  f89ff18d83fcfebdadc6b2db83fb9e2fbb5397c2, aleph/views/permissions_api.py; separate
  deployed UI URL patterns still require validation (no invented routes in this ADR).
- [FollowTheMoney source](https://github.com/opensanctions/followthemoney/tree/c8c457a5c80fcf3ebe134f49b8dc60b417f0b860),
  the library lock and the repository's existing FtM mappings.

Verification performed: docs/evidence/TAX-105-ui-scope.md records static inspection,
matched upstream commit IDs, historical smoke evidence and limited local reachability.
No authenticated native-screen acceptance was performed in this task.

Consequences and revisit trigger: TAX-106–109 own missing navigation, configuration and
document-access acceptance. TAX-103/058 include connected native-screen checks. A reduced
recorded-data laptop demonstration cannot satisfy full native-UI acceptance. Revisit
if the pinned versions cannot enforce case permissions/approval controls, browser links
change, shared login becomes required or a source fork is necessary.
