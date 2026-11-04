import { expect, test, type Page } from '@playwright/test';
import {
  hasVisibleFocus,
  horizontalOverflow,
  pageReady,
  seriousAxeViolations,
  tabUntilFocused,
} from './support/checks';
import { AUDITOR } from './support/env';
import { AUDITOR_ROUTES } from './support/matrix';
import { mockAuditorAccess } from './support/sessions';

const nav = (page: Page) => page.getByRole('navigation', { name: 'Auditor workspace' });

test.describe('auditor workspace', () => {
  test('positive navigation: overview → queue → case → breadcrumb back', async ({ page }) => {
    await mockAuditorAccess(page, 'officer');
    await page.goto(AUDITOR + '/auditor/');
    await pageReady(page, 'Overview');
    await expect(page.getByText('Fixture mode.')).toBeVisible();

    await nav(page).getByRole('link', { name: 'Case queue' }).click();
    await expect(page).toHaveURL(/\/auditor\/cases$/);
    await pageReady(page, 'Case queue');
    await expect(nav(page).getByRole('link', { name: 'Case queue' })).toHaveAttribute(
      'aria-current',
      'page',
    );

    await page.getByRole('link', { name: 'SYN-002' }).click();
    await expect(page).toHaveURL(/\/auditor\/cases\/SYN-002$/);
    await pageReady(page, 'SYN-002 · Synthetic Border Retail');

    await page
      .getByRole('navigation', { name: /breadcrumb/i })
      .getByRole('link', {
        name: 'Case queue',
      })
      .click();
    await expect(page).toHaveURL(/\/auditor\/cases$/);
    await pageReady(page, 'Case queue');
  });

  test('denied access: a refused case shows no case data', async ({ page }) => {
    await mockAuditorAccess(page, 'denied');
    await page.goto(AUDITOR + '/auditor/cases/SYN-001');
    await expect(
      page.getByRole('alert').filter({ hasText: 'You do not have access' }),
    ).toBeVisible();
    await expect(page.getByText('Synthetic Mountain Wholesale')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Request evidence' })).toHaveCount(0);
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

    const link = nav(page).getByRole('link', { name: 'Case queue' });
    await page.keyboard.press('Shift+Tab');
    await tabUntilFocused(page, link);
    expect(await hasVisibleFocus(link)).toBe(true);
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/auditor\/cases$/);
    await pageReady(page, 'Case queue');
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
