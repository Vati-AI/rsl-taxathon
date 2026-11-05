import { spawn, type ChildProcess } from 'node:child_process';
import { createServer } from 'node:net';
import path from 'node:path';
import type { Page } from '@playwright/test';

/**
 * A fresh synthetic fixture API (`scripts/dev.py serve`) for one test.
 *
 * The officer review operations are stateful (scripts/fixture_review.py keeps
 * proposals, approvals and evidence requests in process memory until restart).
 * The shared harness server on :58092 is used by every spec at every width in
 * parallel, so a test that approves or records something gets its own server:
 * results never leak between tests, widths, workers or `--repeat-each` runs.
 * Nothing is faked: the page's /api/v1 requests reach the real fixture server,
 * with its own principal, tenant and review rules.
 */
const root = path.resolve(__dirname, '../../..');
const python = process.env.PYTHON ?? 'python3';

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.unref();
    probe.on('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const address = probe.address();
      probe.close(() =>
        typeof address === 'object' && address
          ? resolve(address.port)
          : reject(new Error('no port')),
      );
    });
  });
}

async function healthy(base: string, deadline: number): Promise<void> {
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${base}/api/health`);
      if (res.ok) return;
    } catch {
      // Not listening yet.
    }
    await new Promise((r) => setTimeout(r, 150));
  }
  throw new Error(`Fixture API at ${base} did not start`);
}

export interface IsolatedFixtureApi {
  base: string;
  stop(): void;
}

export async function startFixtureApi(): Promise<IsolatedFixtureApi> {
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const child: ChildProcess = spawn(python, ['scripts/dev.py', 'serve', '--port', String(port)], {
    cwd: root,
    stdio: 'ignore',
    windowsHide: true,
  });
  try {
    await healthy(base, Date.now() + 20_000);
  } catch (error) {
    child.kill();
    throw error;
  }
  return { base, stop: () => child.kill() };
}

/** Send this page's /api/v1 requests to `api` instead of the shared harness server. */
export async function useFixtureApi(page: Page, api: IsolatedFixtureApi): Promise<void> {
  await page.route(/\/api\/v1\//, async (route) => {
    const from = new URL(route.request().url());
    const response = await route.fetch({ url: api.base + from.pathname + from.search });
    await route.fulfill({ response });
  });
}
