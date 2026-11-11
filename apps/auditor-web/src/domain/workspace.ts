/**
 * Vati Workspace navigation for the auditor app (TAX-146). Pure functions over the
 * server's workspace session (GET /api/v1/session), so what the shell shows follows the
 * server-side access scope: an app or view the session does not list never appears, and
 * an app that is permitted but not connected is shown as such, never as a working link.
 */
import type { RouteLocationRaw } from 'vue-router';
import type { AppLink, CommandItem, NavItem, NavSection } from '../../../shared/components';
import type { AppKey, WorkspaceSession } from '../api/session';
import type { Case, TaxpayerProfile } from '../api/vati';

export const APP_LABELS: Record<AppKey, string> = {
  cases: 'Cases',
  investigation: 'Investigation',
  explore: 'Explore',
  reports: 'Reports',
  admin: 'Admin',
};

/** Apps whose native screens open from Vati Workspace once TAX-106 connects them. */
export const NATIVE_APPS = ['investigation', 'explore', 'admin'] as const;
export type NativeApp = (typeof NATIVE_APPS)[number];

/** The case workspace tabs (TAX-148). */
export const CASE_TABS = [
  'case',
  'case-evidence',
  'evidence',
  'case-relationships',
  'case-findings',
  'case-review',
];

/** Route names that belong to each app, to mark the current tab. */
const APP_ROUTES: Record<'cases' | 'reports', string[]> = {
  cases: ['overview', 'queue', ...CASE_TABS, 'taxpayer'],
  reports: ['management', 'integrity'],
};
/** Route names that are "inside a case": the sidebar offers "Back to cases". */
const CASE_CONTEXT = [...CASE_TABS, 'taxpayer'];

function has(session: WorkspaceSession, view: WorkspaceSession['views'][number]) {
  return session.views.includes(view);
}

function appRoute(session: WorkspaceSession, key: AppKey): RouteLocationRaw {
  if (key === 'cases') return { name: has(session, 'overview') ? 'overview' : 'queue' };
  if (key === 'reports') return { name: has(session, 'management') ? 'management' : 'integrity' };
  return { name: 'native-app', params: { app: key } };
}

export function workspaceApps(
  session: WorkspaceSession,
  routeName: string | null,
  nativeApp: string | null,
): AppLink[] {
  return session.apps.map((app) => {
    const current =
      app.key === 'cases' || app.key === 'reports'
        ? APP_ROUTES[app.key].includes(routeName ?? '')
        : routeName === 'native-app' && nativeApp === app.key;
    const unavailable =
      app.availability === 'available'
        ? undefined
        : app.reason === 'not_connected'
          ? 'Not connected'
          : 'Unavailable';
    return { label: APP_LABELS[app.key], to: appRoute(session, app.key), current, unavailable };
  });
}

export function workspaceSections(
  session: WorkspaceSession,
  counts: { queue?: number } = {},
): NavSection[] {
  const cases: NavItem[] = [];
  if (has(session, 'overview'))
    cases.push({ label: 'Overview', to: { name: 'overview' }, icon: 'lucide-layout-dashboard', match: 'overview' });
  if (has(session, 'case_queue'))
    cases.push({
      label: 'My case queue',
      to: { name: 'queue' },
      icon: 'lucide-list-checks',
      match: ['queue', ...CASE_CONTEXT],
      count: counts.queue,
    });
  const reports: NavItem[] = [];
  if (has(session, 'management'))
    reports.push({ label: 'Management dashboard', to: { name: 'management' }, icon: 'lucide-gauge', match: 'management' });
  if (has(session, 'integrity'))
    reports.push({ label: 'Integrity review', to: { name: 'integrity' }, icon: 'lucide-shield-check', match: 'integrity' });
  const sections: NavSection[] = [];
  if (cases.length) sections.push({ label: 'Cases', items: cases });
  if (reports.length) sections.push({ label: 'Reports', items: reports });
  return sections;
} // prettier-ignore

export function backToCases(
  session: WorkspaceSession,
  routeName: string | null,
): NavItem | undefined {
  if (!has(session, 'case_queue') || !CASE_CONTEXT.includes(routeName ?? '')) return undefined;
  return { label: 'Back to cases', to: { name: 'queue' }, icon: 'lucide-arrow-left', match: [] };
}

/** Cases assigned to the officer that are not resolved yet (the queue count). */
export function openCount(cases: Case[]): number {
  return cases.filter((c) => c.status !== 'RESOLVED').length;
}

/**
 * Palette items: the pages, cases and taxpayers this officer may see. Cases and taxpayers
 * come from the officer's own queue and the profiles of its taxpayers; TINs are masked
 * by the server, so the last digits are what can be searched.
 */
export function commandItems(
  session: WorkspaceSession,
  cases: Case[],
  taxpayers: TaxpayerProfile[],
): CommandItem[] {
  const pages: CommandItem[] = workspaceSections(session).flatMap((section) =>
    section.items.map((item) => ({
      id: `page:${String((item.to as { name: string }).name)}`,
      label: item.label,
      group: 'Pages',
    })),
  );
  const caseItems: CommandItem[] = cases.map((c) => ({
    id: `case:${c.case_id}`,
    label: c.display_id,
    group: 'Cases',
    hint: c.taxpayer.display_name,
    keywords: [c.taxpayer.display_name],
  }));
  const taxpayerItems: CommandItem[] = taxpayers.map((t) => {
    const tin = t.tin_masked;
    return {
      id: `taxpayer:${t.taxpayer_id}`,
      label: t.display_name,
      group: 'Taxpayers',
      hint: tin ? `TIN ${tin}` : 'No TIN on record',
      keywords: tin ? [tin, tin.replace(/\D/g, '')] : [],
    };
  });
  return [...pages, ...caseItems, ...taxpayerItems];
}

export function commandTarget(item: CommandItem): RouteLocationRaw | null {
  const [kind, id] = [item.id.slice(0, item.id.indexOf(':')), item.id.slice(item.id.indexOf(':') + 1)];
  if (kind === 'page') return { name: id };
  if (kind === 'case') return { name: 'case', params: { caseId: id } };
  if (kind === 'taxpayer') return { name: 'taxpayer', params: { taxpayerId: id } };
  return null;
} // prettier-ignore
