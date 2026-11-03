/**
 * Client mirror of vati_tax/portal/access.py: login redirects may only return
 * to a path inside the portal. Keep both implementations in step.
 */
export const PORTAL_ROOT = '/taxpayer';

export function portalPath(path: string | null | undefined): string {
  if (!path) return PORTAL_ROOT;
  if (/^[a-z][a-z0-9+.-]*:/i.test(path) || path.startsWith('//') || path.includes('\\')) {
    return PORTAL_ROOT;
  }
  const clean = path.split(/[?#]/, 1)[0];
  const segments = clean.split('/');
  if (segments.includes('..') || segments.includes('.') || clean.includes('//')) {
    return PORTAL_ROOT;
  }
  if (clean === PORTAL_ROOT || clean.startsWith(PORTAL_ROOT + '/')) return clean;
  return PORTAL_ROOT;
}

export function loginUrl(path: string | null | undefined): string {
  return `/login?${new URLSearchParams({ 'redirect-to': portalPath(path) }).toString()}`;
}
