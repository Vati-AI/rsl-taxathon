import { expect, test, type Page } from '@playwright/test';
import {
  hasVisibleFocus,
  horizontalOverflow,
  pageReady,
  seriousAxeViolations,
  tabUntilFocused,
} from './support/checks';
import { AUDITOR } from './support/env';
import { AUDITOR_ROUTES, auditorCase } from './support/matrix';
import { mockAuditorAccess } from './support/sessions';
import { sidebar } from './support/shell';

// The sidebar, or the Menu drawer below 1024px (TAX-146).
const nav = (page: Page) => sidebar(page, 'Auditor workspace');
const syn002 = auditorCase('SYN-002');

test.describe('auditor workspace', () => {
  test('positive navigation: overview → queue → case → taxpayer → breadcrumb back', async ({
    page,
  }) => {
    await mockAuditorAccess(page, 'officer');
    await page.goto(AUDITOR + '/auditor/');
    await pageReady(page, 'Overview');
    // TAX-146: fixture mode is a slim strip in the header.
    await expect(page.locator('[data-kit="fixture-strip"]')).toHaveText('Synthetic data');

    await (await nav(page)).getByRole('link', { name: /^My cases/ }).click();
    await expect(page).toHaveURL(/\/auditor\/cases$/);
    await pageReady(page, 'My case queue');
    await expect((await nav(page)).getByRole('link', { name: /^My cases/ })).toHaveAttribute(
      'aria-current',
      'page',
    );
    await page.keyboard.press('Escape'); // closes the Menu drawer below 1024px; no-op above

    await page.getByRole('link', { name: 'SYN-002' }).click();
    await expect(page).toHaveURL(new RegExp(`/auditor/cases/${syn002.case_id}$`));
    await pageReady(page, 'SYN-002 · Synthetic Border Retail');

    await page.getByRole('link', { name: 'View taxpayer profile' }).click();
    await expect(page).toHaveURL(new RegExp(`/auditor/taxpayers/${syn002.taxpayer_id}$`));
    await pageReady(page, 'Synthetic Border Retail');

    await page.goBack();
    await pageReady(page, 'SYN-002 · Synthetic Border Retail');
    await page
      .getByRole('navigation', { name: /breadcrumb/i })
      .getByRole('link', { name: 'My case queue' })
      .click();
    await expect(page).toHaveURL(/\/auditor\/cases$/);
    await pageReady(page, 'My case queue');
  });

  test('denied access: another tenant is refused by the API and shown without data', async ({
    page,
  }) => {
    await mockAuditorAccess(page, 'denied');
    for (const route of AUDITOR_ROUTES.filter((r) =>
      ['queue', 'case-detail', 'taxpayer'].includes(r.name),
    )) {
      await page.goto(AUDITOR + route.path);
      const alert = page.getByRole('alert').filter({ hasText: 'You do not have access' });
      await expect(alert, route.path).toBeVisible();
      await expect(page.getByText('Synthetic Border Retail')).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Request evidence' })).toHaveCount(0);
    }
  });

  test('keyboard: skip link, visible focus and sidebar activation', async ({ page }) => {
    await page.goto(AUDITOR + '/auditor/');
    await pageReady(page, 'Overview');

    await page.keyboard.press('Tab');
    const skip = page.getByRole('link', { name: 'Skip to main content' });
    await expect(skip).toBeFocused();
    await expect(skip).toBeInViewport();
    await page.keyboard.press('Enter');
    await expect(page.locator('#main-content')).toBeFocused();

    const link = (await nav(page)).getByRole('link', { name: /^My cases/ });
    await page.keyboard.press('Shift+Tab');
    await tabUntilFocused(page, link);
    expect(await hasVisibleFocus(link)).toBe(true);
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/auditor\/cases$/);
    await pageReady(page, 'My case queue');
  });

  for (const route of AUDITOR_ROUTES) {
    test(`matrix ${route.name}: no serious axe violations, no page-wide horizontal scroll`, async ({
      page,
    }) => {
      await page.goto(AUDITOR + route.path);
      await pageReady(page, route.heading);
      expect(await seriousAxeViolations(page)).toEqual([]);
      expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
    });
  }
});
