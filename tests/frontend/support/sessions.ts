/**
 * Role/session mocks for browser tests. Everything here is synthetic and runs
 * only inside the test browser; no Frappe site, account or password is used.
 *
 * Taxpayer portal: `vati_tax.portal.api.*` calls are answered in the browser
 * from the records and ownership rules the Frappe app uses
 * (vati_tax/portal/fixtures.py, called below so the two cannot drift).
 * Response shapes follow Frappe v16: success is `{ message }`, a refused call
 * is HTTP 403 with `exc_type`. Access follows TAX-029 (docs/PERMISSIONS.md):
 * only a user with a taxpayer membership is served, and only their own
 * taxpayer's submissions; another taxpayer's submission is "not found".
 *
 * Auditor workspace: the fixture API (scripts/fixture_api.py) has one signed-in
 * officer principal scoped to one synthetic tenant. Personas choose whether
 * requests stay in that tenant or select another one, which the API refuses.
 */
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import type { Page, Route } from '@playwright/test';

const root = path.resolve(__dirname, '../../..');

/**
 * The synthetic taxpayer user from docs/PERMISSIONS.md: a taxpayer membership in
 * the fixture tenant for the taxpayer that owns SUB-SYN-0001. Not a real account.
 */
export const SYNTHETIC_TAXPAYER = {
  user: 'taxpayer.a@example.test',
  full_name: 'Synthetic Taxpayer A',
  taxpayer_id: '80015243-dd34-4e97-b085-7356ffdd042f',
} as const;

/**
 * - `taxpayer`: signed in with the membership above.
 * - `guest`: not signed in; Frappe refuses every portal method with 403.
 * - `no-membership`: signed in, but not linked to a taxpayer; refused with 403.
 * - `revoked`: the portal loaded, then the membership was removed, so data calls
 *   are refused while the app still holds the earlier session.
 */
export type TaxpayerPersona = 'taxpayer' | 'guest' | 'no-membership' | 'revoked';
export type AuditorPersona = 'officer' | 'denied';

interface PortalSubmission {
  id: string;
  [key: string]: unknown;
}

interface PortalFixtures {
  /** Every fixture ID, owned by anyone. */
  all_ids: string[];
  /** list_submissions(scope) for SYNTHETIC_TAXPAYER. */
  visible: PortalSubmission[];
}

function loadPortalFixtures(): PortalFixtures {
  const script = [
    'import importlib.util, json, sys',
    "spec = importlib.util.spec_from_file_location('fixtures', sys.argv[1])",
    'm = importlib.util.module_from_spec(spec)',
    'spec.loader.exec_module(m)',
    'scope = [(m.FIXTURE_TENANT_ID, sys.argv[2])]',
    "print(json.dumps({'all_ids': [s['id'] for s in m.SUBMISSIONS],",
    "                  'visible': m.list_submissions(scope)}))",
  ].join('\n');
  const file = path.join(root, 'backend/vati_tax/vati_tax/portal/fixtures.py');
  const out = execFileSync(
    process.env.PYTHON ?? 'python3',
    ['-c', script, file, SYNTHETIC_TAXPAYER.taxpayer_id],
    { encoding: 'utf8' },
  );
  return JSON.parse(out) as PortalFixtures;
}

const fixtures = loadPortalFixtures();

/** Submissions the synthetic taxpayer may see (their own). */
export const PORTAL_SUBMISSIONS = fixtures.visible;
/** A fixture submission owned by another taxpayer, which must stay concealed. */
export const OTHER_TAXPAYER_SUBMISSION_ID = fixtures.all_ids.find(
  (id) => !PORTAL_SUBMISSIONS.some((s) => s.id === id),
)!;

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
    const p = mock.persona;

    if (method === 'logout') {
      mock.persona = 'guest';
      return ok(route, null);
    }
    if (method === 'vati_tax.portal.api.get_session') {
      if (p === 'guest') return refused(route, 'PermissionError', 403, 'Not permitted');
      if (p === 'no-membership')
        return refused(route, 'PermissionError', 403, 'This account is not linked to a taxpayer.');
      const { user, full_name } = SYNTHETIC_TAXPAYER;
      return ok(route, { ...FIXTURE, user, full_name });
    }
    if (p !== 'taxpayer' && method.startsWith('vati_tax.portal.api.')) {
      return refused(route, 'PermissionError', 403, 'This account is not linked to a taxpayer.');
    }
    if (method === 'vati_tax.portal.api.get_submissions') {
      return ok(route, { ...FIXTURE, data: PORTAL_SUBMISSIONS });
    }
    if (method === 'vati_tax.portal.api.get_submission') {
      const body = (route.request().postDataJSON() ?? {}) as { submission_id?: string };
      // As find_submission(id, scope): another taxpayer's record is concealed.
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
