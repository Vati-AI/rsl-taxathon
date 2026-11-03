# ADR-003: Auditor frontend stack and shared visual contract

Status: proposed (accepted on merge of the TAX-020 PR)
Owner and task: Leseli (auditor-frontend); TAX-020. Coordinate shared-token changes with Kananelo (TAX-021) and Jane (TAX-023).
Date: 2026-11-03

Context and smallest prototype need: ADR-002 selects Vue 3 + TypeScript + Vite + Frappe UI for custom screens and asks TAX-020/021 to pin compatible versions and prove a build. DESIGN_STANDARD.md requires one shared token/component contract for both custom frontends.

Decision:
- Pinned runtime: frappe-ui 1.1.0, vue 3.5.44, vue-router 4.6.4. Tooling: vite 8.3.5, @vitejs/plugin-vue 6.0.10, tailwindcss 3.4.19 (frappe-ui requires Tailwind `>=3.4.2 <4`), typescript 5.9.3 (frappe-ui depends on `typescript ^5`; TypeScript 7 not adopted), vue-tsc 3.3.14, vitest 5.1.0, @playwright/test 1.64.1, @axe-core/playwright 4.14.0. Exact versions in package.json/package-lock.json.
- `frappe-ui/vite` runs with `frappeProxy`, `buildConfig` and `jinjaBootData` disabled: there is no Frappe bench behind the auditor app, and its default `/api` proxy to port 8000 would hide the fixture server. frappe-ui data composables (`useCall`, `createResource`) are not used; the app fetches the documented fixture contract through `src/api/fixtures.ts`, which rejects any response not marked `mode: fixture` / `synthetic: true`.
- Shared visual contract: `apps/shared/design/tokens.css` (DESIGN_STANDARD colour, type, spacing and radius defaults as `--vati-*` tokens, mapped onto frappe-ui's semantic `--ink-*`, `--outline-*`, `--surface-blue-*` variables) and `apps/shared/design/tailwind.preset.ts` (frappe-ui preset plus `vati-*` utilities). TAX-021 should consume both rather than copy them.
- System sans-serif font: the app does not import `frappe-ui/style.css`, which loads the Inter web font, and declares the Tailwind layers itself.
- One icon family: frappe-ui's built-in Lucide `lucide-*` mask classes (outline icons). No separate icon package.
- Navigation: frappe-ui `DesktopShell` + `Sidebar`; the sidebar collapses to an icon rail with accessible names below the `sm` breakpoint. `MobileShell` is not used yet.
- `scripts/dev.py serve` serves the built app (`npm run build:auditor`) under `/auditor/` with a flat, allow-listed asset directory; without a build it falls back to the dependency-free starter page.

Alternatives and tradeoff: frappe-ui `MobileShell` with bottom tabs for phones (better thumb reach, but a second layout to maintain before the screen inventory is stable); importing `frappe-ui/style.css` (simpler, but Inter conflicts with the standard's system font); Tailwind 4 (not supported by frappe-ui 1.1.0). The CSS bundle is about 370 kB (39 kB gzip), mostly frappe-ui/Lucide utility classes; acceptable for an internal prototype, revisit if load time matters.

Official sources and version/date checked (2026-11-03):
- https://ui.frappe.io/ and the frappe-ui 1.1.0 package readme/source (`tailwind/preset.js`, `tailwind/content.js`, `vite/index.js`, component `*.api.md`), npm registry metadata (MIT licence, peer ranges).
- https://vite.dev/guide/troubleshooting.html#this-package-is-esm-only (config is `vite.config.mts` because the root package is CommonJS).
- https://playwright.dev/docs/browsers (installed Edge channel for local Windows runs).

Verification performed: on Windows 11, Node 22.23.4: `npm run typecheck:auditor`, `npm run test:auditor` (28 unit/component tests), `npm run build:auditor`, and `npm run test:auditor:browser` (17 Playwright smoke/axe tests, repeated twice, in Edge 156) passed. Results recorded in tasks/TAX-020.json evidence.

Dependency audit (2026-11-03): `npm audit --omit=dev` reports three moderate entries rooted in one advisory, GHSA-rj75-hqrm-r3gf (`postcss-selector-parser <6.1.5`, quadratic parsing CPU cost), reached via frappe-ui -> @tailwindcss/typography. It runs at build time over first-party CSS only and is not shipped to browsers. npm's suggested fix downgrades frappe-ui to 0.1.109, so no override was applied. The full tree reports 20 moderate entries, including the existing ESLint minimatch advisory in docs/TOOLCHAIN.md. Recheck on frappe-ui updates; this tree is not vulnerability-free.

Consequences and revisit trigger: changing a shared token changes both frontends and the showcase; screenshots must be regenerated. Revisit on a frappe-ui major release, Tailwind 4 support, or when TAX-021 needs a token not covered here.
