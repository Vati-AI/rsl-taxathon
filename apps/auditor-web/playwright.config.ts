import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';

// Browser smoke checks for the built auditor app (run `npm run build:auditor`
// first). A dedicated fixture server port keeps tests independent of any
// server a developer already has running on 58080.
// Playwright loads this config as CommonJS, so __dirname is available.
const root = path.resolve(__dirname, '../..');
const port = Number(process.env.AUDITOR_E2E_PORT ?? 58091);
const python = process.env.PYTHON ?? 'python3';

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  fullyParallel: true,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    // Local Windows runs can use the installed, signed Edge: PLAYWRIGHT_CHANNEL=msedge.
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: {
    command: `${python} scripts/dev.py serve --port ${port}`,
    cwd: root,
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
