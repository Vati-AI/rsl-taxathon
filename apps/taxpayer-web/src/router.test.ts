import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory } from 'vue-router';

const call = vi.fn();
vi.mock('./lib/frappe-call', () => ({ call }));

// These tests cover the session guard, not page rendering: stub the lazily loaded
// pages so a route change does not compile the whole component library.
// vi.mock calls are hoisted above imports, so the shared factory must be hoisted too.
const { stubPage } = vi.hoisted(() => ({
  stubPage: () => ({ default: { render: () => null } }),
}));
vi.mock('./pages/SubmissionsPage.vue', stubPage);
vi.mock('./pages/SubmissionPage.vue', stubPage);
vi.mock('./pages/NewSubmissionPage.vue', stubPage);
vi.mock('./pages/AccountPage.vue', stubPage);
vi.mock('./pages/NotFoundPage.vue', stubPage);
vi.mock('./pages/EvidenceRequestsPage.vue', stubPage);
vi.mock('./pages/EvidenceRequestPage.vue', stubPage);

const { createPortalRouter } = await import('./router');
const { logout, resetSession, sessionState } = await import('./lib/session');

const SESSION = {
  mode: 'live',
  user: 'a@example.test',
  full_name: 'Taxpayer A',
};

function nav() {
  return { assign: vi.fn<(url: string) => void>() };
}

describe('portal router session boundary', () => {
  beforeEach(() => {
    call.mockReset();
    resetSession();
  });

  it('lets an authenticated user navigate to a portal route', async () => {
    call.mockResolvedValue(SESSION);
    const navigator = nav();
    const router = createPortalRouter(createMemoryHistory(), navigator);
    await router.push('/submissions');
    expect(router.currentRoute.value.name).toBe('submissions');
    expect(navigator.assign).not.toHaveBeenCalled();
    expect(sessionState.value.status).toBe('ready');
  });

  it('sends an expired session to login and blocks the route', async () => {
    call.mockRejectedValue({ exc_type: 'PermissionError', status: 403 });
    const navigator = nav();
    const router = createPortalRouter(createMemoryHistory(), navigator);
    await router.push('/submissions/SUB-SYN-0002');
    expect(navigator.assign).toHaveBeenCalledWith(
      '/login?redirect-to=%2Ftaxpayer%2Fsubmissions%2FSUB-SYN-0002',
    );
    expect(router.currentRoute.value.name).toBeUndefined();
  });

  it('does not redirect to login when the server is unreachable', async () => {
    call.mockRejectedValue(new TypeError('Failed to fetch'));
    const navigator = nav();
    const router = createPortalRouter(createMemoryHistory(), navigator);
    await router.push('/submissions');
    expect(navigator.assign).not.toHaveBeenCalled();
    expect(sessionState.value).toMatchObject({ status: 'error', error: { kind: 'unavailable' } });
  });

  it('accepts fixture data only when it is labelled synthetic (TAX-092)', async () => {
    call.mockResolvedValue({ ...SESSION, mode: 'fixture', synthetic: true });
    await createPortalRouter(createMemoryHistory(), nav()).push('/submissions');
    expect(sessionState.value.status).toBe('ready');
  });

  for (const payload of [{ mode: 'fixture' }, { mode: 'demo' }, { mode: undefined }]) {
    it(`rejects a session that does not say where its data comes from: ${JSON.stringify(payload)}`, async () => {
      resetSession();
      call.mockResolvedValue({ ...SESSION, ...payload });
      await createPortalRouter(createMemoryHistory(), nav()).push('/submissions');
      expect(sessionState.value.status).toBe('error');
    });
  }

  it('routes unknown portal paths to the not-found page', async () => {
    call.mockResolvedValue(SESSION);
    const router = createPortalRouter(createMemoryHistory(), nav());
    await router.push('/nope/at/all');
    expect(router.currentRoute.value.name).toBe('not-found');
  });
});

describe('evidence-first portal (TAX-125)', () => {
  beforeEach(() => {
    call.mockReset();
    resetSession();
  });

  it('opens on evidence requests', async () => {
    call.mockResolvedValue(SESSION);
    const router = createPortalRouter(createMemoryHistory(), nav());
    await router.push('/');
    expect(router.currentRoute.value.name).toBe('evidence-requests');
  });

  it('keeps a deep link to one evidence request', async () => {
    call.mockResolvedValue(SESSION);
    const router = createPortalRouter(createMemoryHistory(), nav());
    await router.push('/requests/45000000-0000-4000-8000-000000000001');
    expect(router.currentRoute.value.name).toBe('evidence-request');
    expect(router.currentRoute.value.params.id).toBe('45000000-0000-4000-8000-000000000001');
  });

  it('sends a signed-out deep link to login and back to the same request', async () => {
    call.mockRejectedValue({ exc_type: 'PermissionError', status: 403 });
    const navigator = nav();
    await createPortalRouter(createMemoryHistory(), navigator).push('/requests/R-1');
    expect(navigator.assign).toHaveBeenCalledWith(
      '/login?redirect-to=%2Ftaxpayer%2Frequests%2FR-1',
    );
  });

  it('has no claim form unless the site enables the Online Refunds simulator', async () => {
    call.mockResolvedValue(SESSION);
    const router = createPortalRouter(createMemoryHistory(), nav());
    await router.push('/submissions/new');
    expect(router.currentRoute.value.name).toBe('not-found');
    expect(router.currentRoute.value.fullPath).toBe('/submissions/new');
  });

  it('opens the simulator on a site that enables it', async () => {
    call.mockResolvedValue({ ...SESSION, simulator_enabled: true });
    const router = createPortalRouter(createMemoryHistory(), nav());
    await router.push('/submissions/new');
    expect(router.currentRoute.value.name).toBe('new-submission');
  });
});

describe('logout', () => {
  beforeEach(() => {
    call.mockReset();
    resetSession();
  });

  it('calls Frappe logout and leaves the portal', async () => {
    call.mockResolvedValue(undefined);
    const navigator = nav();
    await logout(navigator);
    expect(call).toHaveBeenCalledWith('logout');
    expect(navigator.assign).toHaveBeenCalledWith('/login');
    expect(sessionState.value.status).toBe('idle');
  });

  it('still leaves the portal if the logout request fails', async () => {
    call.mockRejectedValue(new TypeError('Failed to fetch'));
    const navigator = nav();
    await expect(logout(navigator)).rejects.toThrow();
    expect(navigator.assign).toHaveBeenCalledWith('/login');
  });
});
