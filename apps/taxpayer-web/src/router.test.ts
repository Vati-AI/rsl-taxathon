import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory } from 'vue-router';

const call = vi.fn();
vi.mock('frappe-ui', () => ({ call }));

const { createPortalRouter } = await import('./router');
const { logout, resetSession, sessionState } = await import('./lib/session');

const SESSION = {
  mode: 'fixture',
  synthetic: true,
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

  it('rejects a session payload that is not explicitly synthetic fixture data', async () => {
    call.mockResolvedValue({ ...SESSION, mode: 'live' });
    const router = createPortalRouter(createMemoryHistory(), nav());
    await router.push('/submissions');
    expect(sessionState.value.status).toBe('error');
  });

  it('routes unknown portal paths to the not-found page', async () => {
    call.mockResolvedValue(SESSION);
    const router = createPortalRouter(createMemoryHistory(), nav());
    await router.push('/nope/at/all');
    expect(router.currentRoute.value.name).toBe('not-found');
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
