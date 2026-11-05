import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';

// Synthetic IDs from integration/api/fixtures/auditor_queue.json.
const CASE_2 = '40000000-0000-4000-8000-000000000002';
const CASE_6 = '40000000-0000-4000-8000-000000000006';
const TAXPAYER_2 = '20000000-0000-4000-8000-000000000002';
const OTHER_TENANT = '10000000-0000-4000-8000-000000000002';
const QUEUE = /\/api\/v1\/cases\?/;
const ASSIGNED_ROWS = 8; // header + 7 cases assigned to the fixture officer

const nav = (page: Page) => page.getByRole('navigation', { name: 'Auditor workspace' });
const rows = (page: Page) => page.getByRole('row');

/** Send the request to the real fixture server, but for a tenant outside the principal's scope. */
const asOtherTenant = (route: Route) =>
  route.continue({ headers: { ...route.request().headers(), 'x-vati-tenant-id': OTHER_TENANT } });

test.describe('navigation', () => {
  test('overview, assigned queue, case, taxpayer profile and showcase', async ({ page }) => {
    await page.goto('/auditor/');
    await expect(page.getByRole('heading', { level: 1, name: 'Overview' })).toBeVisible();
    await expect(page.getByText('Fixture mode.')).toBeVisible();
    await expect(page.getByText('7 assigned to you')).toBeVisible();

    await nav(page).getByRole('link', { name: 'My case queue' }).click();
    await expect(page).toHaveURL(/\/auditor\/cases$/);
    await expect(page.getByRole('heading', { level: 1, name: 'My case queue' })).toBeVisible();
    await expect(nav(page).getByRole('link', { name: 'My case queue' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    await expect(rows(page)).toHaveCount(ASSIGNED_ROWS);
    // Cases assigned to another officer or to nobody are not in this queue.
    await expect(page.getByRole('link', { name: 'SYN-008' })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'SYN-009' })).toHaveCount(0);

    await page.getByRole('link', { name: 'SYN-002' }).click();
    await expect(page).toHaveURL(new RegExp(`/auditor/cases/${CASE_2}$`));
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(
      'SYN-002 · Synthetic Border Retail',
    );
    const reasons = page
      .getByRole('region', { name: 'Why this case was selected' })
      .or(page.locator('section[aria-labelledby="reasons-heading"]'));
    await expect(reasons.first()).toContainText('shares a director');
    await expect(reasons.first()).toContainText('several consecutive periods');
    await expect(page.getByRole('link', { name: 'Review and decide' })).toHaveAttribute(
      'href',
      `/auditor/cases/${CASE_2}/review`,
    );
    await page.getByRole('tab', { name: 'Findings' }).click();
    await expect(page.getByText('No synthetic service agreement was supplied')).toBeVisible();
    await page.getByRole('tab', { name: 'Evidence' }).click();
    await expect(page.getByRole('region', { name: 'Evidence on file' })).toBeVisible();
    await page.getByRole('tab', { name: 'Overview' }).click();
    await expect(page).toHaveURL(new RegExp(`/auditor/cases/${CASE_2}$`));

    await page.getByRole('link', { name: 'View taxpayer profile' }).click();
    await expect(page).toHaveURL(new RegExp(`/auditor/taxpayers/${TAXPAYER_2}$`));
    await expect(
      page.getByRole('heading', { level: 1, name: 'Synthetic Border Retail' }),
    ).toBeVisible();
    await expect(page.getByText('******4102')).toBeVisible();
    await expect(page.getByText('Retail trade')).toBeVisible();
    const open = page.locator('section[aria-labelledby="open-cases-heading"]');
    await expect(open.getByRole('link')).toHaveText(['SYN-002', 'SYN-008']);

    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`/auditor/cases/${CASE_2}$`));
    await nav(page).getByRole('link', { name: 'Component showcase' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Component showcase' })).toBeVisible();
  });

  test('overview status links open the queue filtered by status', async ({ page }) => {
    await page.goto('/auditor/');
    await page.getByRole('link', { name: /Awaiting officer review/ }).click();
    await expect(page).toHaveURL(/status=AWAITING_HUMAN/);
    await expect(rows(page)).toHaveCount(4);
  });

  test('refund lane filter from the URL and clearing filters', async ({ page }) => {
    await page.goto('/auditor/cases?lane=targeted');
    await expect(rows(page)).toHaveCount(3);
    await expect(page.getByRole('link', { name: 'SYN-002' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'SYN-004' })).toBeVisible();
    await page.getByRole('button', { name: 'Clear filters' }).click();
    await expect(rows(page)).toHaveCount(ASSIGNED_ROWS);
  });

  test('queue sorts amounts numerically and keeps the sort in the URL', async ({ page }) => {
    await page.goto('/auditor/cases');
    const header = page.getByRole('columnheader', { name: /Refund claimed/ });
    await header.getByRole('button').click();
    await expect(header).toHaveAttribute('aria-sort', 'ascending');
    await expect(rows(page).nth(1)).toContainText('SYN-003'); // LSL 0.00
    await expect(rows(page).last()).toContainText('SYN-006'); // no claim: always last
    await header.getByRole('button').click();
    await expect(header).toHaveAttribute('aria-sort', 'descending');
    await expect(rows(page).nth(1)).toContainText('SYN-002'); // LSL 25,000.00
    await expect(rows(page).last()).toContainText('SYN-006');
    await expect(page).toHaveURL(/sort=refund_claim&dir=desc|dir=desc.*sort=refund_claim/);
  });
});

test.describe('processing states', () => {
  test('failed processing is flagged on the overview, queue and case', async ({ page }) => {
    await page.goto('/auditor/');
    const alert = page.getByRole('alert').filter({ hasText: 'processing failed' });
    await expect(alert).toContainText('1 case needs attention');
    await alert.getByRole('link', { name: 'View failed cases' }).click();
    await expect(page).toHaveURL(/status=FAILED_PROCESSING/);
    await expect(rows(page)).toHaveCount(2);
    await expect(rows(page).nth(1)).toContainText('Document extraction (Docling) timed out');
    await expect(rows(page).nth(1)).toContainText('Not evaluated yet');

    await page.getByRole('link', { name: 'SYN-006' }).click();
    await expect(page).toHaveURL(new RegExp(`/auditor/cases/${CASE_6}$`));
    await expect(page.getByRole('alert').filter({ hasText: 'Processing failed' })).toContainText(
      'no findings until processing succeeds',
    );
    const services = page.getByRole('region', { name: 'Service processing table' });
    await expect(services).toContainText('Failed (upstream_timeout)');
    await expect(services).toContainText('Not created yet');
  });

  test('a case still validating shows what it is waiting for', async ({ page }) => {
    await page.goto('/auditor/cases?status=VALIDATING');
    await expect(rows(page).nth(1)).toContainText('Waiting for Case management (Jube)');
  });
});

test.describe('keyboard', () => {
  test('skip link, visible focus and keyboard sorting', async ({ page }) => {
    await page.goto('/auditor/cases');
    await expect(rows(page)).toHaveCount(ASSIGNED_ROWS);
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

  test('case and taxpayer links work from the keyboard', async ({ page }) => {
    await page.goto('/auditor/cases');
    await page.getByRole('link', { name: 'SYN-002' }).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(
      'SYN-002 · Synthetic Border Retail',
    );
    await page.getByRole('link', { name: 'View taxpayer profile' }).focus();
    await page.keyboard.press('Enter');
    await expect(
      page.getByRole('heading', { level: 1, name: 'Synthetic Border Retail' }),
    ).toBeVisible();
  });
});

test.describe('states', () => {
  test('loading then ready', async ({ page }) => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    await page.route(QUEUE, async (route) => {
      await gate;
      await route.continue();
    });
    await page.goto('/auditor/cases');
    await expect(page.getByRole('status').filter({ hasText: 'Loading your cases…' })).toBeVisible();
    release();
    await expect(rows(page)).toHaveCount(ASSIGNED_ROWS);
  });

  test('unavailable service shows a retryable error and recovers', async ({ page }) => {
    await page.route(QUEUE, (route) => route.abort('connectionrefused'));
    await page.goto('/auditor/cases');
    const alert = page
      .getByRole('alert')
      .filter({ hasText: 'Case data is temporarily unavailable' });
    await expect(alert).toBeVisible();
    await expect(alert).toContainText('Reference:');
    await page.unroute(QUEUE);
    await alert.getByRole('button', { name: 'Try again' }).click();
    await expect(rows(page)).toHaveCount(ASSIGNED_ROWS);
  });

  test('non-synthetic data is refused rather than displayed', async ({ page }) => {
    await page.route(QUEUE, async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.data[0].synthetic = false;
      body.data[0].taxpayer.display_name = 'Real Taxpayer';
      await route.fulfill({ response, json: body });
    });
    await page.goto('/auditor/cases');
    await expect(
      page.getByRole('alert').filter({ hasText: 'Data could not be shown safely' }),
    ).toBeVisible();
    await expect(page.getByText('Real Taxpayer')).toHaveCount(0);
  });

  test('empty queue and empty filter results', async ({ page }) => {
    await page.route(QUEUE, (route) =>
      route.fulfill({ json: { data: [], meta: { next_cursor: null, has_more: false } } }),
    );
    await page.goto('/auditor/cases');
    await expect(page.getByText('No cases are assigned to you')).toBeVisible();
    await page.goto('/auditor/');
    await expect(page.getByText('No cases are assigned to you')).toBeVisible();
    await page.unroute(QUEUE);

    await page.goto('/auditor/cases?status=RESOLVED');
    await expect(page.getByText('No cases match these filters')).toBeVisible();
    await page.getByRole('status').getByRole('button', { name: 'Clear filters' }).click();
    await expect(rows(page)).toHaveCount(ASSIGNED_ROWS);
  });

  test('access outside the tenant scope is denied by the API and shown without data', async ({
    page,
  }) => {
    await page.route(/\/api\/v1\//, asOtherTenant);
    for (const path of [
      '/auditor/cases',
      `/auditor/cases/${CASE_2}`,
      `/auditor/taxpayers/${TAXPAYER_2}`,
    ]) {
      await page.goto(path);
      const alert = page.getByRole('alert').filter({ hasText: 'You do not have access' });
      await expect(alert, path).toBeVisible();
      await expect(alert).toContainText('Reference:');
      await expect(alert.getByRole('button', { name: 'Try again' })).toHaveCount(0);
      await expect(page.getByText('Synthetic Border Retail')).toHaveCount(0);
    }
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

  test('unknown or malformed case and taxpayer links, and unknown pages', async ({ page }) => {
    await page.goto('/auditor/cases/40000000-0000-4000-8000-000000000099');
    await expect(page.getByText('Case not found')).toBeVisible();
    await page.goto('/auditor/cases/SYN-001');
    await expect(page.getByText('Case not found')).toBeVisible();
    await page.goto('/auditor/taxpayers/20000000-0000-4000-8000-000000000099');
    await expect(page.getByText('Taxpayer not found')).toBeVisible();
    await page.goto('/auditor/no-such-page');
    await expect(page.getByRole('heading', { level: 1, name: 'Page not found' })).toBeVisible();
  });
});

const PAGES = [
  '/auditor/',
  '/auditor/cases',
  `/auditor/cases/${CASE_2}`,
  `/auditor/cases/${CASE_6}`,
  `/auditor/taxpayers/${TAXPAYER_2}`,
  '/auditor/showcase',
];

test.describe('accessibility', () => {
  for (const path of PAGES) {
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

  test('field errors meet 4.5:1 text contrast (TAX-104)', async ({ page }) => {
    // frappe-ui's own field error red (text-ink-red-5) measured 4.45:1 on white;
    // apps/auditor-web/src/style.css darkens it.
    await page.goto('/auditor/showcase');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await page.getByRole('button', { name: 'Validate example' }).click();
    await expect(page.getByText('Enter a case reference in the format SYN-001.')).toBeVisible();
    const results = await new AxeBuilder({ page })
      .include("[data-slot='error']")
      .withRules(['color-contrast'])
      .analyze();
    expect(results.violations.flatMap((v) => v.nodes.map((n) => n.target.join(' ')))).toEqual([]);
    // The three showcase field errors were actually measured, not skipped.
    expect(results.passes.flatMap((v) => v.nodes)).toHaveLength(3);
  });

  test('reflows at 320px (400% zoom equivalent) without page-wide horizontal scroll', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 320, height: 640 });
    for (const path of PAGES) {
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await page.waitForLoadState('networkidle');
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, path).toBeLessThanOrEqual(0);
      // The app shell clips overflow, so also check that no content block extends past the
      // viewport. Wide tables scroll inside their own labelled region and are excluded.
      const clipped = await page.evaluate(() =>
        [...document.querySelectorAll('main h1, main section, main [role="alert"], main dl')]
          .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1)
          .map((el) => el.tagName.toLowerCase() + (el.id ? `#${el.id}` : '')),
      );
      expect(clipped, path).toEqual([]);
    }
  });
});
