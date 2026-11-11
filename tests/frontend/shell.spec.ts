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
import { measurePageLength } from './support/layout';
import { AUDITOR_ROUTES, isMobile, TAXPAYER_ROUTES } from './support/matrix';
import { mockAuditorAccess, mockTaxpayerSession } from './support/sessions';
import { sidebar } from './support/shell';

// TAX-146: the Vati Workspace app shell around both apps (docs/DESIGN_STANDARD.md v2).
// The auditor shell builds its tabs and sidebar from GET /api/v1/session (fixture server),
// so navigation follows the server-side scope of the X-Vati-Fixture-Role officer.
// CAPTURE_SCREENSHOTS=1 writes review screenshots to docs/evidence/TAX-146.

const SESSION = /\/api\/v1\/session$/;

async function shot(page: Page, name: string, project: string) {
  if (!process.env.CAPTURE_SCREENSHOTS) return;
  const file = path.resolve(
    __dirname,
    `../../docs/evidence/TAX-146/${name}-${project.split('-')[1]}.png`,
  );
  await page.screenshot({ path: file });
}

function asRole(page: Page, role: 'auditor' | 'supervisor') {
  return page.addInitScript((r) => localStorage.setItem('vati.fixtureRole', r), role);
}

const appTabs = (page: Page) =>
  page.getByRole('navigation', { name: 'Workspace apps' }).filter({ visible: true });

test.describe('auditor shell (TAX-146)', () => {
  test('every route renders inside the shell, content left-aligned up to 1280px', async ({
    page,
  }, testInfo) => {
    const mobile = isMobile(testInfo.project.name);
    for (const route of AUDITOR_ROUTES) {
      await page.goto(AUDITOR + route.path);
      await pageReady(page, route.heading);
      await expect(page.locator('[data-kit="workspace-shell"]'), route.path).toBeVisible();
      // The first header is the shell's (the showcase also shows an example header).
      await expect(page.locator('[data-kit="workspace-header"]').first()).toBeVisible();
      const box = await page.locator('#main-content > div').boundingBox();
      expect(box!.width, route.path).toBeLessThanOrEqual(1280);
      if (!mobile) {
        const side = await page
          .locator('[data-kit="app-sidebar"]')
          .filter({ visible: true })
          .first() // the shell's; the showcase also shows example sidebars
          .boundingBox();
        expect(Math.round(box!.x), `${route.path} sits beside the sidebar`).toBe(
          Math.round(side!.x + side!.width),
        );
      }
    }
  });

  test('tabs and sidebar follow the server scope; unconnected tools say so', async ({
    page,
  }, testInfo) => {
    await page.goto(AUDITOR + '/auditor/');
    await pageReady(page, 'Overview');
    const nav = await sidebar(page, 'Auditor workspace');
    await expect(nav.getByRole('link', { name: 'My case queue' })).toBeVisible();
    await expect(nav.getByRole('link', { name: 'Management dashboard' })).toHaveCount(0);
    await expect(nav.getByRole('link', { name: 'Component showcase' })).toHaveCount(0);
    const tabs = isMobile(testInfo.project.name)
      ? page.getByRole('dialog').getByRole('navigation', { name: 'Workspace apps' })
      : appTabs(page);
    await expect(tabs.getByRole('link')).toHaveText([/^Cases/, /^Investigation/, /^Explore/]);
    await expect(tabs.getByRole('link', { name: /^Investigation/ })).toContainText(
      /not connected/i,
    );
    await shot(page, 'auditor-overview', testInfo.project.name);
    await tabs.getByRole('link', { name: /^Investigation/ }).click();
    await pageReady(page, 'Investigation');
    await expect(
      page.getByRole('heading', { name: 'Investigation is not connected yet' }),
    ).toBeVisible();
    expect(await seriousAxeViolations(page)).toEqual([]);
    await shot(page, 'auditor-not-connected', testInfo.project.name);
  });

  test('a supervisor also gets Reports and the supervision views', async ({ page }, testInfo) => {
    await asRole(page, 'supervisor');
    await page.goto(AUDITOR + '/auditor/management');
    await pageReady(page, 'Management dashboard');
    const nav = await sidebar(page, 'Auditor workspace');
    await expect(nav.getByRole('link', { name: 'Management dashboard' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    await expect(nav.getByRole('link', { name: 'Integrity review' })).toBeVisible();
    await page.keyboard.press('Escape');
    if (!isMobile(testInfo.project.name))
      await expect(appTabs(page).getByRole('link', { name: 'Reports' })).toHaveAttribute(
        'aria-current',
        'page',
      );
    await shot(page, 'supervisor-management', testInfo.project.name);
  });

  test('case context offers "Back to cases"', async ({ page }) => {
    await page.goto(AUDITOR + AUDITOR_ROUTES.find((r) => r.name === 'case-detail')!.path);
    await pageReady(page, /^SYN-002 · /);
    const nav = await sidebar(page, 'Auditor workspace');
    await nav.getByRole('link', { name: 'Back to cases' }).click();
    await pageReady(page, 'My case queue');
  });

  test('the sidebar collapses to an icon rail and remembers it', async ({ page }, testInfo) => {
    test.skip(isMobile(testInfo.project.name), 'Below 1024px the sidebar is a drawer');
    await page.goto(AUDITOR + '/auditor/cases');
    await pageReady(page, 'My case queue');
    const rail = page.locator('[data-kit="app-sidebar"]').filter({ visible: true });
    await page.getByRole('button', { name: 'Collapse navigation' }).click();
    await expect(rail).toHaveAttribute('data-collapsed', 'true');
    await expect.poll(async () => (await rail.boundingBox())!.width).toBe(56);
    // Item names stay available to screen readers in the rail.
    await expect(rail.getByRole('link', { name: 'My case queue' })).toBeVisible();
    await shot(page, 'auditor-rail', testInfo.project.name);
    await page.reload();
    await pageReady(page, 'My case queue');
    await expect(rail).toHaveAttribute('data-collapsed', 'true');
    await page.getByRole('button', { name: 'Expand navigation' }).click();
    await expect(rail).toHaveAttribute('data-collapsed', 'false');
  });

  test('below 1024px the navigation is an accessible drawer', async ({ page }, testInfo) => {
    test.skip(!isMobile(testInfo.project.name), 'The drawer replaces the sidebar below 1024px');
    await page.goto(AUDITOR + '/auditor/');
    await pageReady(page, 'Overview');
    const menu = page.getByRole('button', { name: 'Menu', exact: true });
    await menu.click();
    const drawer = page.getByRole('dialog', { name: 'Vati Workspace' });
    await expect(drawer).toBeVisible();
    // While the drawer is open the page behind it is hidden from assistive technology.
    await expect(page.locator('button[aria-controls^="vati-nav-drawer"]')).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    expect(await seriousAxeViolations(page)).toEqual([]);
    await shot(page, 'auditor-drawer', testInfo.project.name);
    for (let i = 0; i < 8; i += 1) {
      await page.keyboard.press('Tab');
      expect(await drawer.evaluate((el) => el.contains(document.activeElement))).toBe(true);
    }
    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden();
    await expect(menu).toBeFocused();
    await menu.click();
    await drawer.getByRole('link', { name: 'My case queue' }).click();
    await pageReady(page, 'My case queue');
    await expect(drawer).toBeHidden();
  });

  test('the command palette finds pages, cases and masked TINs by keyboard alone', async ({
    page,
  }, testInfo) => {
    await page.goto(AUDITOR + '/auditor/');
    await pageReady(page, 'Overview');
    await page.keyboard.press('Control+k');
    const box = page.getByRole('combobox', { name: 'Search case reference, TIN or page' });
    await expect(box).toBeFocused();
    await box.fill('4102');
    await expect(page.getByRole('option', { name: /Synthetic Border Retail/ })).toBeVisible();
    expect(await seriousAxeViolations(page)).toEqual([]);
    await shot(page, 'auditor-palette', testInfo.project.name);
    await page.keyboard.press('Enter');
    await pageReady(page, 'Synthetic Border Retail');

    await page.keyboard.press('Control+k');
    await box.fill('SYN-004');
    await page.keyboard.press('Enter');
    await pageReady(page, /^SYN-004 · /);

    // Pages the officer may not open are not offered.
    await page.keyboard.press('Control+k');
    await box.fill('Management');
    await expect(page.getByRole('option')).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(box).toBeHidden();
  });

  test('the design reference is reached from Help, not the navigation', async ({ page }) => {
    await page.goto(AUDITOR + '/auditor/');
    await pageReady(page, 'Overview');
    const help = page.getByRole('button', { name: 'Help' }).filter({ visible: true });
    test.skip((await help.count()) === 0, 'Help leaves the header below 640px');
    await help.click();
    await page.getByRole('link', { name: 'Design reference (component showcase)' }).click();
    await pageReady(page, 'Component showcase');
  });

  test('loading, error with retry and denied states', async ({ page }, testInfo) => {
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route(SESSION, async (route) => {
      await held;
      await route.continue();
    });
    await page.goto(AUDITOR + '/auditor/');
    // The sidebar skeleton (from 1024px; below that the navigation is in the drawer).
    await expect(page.locator('[data-kit="skeleton"]').first()).toBeAttached();
    await shot(page, 'auditor-loading', testInfo.project.name);
    release();
    await pageReady(page, 'Overview');
    await page.unroute(SESSION);

    let fail = true;
    await page.route(SESSION, (route) =>
      fail
        ? route.fulfill({
            status: 503,
            json: {
              error: {
                code: 'dependency_unavailable',
                message: 'x',
                retryable: true,
                correlation_id: '00000000-0000-4000-8000-000000000000',
              },
            },
          })
        : route.continue(),
    );
    await page.reload();
    const error = page
      .getByRole('alert')
      .filter({ hasText: 'Your workspace navigation could not be loaded' });
    await expect(error).toBeVisible();
    expect(await seriousAxeViolations(page)).toEqual([]);
    await shot(page, 'auditor-error', testInfo.project.name);
    fail = false;
    await error.getByRole('button', { name: 'Try again' }).click();
    await expect(error).toBeHidden();
    await page.unroute(SESSION);

    await mockAuditorAccess(page, 'denied');
    await page.goto(AUDITOR + '/auditor/cases');
    await expect(
      page.getByRole('alert').filter({ hasText: 'You do not have access' }),
    ).toBeVisible();
    // Another tenant's scope gives no navigation at all.
    await expect(page.locator('[data-kit="app-sidebar"] a')).toHaveCount(0);
    await shot(page, 'auditor-denied', testInfo.project.name);
  });
});

test.describe('taxpayer shell (TAX-146)', () => {
  test('every route renders inside the simpler shell without app tabs or search', async ({
    page,
  }) => {
    await mockTaxpayerSession(page, 'taxpayer');
    for (const route of TAXPAYER_ROUTES) {
      await page.goto(TAXPAYER + route.path);
      await pageReady(page, route.heading);
      await expect(page.locator('[data-kit="workspace-shell"]'), route.path).toBeVisible();
      await expect(page.locator('[data-kit="workspace-header"]')).toContainText(
        'Vati Taxpayer Portal',
      );
      await expect(page.getByRole('navigation', { name: 'Workspace apps' })).toHaveCount(0);
      await expect(page.getByRole('button', { name: /^Search/ })).toHaveCount(0);
      expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
    }
  });

  test('navigation, account menu and log out', async ({ page }, testInfo) => {
    const mock = await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(TAXPAYER + '/taxpayer/');
    await pageReady(page, 'Evidence requests');
    expect(await seriousAxeViolations(page)).toEqual([]);
    await shot(page, 'taxpayer-requests', testInfo.project.name);
    const nav = await sidebar(page, 'Taxpayer portal');
    const link = nav.getByRole('link', { name: 'My cases' });
    await tabUntilFocused(page, link, 40);
    expect(await hasVisibleFocus(link)).toBe(true);
    await page.keyboard.press('Enter');
    await pageReady(page, 'My cases');
    await page.getByRole('button', { name: 'Synthetic Taxpayer A' }).click();
    const logout = page.getByRole('dialog').getByRole('button', { name: 'Log out' });
    await expect(logout).toBeVisible();
    await shot(page, 'taxpayer-account-menu', testInfo.project.name);
    await logout.click();
    await expect(page).toHaveURL(/\/login$/);
    expect(mock.calls).toContain('logout');
  });

  test('loading and denied states', async ({ page }, testInfo) => {
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await mockTaxpayerSession(page, 'taxpayer');
    await page.route('**/api/method/vati_tax.portal.api.get_session', async (route) => {
      await held;
      await route.fallback();
    });
    await page.goto(TAXPAYER + '/taxpayer/');
    await expect(page.getByText('Loading your portal…')).toBeVisible();
    await shot(page, 'taxpayer-loading', testInfo.project.name);
    release();
    await pageReady(page, 'Evidence requests');

    await mockTaxpayerSession(page, 'revoked');
    await page.goto(TAXPAYER + '/taxpayer/submissions');
    await pageReady(page, 'My cases');
    await expect(page.getByText('Access denied')).toBeVisible();
    await shot(page, 'taxpayer-denied', testInfo.project.name);
  });
});

test('page length at 1440×900 (TAX-144 budget)', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-1440', 'The budget is defined at 1440×900');
  const results: string[] = [];
  for (const route of AUDITOR_ROUTES.filter((r) =>
    ['overview', 'queue', 'taxpayer'].includes(r.name),
  )) {
    await page.goto(AUDITOR + route.path);
    await pageReady(page, route.heading);
    const length = await measurePageLength(page);
    expect(length.viewports, route.path).toBeLessThanOrEqual(1.5);
    results.push(`${route.name} ${length.viewports}`);
  }
  await mockTaxpayerSession(page, 'taxpayer');
  for (const route of TAXPAYER_ROUTES.filter((r) =>
    ['submissions', 'evidence-requests', 'account'].includes(r.name),
  )) {
    await page.goto(TAXPAYER + route.path);
    await pageReady(page, route.heading);
    const length = await measurePageLength(page);
    expect(length.viewports, route.path).toBeLessThanOrEqual(1.5);
    results.push(`taxpayer/${route.name} ${length.viewports}`);
  }
  console.log(`[TAX-146 page length] ${results.join(' · ')}`);
});
