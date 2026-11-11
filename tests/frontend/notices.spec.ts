import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import {
  hasVisibleFocus,
  horizontalOverflow,
  pageReady,
  seriousAxeViolations,
  tabUntilFocused,
} from './support/checks';
import { AUDITOR, TAXPAYER } from './support/env';
import { findComponentNames, measurePageLength } from './support/layout';
import { mockAuditorAccess, mockTaxpayerSession } from './support/sessions';

// TAX-150: one product name (docs/DESIGN_STANDARD.md register) and the About ·
// Open-source notices page, reachable from the auditor user menu and from the taxpayer
// portal's footer and account menu. CAPTURE_SCREENSHOTS=1 writes docs/evidence/TAX-150.

const HEADING = 'Open-source notices';
const COMPONENTS = ['Jube', 'OpenAleph', 'Frappe Framework', 'Docling Serve', 'yente', 'LangGraph'];

async function shot(page: Page, name: string, project: string) {
  if (!process.env.CAPTURE_SCREENSHOTS) return;
  await page.screenshot({
    path: path.resolve(
      __dirname,
      `../../docs/evidence/TAX-150/${name}-${project.split('-')[1]}.png`,
    ),
    fullPage: true,
  });
}

async function checkNoticesPage(page: Page) {
  const region = page.locator('[data-component-names-allowed]').first();
  for (const name of COMPONENTS)
    await expect(region.getByRole('rowheader', { name }), name).toBeVisible();
  // Each row links the upstream source and the licence text.
  const jube = page.getByRole('row').filter({ hasText: 'Jube' });
  await expect(jube).toContainText('Vati Workspace · Investigation');
  await expect(jube.getByRole('link', { name: 'AGPL-3.0 licence for Jube' })).toHaveAttribute(
    'href',
    /agpl-3\.0/,
  );
  await expect(page.getByRole('region', { name: 'Libraries: open-source notices' })).toBeVisible();
  // The names are inside the exempt region only; the rest of the page uses the register.
  expect(await findComponentNames(page)).toEqual([]);
  expect(await seriousAxeViolations(page)).toEqual([]);
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
}

test('auditor: user menu opens About · Open-source notices', async ({ page }, testInfo) => {
  await page.goto(AUDITOR + '/auditor/');
  await pageReady(page, 'Overview');
  await page.getByRole('button', { name: /Synthetic auditor/ }).click();
  const link = page.getByRole('link', { name: 'About · Open-source notices' });
  await expect(link).toBeVisible();
  await link.click();
  await pageReady(page, HEADING);
  await expect(page).toHaveTitle('Open-source notices · Vati Workspace · Cases');
  await checkNoticesPage(page);
  const length = await measurePageLength(page);
  testInfo.annotations.push({ type: 'page-length', description: `${length.viewports}` });
  await shot(page, 'auditor-notices', testInfo.project.name);
});

test('auditor: keyboard reaches the licence links with a visible focus', async ({ page }) => {
  await page.goto(AUDITOR + '/auditor/about/notices');
  await pageReady(page, HEADING);
  const link = page.getByRole('link', { name: 'Jube', exact: true });
  await tabUntilFocused(page, link, 60);
  expect(await hasVisibleFocus(link)).toBe(true);
});

test('taxpayer: footer and account menu open the notices page', async ({ page }, testInfo) => {
  await mockTaxpayerSession(page, 'taxpayer');
  await page.goto(TAXPAYER + '/taxpayer/requests');
  await pageReady(page, 'Evidence requests');
  const footer = page.locator('footer', { hasText: 'Vati Taxpayer Portal' });
  await footer.getByRole('link', { name: 'Open-source notices' }).click();
  await pageReady(page, HEADING);
  await expect(page).toHaveTitle('Open-source notices · Vati Taxpayer Portal');
  await checkNoticesPage(page);
  await shot(page, 'taxpayer-notices', testInfo.project.name);

  await page.goto(TAXPAYER + '/taxpayer/account');
  await pageReady(page, 'Account');
  await page
    .getByRole('button', { name: /Synthetic Taxpayer A/ })
    .first()
    .click();
  await page.getByRole('link', { name: 'About · Open-source notices' }).click();
  await pageReady(page, HEADING);
});

test('auditor: provenance values keep the service identifiers', async ({ page }, testInfo) => {
  // The case's connected records list each service under its Vati name and keep the
  // service's own record identifier verbatim (TAX-150 acceptance 3).
  await page.goto(AUDITOR + '/auditor/cases');
  await pageReady(page, 'My case queue');
  await page
    .getByRole('link', { name: /SYN-002/ })
    .first()
    .click();
  await expect(page.locator('[data-kit="provenance-value"]').first()).toBeAttached();
  expect(await findComponentNames(page)).toEqual([]);
  await shot(page, 'auditor-case-detail', testInfo.project.name);
});

test('taxpayer: the footer offers the notices in the loading and error states', async ({
  page,
}, testInfo) => {
  await mockTaxpayerSession(page, 'taxpayer');
  // Loading: hold the session call, then answer it with a server error.
  let release: () => void = () => {};
  const held = new Promise<void>((r) => (release = r));
  await page.route('**/vati_tax.portal.api.get_session*', async (route) => {
    await held;
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ exc_type: 'ServiceUnavailable', _server_messages: '[]' }),
    });
  });
  await page.goto(TAXPAYER + '/taxpayer/requests');
  const footer = page.locator('footer', { hasText: 'Vati Taxpayer Portal' });
  await expect(page.getByText('Loading your portal…')).toBeVisible();
  await expect(footer.getByRole('link', { name: 'Open-source notices' })).toBeVisible();
  await shot(page, 'taxpayer-loading-footer', testInfo.project.name);
  release();
  await expect(page.getByRole('heading', { name: 'Taxpayer portal', level: 1 })).toBeVisible();
  await expect(footer.getByRole('link', { name: 'Open-source notices' })).toBeVisible();
  expect(await findComponentNames(page)).toEqual([]);
  expect(await seriousAxeViolations(page)).toEqual([]);
  await shot(page, 'taxpayer-error-footer', testInfo.project.name);
});

test('auditor: a denied officer sees no component names', async ({ page }, testInfo) => {
  await mockAuditorAccess(page, 'denied');
  await page.goto(AUDITOR + '/auditor/cases');
  await page.waitForLoadState('networkidle');
  expect(await findComponentNames(page)).toEqual([]);
  await shot(page, 'auditor-denied', testInfo.project.name);
});
