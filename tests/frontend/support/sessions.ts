/**
 * Role/session mocks for browser tests. Everything here is synthetic and runs
 * only inside the test browser; no Frappe site, account or password is used.
 *
 * Taxpayer portal: `vati_tax.portal.api.*` calls are answered in the browser
 * using the same records the Frappe app serves (vati_tax/portal/fixtures.py,
 * loaded below so the two cannot drift). Response shapes follow Frappe v16:
 * success is `{ message }`, a refused call is HTTP 403 with `exc_type`.
 *
 * Personas describe session states, not RSL roles. Taxpayer roles and tenant
 * permissions are defined by TAX-029; until then `denied` stands in for "a
 * signed-in user whom the portal API refuses".
 *
 * Auditor workspace: the fixture API (scripts/fixture_api.py) has one signed-in
 * officer principal scoped to one synthetic tenant. Personas choose whether
 * requests stay in that tenant or select another one, which the API refuses.
 */
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import type { Page, Route } from '@playwright/test';

const root = path.resolve(__dirname, '../../..');

/** Synthetic Website User shown by the portal mocks. Not a real account. */
export const SYNTHETIC_TAXPAYER = {
  user: 'taxpayer.a@example.test',
  full_name: 'Synthetic Taxpayer A',
} as const;

export type TaxpayerPersona = 'taxpayer' | 'guest' | 'denied';
export type AuditorPersona = 'officer' | 'denied';

interface PortalSubmission {
  id: string;
  [key: string]: unknown;
}

function loadPortalFixtures(): PortalSubmission[] {
  const script = [
    'import importlib.util, json, sys',
    "spec = importlib.util.spec_from_file_location('fixtures', sys.argv[1])",
    'module = importlib.util.module_from_spec(spec)',
    'spec.loader.exec_module(module)',
    'print(json.dumps(module.list_submissions()))',
  ].join('\n');
  const file = path.join(root, 'backend/vati_tax/vati_tax/portal/fixtures.py');
  const out = execFileSync(process.env.PYTHON ?? 'python3', ['-c', script, file], {
    encoding: 'utf8',
  });
  return JSON.parse(out) as PortalSubmission[];
}

export const PORTAL_SUBMISSIONS = loadPortalFixtures();

const FIXTURE = { mode: 'fixture', synthetic: true } as const;

function ok(route: Route, message: unknown) {
  return route.fulfill({ status: 200, json: { message } });
}

function refused(route: Route, excType: string, status: number, text: string) {
  return route.fulfill({ status, json: { exc_type: excType, exception: `${excType}: ${text}` } });
}

/** A stand-in for Frappe's /login page, clearly labelled as a mock. */
const LOGIN_STUB = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Login (session mock)</title></head><body><main>
<h1>Login</h1><p>Session mock: Frappe's login page is not part of the browser harness.</p>
</main></body></html>`;

export interface PortalMock {
  /** Calls the portal made, in order, for assertions. */
  calls: string[];
  persona: TaxpayerPersona;
}

/**
 * Answer every portal API call for `persona`. Unhandled `/api/` requests are
 * refused in the browser, so the Vite proxy never forwards a test request to a
 * Frappe site a developer may have running on :58000.
 */
export async function mockTaxpayerSession(
  page: Page,
  persona: TaxpayerPersona,
): Promise<PortalMock> {
  const mock: PortalMock = { calls: [], persona };

  await page.route('**/login**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: LOGIN_STUB }),
  );

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const method = url.pathname.replace(/^\/api\/method\//, '');
    mock.calls.push(method);
    const guest = mock.persona === 'guest';

    if (method === 'logout') {
      mock.persona = 'guest';
      return ok(route, null);
    }
    if (method === 'vati_tax.portal.api.get_session') {
      // Guests: the method is not guest-whitelisted, so Frappe answers 403.
      return guest
        ? refused(route, 'PermissionError', 403, 'Not permitted')
        : ok(route, { ...FIXTURE, ...SYNTHETIC_TAXPAYER });
    }
    if (method === 'vati_tax.portal.api.get_submissions') {
      return mock.persona === 'taxpayer'
        ? ok(route, { ...FIXTURE, data: PORTAL_SUBMISSIONS })
        : refused(route, 'PermissionError', 403, 'Not permitted');
    }
    if (method === 'vati_tax.portal.api.get_submission') {
      if (mock.persona !== 'taxpayer')
        return refused(route, 'PermissionError', 403, 'Not permitted');
      const body = (route.request().postDataJSON() ?? {}) as { submission_id?: string };
      const item = PORTAL_SUBMISSIONS.find((x) => x.id === body.submission_id);
      return item
        ? ok(route, { ...FIXTURE, data: item })
        : refused(route, 'DoesNotExistError', 404, 'Submission not found.');
    }
    return route.fulfill({
      status: 501,
      json: { error: 'not_mocked', method, ...FIXTURE },
    });
  });

  return mock;
}

/** A synthetic tenant outside the fixture principal's scope (integration/api/fixtures). */
export const OTHER_TENANT_ID = '10000000-0000-4000-8000-000000000002';

/**
 * `officer`: requests go unchanged to the fixture API as its signed-in principal.
 * `denied`: every Vati API request selects a tenant outside that principal's
 * scope, so the real fixture API refuses it (403 tenant_scope_denied). Nothing
 * is faked in the browser; the server's own access rule is exercised.
 */
export async function mockAuditorAccess(page: Page, persona: AuditorPersona): Promise<void> {
  if (persona === 'officer') return;
  await page.route(/\/api\/v1\//, (route) =>
    route.continue({
      headers: { ...route.request().headers(), 'x-vati-tenant-id': OTHER_TENANT_ID },
    }),
  );
}
