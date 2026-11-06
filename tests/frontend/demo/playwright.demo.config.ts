import { defineConfig } from '@playwright/test';

/**
 * Demo-video capture (not a test gate). Records the scripted product walkthrough
 * in demo.spec.ts against already-running local servers:
 *   python3 scripts/dev.py serve                                   # :58080 fixture API + auditor build
 *   npx vite --config apps/taxpayer-web/vite.config.mts            # :58082 taxpayer portal
 * Then: npx playwright test --config tests/frontend/demo/playwright.demo.config.ts
 * and:  bash scripts/make_demo_video.sh
 * Synthetic fixture data only.
 */
export default defineConfig({
  testDir: '.',
  testMatch: 'demo.spec.ts',
  timeout: 300_000,
  workers: 1,
  outputDir: '../../../docs/demo/raw',
  reporter: 'line',
  use: {
    viewport: { width: 1440, height: 900 },
    video: 'off',
    trace: 'off',
    actionTimeout: 10_000,
    navigationTimeout: 20_000,
    launchOptions: process.env.DEMO_CHROMIUM ? { executablePath: process.env.DEMO_CHROMIUM } : {},
  },
});
