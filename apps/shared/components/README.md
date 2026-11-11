# Vati Workspace component kit

Shared Vue 3 components for the auditor app and the taxpayer portal (TAX-145,
[DESIGN_STANDARD.md](../../../docs/DESIGN_STANDARD.md) v2, ADR-044). Built on frappe-ui and
reka-ui (the dialog and popover primitives frappe-ui uses). Import from
`apps/shared/components` (`index.ts`). Every component appears, in its states, in the
auditor showcase (`/auditor/showcase`, `apps/auditor-web/src/components/KitShowcase.vue`).
Behaviour tests: `apps/auditor-web/src/components/kit.test.ts`; browser checks:
`tests/frontend/kit.spec.ts`.

| Component         | Use                                                                                         |
| ----------------- | ------------------------------------------------------------------------------------------- |
| `WorkspaceHeader` | Product name, app tabs, search (opens the palette), fixture strip, account                  |
| `AppSidebar`      | Named sections with counts, "Back to …", collapses to a 56px rail, optional dark panel      |
| `PageHeader`      | Breadcrumb, title, one sentence, status chip, up to three actions                           |
| `NoticePanel`     | One notice with a headline, one line, optional collapsible recommendations                  |
| `TableToolbar`    | Search, filters, display, export and one primary action                                     |
| `DataTable`       | Sticky header, sort, column info, empty dash, row action, 25-row pages, loading/empty/error |
| `UsageLine`       | "Showing 25 of 143 cases · 3 overdue"                                                       |
| `KeyFacts`        | The first facts a reader needs, in one strip                                                |
| `RoutedTabs`      | Tabs that are links to their own URL                                                        |
| `SideDrawer`      | Right-side modal drawer for review actions and long forms                                   |
| `ConfirmDialog`   | Confirmation of a consequential action, named on the button                                 |
| `StatusChip`      | A status in words with a tone                                                               |
| `EmptyState`      | Heading, one sentence, two or three actions                                                 |
| `SkeletonBlock`   | Loading placeholder announced once                                                          |
| `InlineError`     | What failed, retry, optional reference                                                      |
| `notify()`        | Toast for something that just happened                                                      |
| `CommandPalette`  | ⌘K / Ctrl K search over case references, TINs and pages                                     |

Colours come only from `apps/shared/design/tokens.css`. When a component uses a new
text/background pair, add it to `apps/shared/design/contrast-pairs.json`;
`python3 scripts/check_contrast.py` must still pass.
