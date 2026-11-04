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

export const AUDITOR_ROUTES: MatrixRoute[] = [
  { name: 'overview', path: '/auditor/', heading: 'Overview' },
  { name: 'queue', path: '/auditor/cases', heading: 'Case queue' },
  { name: 'case-detail', path: '/auditor/cases/SYN-002', heading: /^SYN-002 · / },
  { name: 'showcase', path: '/auditor/showcase', heading: 'Component showcase' },
  { name: 'not-found', path: '/auditor/no-such-page', heading: 'Page not found' },
];

export const TAXPAYER_ROUTES: MatrixRoute[] = [
  { name: 'submissions', path: '/taxpayer/submissions', heading: 'Your submissions' },
  { name: 'submission', path: '/taxpayer/submissions/SUB-SYN-0002', heading: /^VAT return · / },
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
