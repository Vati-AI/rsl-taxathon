import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';
import { AUDITOR_PORT, TAXPAYER_PORT } from './support/env';
import { VIEWPORTS } from './support/matrix';

// TAX-023 cross-portal browser harness. One project per design-standard width
// (docs/DESIGN_STANDARD.md); every spec runs at every width.
//
// - Auditor: the built app (`npm run build:auditor`) on the synthetic fixture
//   server, on a dedicated port so a developer's own :58080 server is untouched.
// - Taxpayer: the Vite dev server. No Frappe site is needed: every
//   `vati_tax.portal.api.*` call and `/login` is answered in the browser by the
//   session mocks in support/sessions.ts, so tests never reach a real site.
const root = path.resolve(__dirname, '../..');
const python = process.env.PYTHON ?? 'python3';

export default defineConfig({
  testDir: '.',
  testMatch: '*.spec.ts',
  globalSetup: './support/warmup.ts',
  outputDir: './test-results',
  // Baselines are only added after a human accepts the screens (see README).
  snapshotPathTemplate: '{testDir}/baselines/{projectName}/{arg}{ext}',
  fullyParallel: true,
  retries: 0,
  // The taxpayer dev server compiles modules on first request; allow for a cold start.
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [['list']],
  use: {
    // Local Windows runs can use the installed, signed Edge: PLAYWRIGHT_CHANNEL=msedge.
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    trace: 'retain-on-failure',
  },
  projects: VIEWPORTS.map((viewport) => ({
    name: viewport.name,
    use: {
      ...devices['Desktop Chrome'],
      viewport: { width: viewport.width, height: viewport.height },
    },
  })),
  webServer: [
    {
      command: `${python} scripts/dev.py serve --port ${AUDITOR_PORT}`,
      cwd: root,
      url: `http://127.0.0.1:${AUDITOR_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `npx vite --config apps/taxpayer-web/vite.config.mts --port ${TAXPAYER_PORT} --strictPort`,
      cwd: root,
      url: `http://127.0.0.1:${TAXPAYER_PORT}/taxpayer/`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
