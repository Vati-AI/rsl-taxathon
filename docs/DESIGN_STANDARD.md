# Frontend design standard (v2: Vati Workspace)

Updated 2026-11-08 by [ADR-044](decisions/ADR-044-vati-workspace-identity-and-design-v2.md),
which replaces the 2026-11-03 "polished Frappe application" direction. The user's review was that
both apps head in the right direction but still read as generated prototypes. This standard sets
the target, and TAX-144 to TAX-152 deliver it. The current screens do not meet it yet.

## What we take from the references, and what we do not

We take interaction and layout **patterns**, not brand assets. We do not copy logos, colours,
illustrations, icon artwork or wording from either product. We do not switch frameworks:
the stack stays Vue 3 + TypeScript + frappe-ui (ADR-004). Cloudflare's own React library
(Kumo) is a reference for component behaviour only.

**Cloudflare dashboard** (screenshots supplied by the user, 2026-11-08), used for the officer
and admin screens:
- A quiet, neutral **app shell**:
  - a top bar with the account/scope switcher, the current object (domain → **case**), "Ask AI"
    → **Ask Vati**, Support and the user menu;
  - a left sidebar of named sections that expand in place;
  - in object context, a **"Back to …"** item at the top of the sidebar (Back to Domains →
    **Back to cases**).
- A **page header** with a large title, one plain sentence saying what the page is for, a status
  chip on the right ("DNS Setup: Full" → case status) and a documentation link.
- **Recommendations / notices** as one dismissable amber panel with a short bold headline and one
  line of guidance, plus a numbered "Recommendations (2)" group that collapses.
- **Table toolbar** in one row: search, Filters, Display options, Import, Export and **one** blue
  primary action on the right.
- A **usage line** above the table ("You have used 8 of 200 …" → "Showing 25 of 143 cases ·
  3 overdue").
- Dense tables with sortable headers, info icons that explain a column, a muted dash for an empty
  value, a row-level text action ("Edit" → "Open") and a checkbox column only where bulk actions
  exist.
- Very few colours: grey surfaces, one blue accent, amber for warnings, red only for errors.

**Zoho Workplace** (screenshots supplied by the user, 2026-11-08), used for how the workspace
fits together:
- One **workspace header** across every app: product mark and name, the app tabs (Mail, Cliq,
  WorkDrive … → **Cases, Explore, Reports, Admin**), global search, notifications, settings and
  the account avatar. Moving between apps keeps the same chrome, so the native tools feel like one
  product.
- A **left app rail** (Mail, Calendar, ToDo … → Cases, Requests, Tasks) beside an
  **in-app panel** of folders and views with unread counts (Inbox 41 → **My cases 12, Awaiting
  evidence 4, Overdue 2**). The panel can be dark while the content stays light.
- A **list-first workspace**: rows grouped by time ("Last 7 days"), sender plus subject on two
  lines, size and date right-aligned, and row actions shown on hover. The evidence-request inbox
  and the case queue follow this.
- Helpful **empty states**: one illustration slot, one sentence and the two or three actions that
  make sense ("Create · Upload · Record").
- One **unified settings** surface for every app.

General guidance (secondary sources, see ADR-044):
- Summary first, detail on demand (progressive disclosure).
- List-detail for queues where officers work one item at a time.
- Advanced options behind a disclosure or drawer.
- Pagination instead of endless scrolling.

## Product identity: one name, no tool names

Everything the user sees is **Vati Workspace**. Open-source components are never named in the
interface. The register below is the only allowed mapping. TAX-150 enforces it with a test.

| Component (code, docs and logs only) | Name shown to users |
|---|---|
| Auditor app (Vue) | **Vati Workspace · Cases** |
| Jube native case screens | **Vati Workspace · Investigation** |
| OpenAleph search and network diagrams | **Vati Workspace · Explore** |
| Frappe Desk administration | **Vati Workspace · Admin** |
| Frappe taxpayer portal | **Vati Taxpayer Portal** |
| Docling | Document reading |
| yente | Name screening |
| LangGraph / model router / Groq | Vati assistant |
| Frappe records (submissions, decisions) | Vati records |

- RSL systems keep RSL's own names (e-Tax, Lekuka, ASYCUDA World, Online Refunds Application,
  RSL fraud model) because they are RSL's (TAX-138). RSL branding only with RSL's approval.
- Provenance is still recorded. Component names stay in API fields, the audit ledger, logs,
  Admin diagnostics and the **About · Open-source notices** page. Licences require that page
  (TAX-150, TAX-151).
- Native tools are themed to the same tokens, logo and product name only through their supported
  configuration, a stylesheet or a reverse proxy. There is no source fork (ADR-034). Anything a
  pinned version cannot rename or theme is recorded as a limitation (TAX-151).

## Layout rules

1. **App shell on every screen** (TAX-146):
   - a 48–56px workspace header;
   - a 240px sidebar, collapsible to a 56px icon rail;
   - content up to 1280px wide, left-aligned beside the sidebar and not centred in the window.
2. **Page header**: breadcrumb, title, one-sentence description, status chip, at most one primary
   action and up to two secondary actions. Nothing else above the fold except notices.
3. **Page-length budget.** At 1440×900:
   - an operational page is at most **1.5 viewport heights**, and its main working content is
     visible without scrolling;
   - tables scroll inside a labelled region with a sticky header, and paginate at 25 rows by
     default;
   - forms longer than one viewport become steps or move into a side drawer.

   TAX-144 measures this and fails a screen that breaks it. The component showcase is exempt and
   stays out of operational navigation.
4. **Split, don't stack.**
   - A case is a workspace with routed tabs (Summary, Evidence, Relationships, Findings, Review).
   - Review actions open in a right-side drawer or dialog; they do not sit as a form below the
     page.
   - Evidence opens in a preview pane beside the list.
   - Management sections are tabs, not one long column.
5. **Density.**
   - Officer screens: 36–40px table rows and 14px text.
   - Taxpayer screens: roomier, 16px form text and one task per step.

## Visual contract (tokens: TAX-145)

Final values since TAX-145, with measured contrast: `python3 scripts/check_contrast.py` checks every text/background pair the kit uses (`apps/shared/design/contrast-pairs.json`), and `dev.py check` runs it. The components are in `apps/shared/components` ([README](../apps/shared/components/README.md)). Change them once in
`apps/shared/design/tokens.css`. Native-tool theming (TAX-151) reads the same values.

- Neutral canvas `#f7f8fa`, white surfaces, `#e4e7ec` dividers, `#101828` text, `#475467`
  supporting text.
- One accent, Vati blue `#1d4ed8` (hover `#1e40af`), for primary actions, links and the active
  navigation item only.
- Status colours always come with text:
  - info blue;
  - success green;
  - warning amber;
  - danger red;
  - neutral grey for "not available".

  Risk is a review signal, never a verdict.
- Optional dark navigation panel `#111827` with `#e5e7eb` text; content always light. Full dark
  mode is out of scope for the pilot.
- Type: system sans-serif, scale 12/13/14/16/20/24/30px, weights 400/500/600, tabular numerals for
  amounts and dates.
- Spacing: 4, 8, 12, 16, 24, 32, 48px. Radius: 6px on controls, 8px on surfaces.
- Shadows only on overlays: menus, drawers, dialogs and the command palette.
- One outline icon family at 16px and 20px; icon-only buttons have accessible names.
- Banned:
  - gradients, glass panels and glows;
  - emoji navigation and hero sections;
  - card grids used as page layout;
  - text-only decorative headings ("Five pilot scenarios");
  - developer copy in the product ("Starter workspace for …");
  - invented metrics.

## Shared components (TAX-145)

Each one is built once, shown in the showcase in every state, and reused:
- workspace header;
- app rail and sidebar;
- page header;
- notice and recommendations panel;
- table toolbar (search, filters, display options, export, primary action);
- data table with sticky header, sort, column info, empty value, row action and pagination;
- usage line;
- key-facts strip;
- tabs bound to routes;
- right-side drawer;
- confirmation dialog;
- status chip;
- empty state;
- skeleton loading;
- inline error with retry;
- toast;
- command palette (⌘K / Ctrl K) for case reference, TIN and page search.

## Visual acceptance for every UI PR

Attach screenshots of the changed screens at 1440, 1024 and 390px widths and name the browser.
Include loading, empty, error, success and denied-access states where they apply.

Record:
- keyboard navigation and visible focus;
- labels and contrast;
- zoom and reflow;
- the TAX-144 page-length result.

Automated accessibility (axe), screenshot comparison and the page-length check support this
evidence; they do not replace it.

No clipped text, page-wide horizontal scrolling, overlapping controls or inconsistent spacing.
Errors are actionable and preserve what was entered. Consequential actions keep their
confirmation and human-approval requirements. Fixture mode stays clearly labelled, but as a slim
strip inside the header, not a full-width banner. Under ADR-038 a second-person visual review is
optional and does not block merge or DONE.
