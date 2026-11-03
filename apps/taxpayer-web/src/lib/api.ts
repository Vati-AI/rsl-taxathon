import { call } from 'frappe-ui';
import type { FixtureEnvelope, PortalSession, Submission } from './types';

export type PortalErrorKind = 'unauthenticated' | 'denied' | 'not_found' | 'unavailable';

export class PortalError extends Error {
  constructor(
    readonly kind: PortalErrorKind,
    message: string,
  ) {
    super(message);
    this.name = 'PortalError';
  }
}

interface FrappeLikeError {
  exc_type?: string;
  status?: number;
}

/** Map a Frappe/transport failure to what the UI should show. Never treated as success. */
export function classifyError(error: unknown, guestCheck = false): PortalError {
  const { exc_type: excType, status } = (error ?? {}) as FrappeLikeError;
  if (excType === 'DoesNotExistError' || status === 404) {
    return new PortalError('not_found', 'We could not find that record.');
  }
  if (
    excType === 'PermissionError' ||
    excType === 'AuthenticationError' ||
    status === 401 ||
    status === 403
  ) {
    return guestCheck
      ? new PortalError('unauthenticated', 'Your session has ended. Log in again to continue.')
      : new PortalError('denied', 'You do not have access to this information.');
  }
  return new PortalError('unavailable', 'The portal could not be reached. Try again in a moment.');
}

function assertFixture(payload: { mode?: string; synthetic?: boolean }): void {
  // Fixture mode must be explicit; anything else means the contract changed.
  if (payload.mode !== 'fixture' || payload.synthetic !== true) {
    throw new PortalError('unavailable', 'The portal returned data in an unexpected format.');
  }
}

async function request<T extends { mode?: string; synthetic?: boolean }>(
  method: string,
  args: Record<string, unknown> = {},
  guestCheck = false,
): Promise<T> {
  let payload: T;
  try {
    payload = await call<T>(method, args);
  } catch (error) {
    throw error instanceof PortalError ? error : classifyError(error, guestCheck);
  }
  assertFixture(payload);
  return payload;
}

export function fetchSession(): Promise<PortalSession> {
  return request<PortalSession>('vati_tax.portal.api.get_session', {}, true);
}

export async function fetchSubmissions(): Promise<Submission[]> {
  return (await request<FixtureEnvelope<Submission[]>>('vati_tax.portal.api.get_submissions')).data;
}

export async function fetchSubmission(id: string): Promise<Submission> {
  return (
    await request<FixtureEnvelope<Submission>>('vati_tax.portal.api.get_submission', {
      submission_id: id,
    })
  ).data;
}

export async function logout(): Promise<void> {
  // Frappe v16: POST /api/method/logout clears the session cookie.
  await call('logout');
}
