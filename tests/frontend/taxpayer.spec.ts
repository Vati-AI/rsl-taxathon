import { expect, test, type Page } from '@playwright/test';
import {
  hasVisibleFocus,
  horizontalOverflow,
  pageReady,
  seriousAxeViolations,
  tabUntilFocused,
} from './support/checks';
import { TAXPAYER } from './support/env';
import { isMobile, TAXPAYER_ROUTES } from './support/matrix';
import { mockTaxpayerSession, PORTAL_SUBMISSIONS, SYNTHETIC_TAXPAYER } from './support/sessions';

// The desktop sidebar and the mobile bottom bar share this accessible name.
const nav = (page: Page) => page.getByRole('navigation', { name: 'Taxpayer portal' });
const newLabel = (project: string) => (isMobile(project) ? 'New' : 'New submission');

test.describe('taxpayer portal', () => {
  test('positive navigation: list → status → breadcrumb → new submission → account', async ({
    page,
  }, testInfo) => {
    await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(TAXPAYER + '/taxpayer/');
    await expect(page).toHaveURL(/\/taxpayer\/submissions$/);
    await pageReady(page, 'Your submissions');
    await expect(page.getByText('Synthetic fixture data')).toBeVisible();
    await expect(page.getByRole('list', { name: 'Submissions' }).getByRole('listitem')).toHaveCount(
      PORTAL_SUBMISSIONS.length,
    );

    await page.getByRole('link', { name: /VAT-2026-08-000921/ }).click();
    await expect(page).toHaveURL(/\/taxpayer\/submissions\/SUB-SYN-0002$/);
    await pageReady(page, /^VAT return · /);
    await expect(page.getByText('What happens next')).toBeVisible();

    await page.getByRole('link', { name: 'Submissions', exact: true }).first().click();
    await expect(page).toHaveURL(/\/taxpayer\/submissions$/);
    await pageReady(page, 'Your submissions');

    await nav(page)
      .getByRole('link', { name: newLabel(testInfo.project.name) })
      .click();
    await expect(page).toHaveURL(/\/taxpayer\/submissions\/new$/);
    await pageReady(page, 'New submission');

    await nav(page).getByRole('link', { name: 'Account' }).click();
    await pageReady(page, 'Account');
    await expect(page.getByText(SYNTHETIC_TAXPAYER.user)).toBeVisible();
  });

  test('denied access: a guest is sent to login with a portal-only return path', async ({
    page,
  }) => {
    const mock = await mockTaxpayerSession(page, 'guest');
    await page.goto(TAXPAYER + '/taxpayer/submissions/SUB-SYN-0002');
    await expect(page).toHaveURL(/\/login\?redirect-to=%2Ftaxpayer%2Fsubmissions%2FSUB-SYN-0002$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Login' })).toBeVisible();
    // Only the session check ran: no submission data was requested for a guest.
    expect(mock.calls).toEqual(['vati_tax.portal.api.get_session']);
  });

  test('denied access: a signed-in user refused by the API sees no submissions', async ({
    page,
  }) => {
    await mockTaxpayerSession(page, 'denied');
    await page.goto(TAXPAYER + '/taxpayer/submissions');
    await pageReady(page, 'Your submissions');
    await expect(page.getByText('Access denied')).toBeVisible();
    await expect(page.getByText('You do not have access to this information.')).toBeVisible();
    await expect(page.getByText(/VAT-2026-|PAYE-2026-/)).toHaveCount(0);
  });

  test('logout ends the session and a later visit goes to login', async ({ page }) => {
    const mock = await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(TAXPAYER + '/taxpayer/account');
    await pageReady(page, 'Account');
    await page.getByRole('main').getByRole('button', { name: 'Log out' }).click();
    await expect(page).toHaveURL(/\/login$/);
    expect(mock.calls).toContain('logout');
    await page.goto(TAXPAYER + '/taxpayer/account');
    await expect(page).toHaveURL(/\/login\?redirect-to=%2Ftaxpayer%2Faccount$/);
  });

  test('keyboard: skip link and navigation activation', async ({ page }) => {
    await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(TAXPAYER + '/taxpayer/submissions');
    await pageReady(page, 'Your submissions');

    await page.keyboard.press('Tab');
    const skip = page.getByRole('link', { name: 'Skip to main content' });
    await expect(skip).toBeFocused();
    await expect(skip).toBeInViewport();
    await page.keyboard.press('Enter');
    // <main> is not itself focusable here; the skip link moves the sequential
    // focus start, so the next Tab must land inside the main region.
    await page.keyboard.press('Tab');
    expect(await page.evaluate(() => Boolean(document.activeElement?.closest('main#main')))).toBe(
      true,
    );

    const account = nav(page).getByRole('link', { name: 'Account' });
    await tabUntilFocused(page, account, 40);
    expect(await hasVisibleFocus(account)).toBe(true);
    await page.keyboard.press('Enter');
    await pageReady(page, 'Account');
  });

  test('keyboard: an incomplete form keeps values and focuses the first invalid field', async ({
    page,
  }) => {
    await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(TAXPAYER + '/taxpayer/submissions/new');
    await pageReady(page, 'New submission');

    const period = page.getByLabel('Tax period');
    await period.fill('2026-09');
    await period.press('Enter');

    await expect(
      page.getByRole('alert').filter({ hasText: 'Fix 3 fields to continue' }),
    ).toBeVisible();
    const type = page.getByLabel('Type of return or claim');
    await expect(type).toBeFocused();
    await expect(type).toHaveAttribute('aria-invalid', 'true');
    await expect(period).toHaveValue('2026-09');
  });

  for (const route of TAXPAYER_ROUTES) {
    test(`matrix ${route.name}: no serious axe violations, no page-wide horizontal scroll`, async ({
      page,
    }) => {
      await mockTaxpayerSession(page, 'taxpayer');
      await page.goto(TAXPAYER + route.path);
      await pageReady(page, route.heading);
      expect(await seriousAxeViolations(page)).toEqual([]);
      expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
    });
  }
});
