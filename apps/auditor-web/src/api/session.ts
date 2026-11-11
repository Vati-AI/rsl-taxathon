/**
 * getWorkspaceSession (GET /api/v1/session, TAX-146): who is signed in and which Vati
 * Workspace apps and views the server permits. The shell builds its tabs and sidebar
 * from this; every page still relies on the server enforcing access on its own calls.
 */
import { apiRequest, invalid, isRecord, UUID, type Fetch } from './vati';

export const APP_KEYS = ['cases', 'investigation', 'explore', 'reports', 'admin'] as const;
export type AppKey = (typeof APP_KEYS)[number];
export const VIEW_KEYS = ['overview', 'case_queue', 'management', 'integrity'] as const;
export type ViewKey = (typeof VIEW_KEYS)[number];
export type Role = 'auditor' | 'specialist' | 'supervisor' | 'administrator';

export interface WorkspaceApp {
  key: AppKey;
  availability: 'available' | 'unavailable';
  reason: 'not_connected' | 'dependency_unavailable' | null;
}

export interface WorkspaceSession {
  tenant_id: string;
  tenant_name: string;
  officer_id: string;
  display_name: string;
  roles: Role[];
  apps: WorkspaceApp[];
  views: ViewKey[];
  synthetic: boolean;
}

const ROLES: Role[] = ['auditor', 'specialist', 'supervisor', 'administrator'];

/** Validate the session; anything unexpected is refused rather than guessed. */
export function parseWorkspaceSession(value: unknown): WorkspaceSession {
  if (!isRecord(value)) invalid('a session');
  for (const key of ['tenant_id', 'tenant_name', 'officer_id', 'display_name']) {
    if (typeof value[key] !== 'string' || value[key] === '') invalid('a session');
  }
  if (!UUID.test(value.tenant_id as string) || !UUID.test(value.officer_id as string))
    invalid('a session');
  if (!Array.isArray(value.roles) || !value.roles.every((r) => ROLES.includes(r as Role)))
    invalid('session roles');
  if (!Array.isArray(value.apps)) invalid('session apps');
  for (const app of value.apps) {
    if (!isRecord(app) || !APP_KEYS.includes(app.key as AppKey)) invalid('a workspace app');
    if (app.availability !== 'available' && app.availability !== 'unavailable')
      invalid('a workspace app');
  }
  if (!Array.isArray(value.views) || !value.views.every((v) => VIEW_KEYS.includes(v as ViewKey)))
    invalid('session views');
  if (typeof value.synthetic !== 'boolean') invalid('a session');
  return value as unknown as WorkspaceSession;
}

export async function getWorkspaceSession(fetchImpl: Fetch = fetch, signal?: AbortSignal) {
  const body = await apiRequest('/api/v1/session', { signal }, fetchImpl);
  return parseWorkspaceSession(body.data);
}
