# ADR-044: One Vati Workspace identity and design system v2

Status: proposed
Owner and task: Keletso; implemented by TAX-144 to TAX-152
Date: 2026-11-08
Revises: [DESIGN_STANDARD.md](../DESIGN_STANDARD.md) (2026-11-03 "polished Frappe application"),
the naming in [UI_INTEGRATION_SCOPE.md](../UI_INTEGRATION_SCOPE.md) and the "one product name"
part of TAX-137. ADR-001, ADR-004 (Vue + frappe-ui) and ADR-034's no-fork rule are unchanged.

Context and smallest prototype need:
- The user reviewed both frontends on 2026-11-08. The direction is right, but the screens "still
  look like AI/vibe code, not a professional system". They asked for Cloudflare's dashboard as the
  reference for look and feel and Zoho Workplace for how the workspace fits together, and
  supplied screenshots of both.
- Measured on the fixture build at 1440×900 (2026-11-08):

  | Screen | Height | Problem |
  |---|---|---|
  | Auditor sidebar | 3 links | no workspace header, search or account menu |
  | Review and decide | 1.3 screens | decision, evidence-request form, suggestions and trail stacked |
  | Management dashboard (supervisor) | 1.9 screens | one long column |
  | Component showcase | 2.9 screens | reachable from operational navigation |

  Also:
  - fixture mode is a full-width banner;
  - the taxpayer starter page uses a hero title, a card grid and developer copy ("Starter
    workspace for Kananelo");
  - the taxpayer app does not run without Frappe, so it could not be measured.
- 28 user-visible strings in the two apps name "Jube", 27 "Frappe" (for example "Signal from Case
  management (Jube)" and "Taxpayer portal (Frappe)"). Jube's and OpenAleph's own screens keep
  their own names and looks. To a judge or an RSL officer it reads as several tools stitched
  together rather than one product that RSL could adopt.

Decision:
1. **One product: Vati Workspace.** Users see Vati Workspace and its apps:
   - Cases (auditor app);
   - Investigation (Jube screens);
   - Explore (OpenAleph);
   - Admin (Frappe Desk);
   - the Vati Taxpayer Portal.

   Docling, yente and the model stack appear as capabilities ("Document reading", "Name
   screening", "Vati assistant"). The register in DESIGN_STANDARD.md is the only mapping.
   - Component names remain in code, API fields, the audit ledger, logs, Admin diagnostics and an
     **About · Open-source notices** page.
   - RSL systems keep RSL's names (TAX-138).
2. **Design system v2.** Adopt the layout and interaction patterns in DESIGN_STANDARD.md: app
   shell, page header, notices, table toolbar, routed case tabs, drawers, page-length budget and
   command palette. We take patterns, not brand assets: no Cloudflare or Zoho logos, colours,
   illustrations or copy, and no move to React or Kumo.
3. **Seamless native tools by configuration.**
   - Jube, OpenAleph and Frappe Desk/login get the Vati name, logo, favicon and colour tokens
     through their supported settings, an injected stylesheet or reverse-proxy headers and
     footers, with no source fork.
   - Licence notices and attribution are preserved and linked from the About page.
   - Whatever a pinned version cannot rename or theme is recorded as a limitation (TAX-151).
4. **Measured, not asserted.** A page-length and naming check runs in the browser tests (TAX-144,
   TAX-150), so "professional" has testable parts: no screen over budget, no banned component name
   in user-visible text, axe clean, screenshots at three widths.

Alternatives and tradeoff:
- **Polish screen by screen under the old standard.** Cheaper, but it does not fix the missing
  shell, navigation and naming that make it look assembled.
- **Fork Jube/OpenAleph to rebrand them deeply.** Seamless, but it breaks ADR-034 and the pinned
  upstreams, and adds licence obligations.
- **Embed native tools in iframes inside one shell.** Looks unified, but ADR-034 rejected it for
  this iteration (session, CSP and permission complexity). Revisit only through a new ADR.
- **Adopt Kumo.** It is React, and our apps are Vue + frappe-ui; switching would rewrite both
  apps.

Official sources and version/date checked (2026-11-08):
- User-supplied screenshots of the Cloudflare dashboard (DNS records and sign-in) and of Zoho
  Workplace (Mail and WorkDrive).
- Cloudflare: [Radar redesign blog](https://blog.cloudflare.com/radar-redesign/), which notes the
  move to Kumo components, and [Kumo](https://kumo-ui.com/) / `@cloudflare/kumo` (pattern
  reference only, not adopted).
- Zoho:
  - [Interface design in collaboration tools](https://www.zoho.com/workplace/articles/interface-design-in-collaboration-tools.html);
  - [Unified settings for Workplace apps](https://www.zoho.com/workplace/help/settings.html);
  - [Unified digital workspace](https://blog.zoho.com/workplace/unified-digital-workspace.html).
- General guidance (secondary, vendor blogs):
  - progressive disclosure and pagination: [AdminLTE dashboard design](https://adminlte.io/blog/admin-dashboard-design/);
  - list-detail: [SAP master-detail usage](https://www.sap.com/design-system/hcm/patterns/structure-and-layout/master-detail/usage)
    and [Microsoft list-detail](https://learn.microsoft.com/dual-screen/design/list-detail).
- **Not yet verified:**
  - Jube's and OpenAleph's licence terms and branding options for the pinned commits (TAX-151
    records them);
  - Frappe `app_logo_url` / `brand_html` / website settings for the pinned Frappe version
    (TAX-151).

Verification performed (or named implementation task):
The screen measurements above came from a Playwright run against the fixture build on 2026-11-08
([docs/evidence/ADR-044](../evidence/ADR-044/README.md)).
No redesign is implemented in this decision. TAX-144 to TAX-152 implement and verify it.

Consequences and revisit trigger:
- Every UI task follows DESIGN_STANDARD.md v2.
- TAX-137 keeps the return links and the status vocabulary; the product name and header move to
  TAX-146, TAX-150 and TAX-151.
- Revisit if:
  - RSL asks for RSL branding;
  - a pinned native tool cannot be themed without a fork;
  - shared sign-in (TAX-136) makes an embedded shell feasible.
