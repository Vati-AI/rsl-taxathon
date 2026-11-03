import { reactive } from 'vue';
import { fetchSession, logout as apiLogout, PortalError } from './api';
import { loginUrl } from './portal-path';
import type { PortalSession } from './types';

export type SessionState =
  | { status: 'idle' | 'loading' }
  | { status: 'ready'; session: PortalSession }
  | { status: 'error'; error: PortalError };

export const sessionState = reactive<{ value: SessionState }>({ value: { status: 'idle' } });

let pending: Promise<SessionState> | null = null;

/** Load the session once; concurrent navigations share the same request. */
export function ensureSession(): Promise<SessionState> {
  if (sessionState.value.status === 'ready') return Promise.resolve(sessionState.value);
  if (!pending) {
    sessionState.value = { status: 'loading' };
    pending = fetchSession()
      .then((session): SessionState => ({ status: 'ready', session }))
      .catch((error: unknown): SessionState => ({
        status: 'error',
        error: error instanceof PortalError ? error : new PortalError('unavailable', String(error)),
      }))
      .then((state) => {
        sessionState.value = state;
        pending = null;
        return state;
      });
  }
  return pending;
}

export function resetSession(): void {
  sessionState.value = { status: 'idle' };
  pending = null;
}

export interface Navigator {
  assign(url: string): void;
}

export function redirectToLogin(path: string, nav: Navigator = window.location): void {
  nav.assign(loginUrl(path));
}

/** Log out, then always leave the portal, even if the server call fails. */
export async function logout(nav: Navigator = window.location): Promise<void> {
  try {
    await apiLogout();
  } finally {
    resetSession();
    nav.assign('/login');
  }
}
