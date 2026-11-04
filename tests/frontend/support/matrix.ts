import { readFileSync } from 'node:fs';
import path from 'node:path';

/**
 * The viewport, route and keyboard test matrix for both frontends.
 *
 * Widths come from docs/DESIGN_STANDARD.md (1440/1024/390). Each Playwright
 * project is one width; specs read `testInfo.project.name` to know which one.
 */

export interface Viewport {
  name: 'desktop-1440' | 'tablet-1024' | 'mobile-390';
  width: number;
  height: number;
}

export const VIEWPORTS: Viewport[] = [
  { name: 'desktop-1440', width: 1440, height: 900 },
  { name: 'tablet-1024', width: 1024, height: 768 },
  { name: 'mobile-390', width: 390, height: 844 },
];

export interface MatrixRoute {
  /** Short stable name, also used for screenshot file names. */
  name: string;
  path: string;
  /** Accessible name of the page's single level-1 heading once loaded. */
  heading: string | RegExp;
}

// SYN-002 from integration/api/fixtures/auditor_queue.json (TAX-040 fixture API).
const queue = JSON.parse(
  readFileSync(
    path.resolve(__dirname, '../../../integration/api/fixtures/auditor_queue.json'),
    'utf8',
  ),
) as { cases: { case_id: string; display_id: string; taxpayer_id: string }[] };
/** The auditor fixture case with display ID `displayId`, e.g. SYN-002. */
export function auditorCase(displayId: string) {
  const item = queue.cases.find((c) => c.display_id === displayId);
  if (!item) throw new Error(`No auditor fixture case ${displayId}`);
  return item;
}
const syn002 = auditorCase('SYN-002');

export const AUDITOR_ROUTES: MatrixRoute[] = [
  { name: 'overview', path: '/auditor/', heading: 'Overview' },
  { name: 'queue', path: '/auditor/cases', heading: 'My case queue' },
  {
    name: 'case-detail',
    path: `/auditor/cases/${syn002.case_id}`,
    heading: 'SYN-002 · Synthetic Border Retail',
  },
  {
    // TAX-042 decision, evidence request and referral page.
    name: 'case-review',
    path: `/auditor/cases/${syn002.case_id}/review`,
    heading: 'Review and decide · SYN-002',
  },
  {
    name: 'taxpayer',
    path: `/auditor/taxpayers/${syn002.taxpayer_id}`,
    heading: 'Synthetic Border Retail',
  },
  { name: 'showcase', path: '/auditor/showcase', heading: 'Component showcase' },
  { name: 'not-found', path: '/auditor/no-such-page', heading: 'Page not found' },
];

export const TAXPAYER_ROUTES: MatrixRoute[] = [
  { name: 'submissions', path: '/taxpayer/submissions', heading: 'Your submissions' },
  // SUB-SYN-0001: owned by the synthetic taxpayer user (docs/PERMISSIONS.md, TAX-029).
  {
    name: 'submission',
    path: '/taxpayer/submissions/SUB-SYN-0001',
    heading: /^VAT refund claim · /,
  },
  { name: 'new-submission', path: '/taxpayer/submissions/new', heading: 'New submission' },
  { name: 'account', path: '/taxpayer/account', heading: 'Account' },
  { name: 'not-found', path: '/taxpayer/no-such-page', heading: 'Page not found' },
];

/**
 * Keyboard checks each portal must keep passing. The specs implement them;
 * this list is the reviewable inventory (README links here).
 */
export const KEYBOARD_MATRIX = {
  auditor: [
    'First Tab focuses a visible "Skip to main content" link; Enter moves focus to the main region',
    'Sidebar links are focusable, show a visible focus outline and navigate on Enter',
  ],
  taxpayer: [
    'First Tab focuses a visible "Skip to main content" link; Enter moves focus to the main region',
    'Navigation links (sidebar on desktop, bottom bar on mobile) navigate on Enter',
    'Submitting an incomplete form with Enter keeps values and moves focus to the first invalid field',
  ],
} as const;

/** Desktop shells show a sidebar; below 768px the taxpayer portal uses a bottom bar. */
export function isMobile(projectName: string): boolean {
  return projectName === 'mobile-390';
}
