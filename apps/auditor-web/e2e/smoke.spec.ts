import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

const nav = (page: Page) => page.getByRole('navigation', { name: 'Auditor workspace' });

test.describe('navigation', () => {
  test('sidebar moves between overview, queue, case detail and showcase', async ({ page }) => {
    await page.goto('/auditor/');
    await expect(page.getByRole('heading', { level: 1, name: 'Overview' })).toBeVisible();
    await expect(page.getByText('Fixture mode.')).toBeVisible();
    await expect(page.getByText('5 synthetic cases in the fixture set')).toBeVisible();

    await nav(page).getByRole('link', { name: 'Case queue' }).click();
    await expect(page).toHaveURL(/\/auditor\/cases$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Case queue' })).toBeVisible();
    await expect(nav(page).getByRole('link', { name: 'Case queue' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    await expect(page.getByRole('row')).toHaveCount(6);

    await page.getByRole('link', { name: 'SYN-002' }).click();
    await expect(page).toHaveURL(/\/auditor\/cases\/SYN-002$/);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(
      'SYN-002 · Synthetic Border Retail',
    );
    await expect(page.getByRole('button', { name: 'Request evidence' })).toBeDisabled();
    await page.getByRole('tab', { name: 'Evidence' }).click();
    await expect(page.getByText('Evidence are not available in fixture mode')).toBeVisible();

    await page.goBack();
    await expect(page).toHaveURL(/\/auditor\/cases$/);
    await nav(page).getByRole('link', { name: 'Component showcase' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Component showcase' })).toBeVisible();
  });

  test('overview lane links open the queue filtered by that lane', async ({ page }) => {
    await page.goto('/auditor/');
    await page.getByRole('link', { name: /Targeted evidence request/ }).click();
    await expect(page).toHaveURL(/lane=targeted_evidence_request/);
    await expect(page.getByRole('row')).toHaveCount(3);
  });

  test('queue sorts numerically and keeps the sort in the URL', async ({ page }) => {
    await page.goto('/auditor/cases');
    const header = page.getByRole('columnheader', { name: /Refund claimed/ });
    await header.getByRole('button').click();
    await expect(header).toHaveAttribute('aria-sort', 'ascending');
    await expect(page.getByRole('row').nth(1)).toContainText('SYN-003'); // LSL 0.00
    await header.getByRole('button').click();
    await expect(header).toHaveAttribute('aria-sort', 'descending');
    await expect(page.getByRole('row').nth(1)).toContainText('SYN-002'); // LSL 25,000.00
    await expect(page).toHaveURL(/sort=refund_claim&dir=desc|dir=desc.*sort=refund_claim/);
  });
});

test.describe('keyboard', () => {
  test('skip link, visible focus and keyboard sorting', async ({ page }) => {
    await page.goto('/auditor/cases');
    await expect(page.getByRole('row')).toHaveCount(6);
    await page.keyboard.press('Tab');
    const skip = page.getByRole('link', { name: 'Skip to main content' });
    await expect(skip).toBeFocused();
    await expect(skip).toBeInViewport();
    await page.keyboard.press('Enter');
    await expect(page.locator('#main-content')).toBeFocused();

    // Tab forward until the first sortable column header is focused.
    const caseSort = page.getByRole('columnheader', { name: 'Case' }).getByRole('button');
    for (
      let i = 0;
      i < 25 && !(await caseSort.evaluate((el) => el === document.activeElement));
      i++
    ) {
      await page.keyboard.press('Tab');
    }
    await expect(caseSort).toBeFocused();
    const outline = await caseSort.evaluate((el) => getComputedStyle(el).outlineStyle);
    expect(outline).not.toBe('none');
    await page.keyboard.press('Enter');
    await expect(page.getByRole('columnheader', { name: 'Case' })).toHaveAttribute(
      'aria-sort',
      'descending',
    );
  });

  test('sidebar links are reachable and activated by keyboard', async ({ page }) => {
    await page.goto('/auditor/');
    const link = nav(page).getByRole('link', { name: 'Component showcase' });
    await link.focus();
    await expect(link).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { level: 1, name: 'Component showcase' })).toBeVisible();
  });
});

test.describe('states', () => {
  test('loading then ready', async ({ page }) => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    await page.route('**/api/cases', async (route) => {
      await gate;
      await route.continue();
    });
    await page.goto('/auditor/cases');
    await expect(page.getByRole('status').filter({ hasText: 'Loading cases…' })).toBeVisible();
    release();
    await expect(page.getByRole('row')).toHaveCount(6);
  });

  test('unavailable service shows a retryable error and recovers', async ({ page }) => {
    await page.route('**/api/cases', (route) => route.abort('connectionrefused'));
    await page.goto('/auditor/cases');
    const alert = page
      .getByRole('alert')
      .filter({ hasText: 'Case data is temporarily unavailable' });
    await expect(alert).toBeVisible();
    await page.unroute('**/api/cases');
    await alert.getByRole('button', { name: 'Try again' }).click();
    await expect(page.getByRole('row')).toHaveCount(6);
  });

  test('non-fixture data is refused rather than displayed', async ({ page }) => {
    await page.route('**/api/cases', (route) =>
      route.fulfill({ json: { mode: 'live', items: [{ id: 'X', name: 'Real Taxpayer' }] } }),
    );
    await page.goto('/auditor/cases');
    await expect(
      page.getByRole('alert').filter({ hasText: 'Data could not be shown safely' }),
    ).toBeVisible();
    await expect(page.getByText('Real Taxpayer')).toHaveCount(0);
  });

  test('empty queue and empty filter results', async ({ page }) => {
    await page.route('**/api/cases', (route) =>
      route.fulfill({ json: { mode: 'fixture', synthetic: true, items: [] } }),
    );
    await page.goto('/auditor/cases');
    await expect(page.getByText('No cases waiting for review')).toBeVisible();
    await page.unroute('**/api/cases');
    await page.goto('/auditor/cases?q=no-such-taxpayer');
    await expect(page.getByText('No cases match these filters')).toBeVisible();
    await page.getByRole('status').getByRole('button', { name: 'Clear filters' }).click();
    await expect(page.getByRole('row')).toHaveCount(6);
  });

  test('denied access to a case', async ({ page }) => {
    await page.route('**/api/cases/SYN-001', (route) =>
      route.fulfill({ status: 403, json: { error: 'forbidden' } }),
    );
    await page.goto('/auditor/cases/SYN-001');
    await expect(
      page.getByRole('alert').filter({ hasText: 'You do not have access' }),
    ).toBeVisible();
    await expect(page.getByText('Synthetic Mountain Wholesale')).toHaveCount(0);
  });

  test('a page chunk that fails to load shows a recoverable error', async ({ page }) => {
    await page.goto('/auditor/');
    await expect(page.getByRole('heading', { level: 1, name: 'Overview' })).toBeVisible();
    await page.route('**/assets/ShowcaseView-*.js', (route) => route.abort('connectionreset'));
    await nav(page).getByRole('link', { name: 'Component showcase' }).click();
    const alert = page.getByRole('alert').filter({ hasText: 'This page could not be loaded' });
    await expect(alert).toBeVisible();
    await page.unroute('**/assets/ShowcaseView-*.js');
    await alert.getByRole('button', { name: 'Reload page' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Component showcase' })).toBeVisible();
  });

  test('unknown case and unknown page', async ({ page }) => {
    await page.goto('/auditor/cases/SYN-999');
    await expect(page.getByText('Case not found')).toBeVisible();
    await page.goto('/auditor/no-such-page');
    await expect(page.getByRole('heading', { level: 1, name: 'Page not found' })).toBeVisible();
  });
});

test.describe('accessibility', () => {
  for (const path of [
    '/auditor/',
    '/auditor/cases',
    '/auditor/cases/SYN-002',
    '/auditor/showcase',
  ]) {
    test(`no serious or critical axe violations on ${path}`, async ({ page }) => {
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await page.waitForLoadState('networkidle');
      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
        .analyze();
      const serious = results.violations.filter(
        (v) => v.impact === 'serious' || v.impact === 'critical',
      );
      expect(
        serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`),
      ).toEqual([]);
    });
  }

  test('reflows at 320px (400% zoom equivalent) without page-wide horizontal scroll', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 320, height: 640 });
    for (const path of [
      '/auditor/',
      '/auditor/cases',
      '/auditor/cases/SYN-002',
      '/auditor/showcase',
    ]) {
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, path).toBeLessThanOrEqual(0);
    }
  });
});
