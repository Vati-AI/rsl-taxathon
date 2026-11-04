import path from 'node:path';
import { expect, test, type Page, type Route } from '@playwright/test';

// Review evidence for the reviewer's visual approval (docs/DESIGN_STANDARD.md). Not a
// baseline comparison: Jane captures approved baselines after acceptance.
// Run with CAPTURE_SCREENSHOTS=1; output goes to docs/evidence/TAX-040.
// baseline comparison: the frontend QA owner (Kananelo) captures approved baselines after acceptance.
// Run with CAPTURE_SCREENSHOTS=1; output goes to docs/evidence/TAX-020.
test.skip(
  !process.env.CAPTURE_SCREENSHOTS,
  'Set CAPTURE_SCREENSHOTS=1 to capture review screenshots',
);

const out = path.resolve(__dirname, '../../../docs/evidence/TAX-040');
const CASE_2 = '40000000-0000-4000-8000-000000000002';
const CASE_6 = '40000000-0000-4000-8000-000000000006';
const TAXPAYER_2 = '20000000-0000-4000-8000-000000000002';
const QUEUE = /\/api\/v1\/cases\?/;
const widths = [1440, 1024, 390];
const pages = [
  { name: 'overview', path: '/auditor/' },
  { name: 'queue', path: '/auditor/cases' },
  { name: 'case-detail', path: `/auditor/cases/${CASE_2}` },
  { name: 'case-failed-processing', path: `/auditor/cases/${CASE_6}` },
  { name: 'taxpayer-profile', path: `/auditor/taxpayers/${TAXPAYER_2}` },
];

async function ready(page: Page) {
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await page.waitForLoadState('networkidle');
}

const asOtherTenant = (route: Route) =>
  route.continue({
    headers: {
      ...route.request().headers(),
      'x-vati-tenant-id': '10000000-0000-4000-8000-000000000002',
    },
  });

for (const width of widths) {
  test(`screens at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    for (const item of pages) {
      await page.goto(item.path);
      await ready(page);
      await page.screenshot({ path: `${out}/${item.name}-${width}.png`, fullPage: true });
    }
  });
}

for (const width of [1440, 390]) {
  test(`states at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const shot = (name: string) => page.screenshot({ path: `${out}/state-${name}-${width}.png` });

    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    await page.route(QUEUE, async (route) => {
      await gate;
      await route.continue();
    });
    await page.goto('/auditor/cases');
    await expect(page.getByText('Loading your cases…').first()).toBeVisible();
    await shot('loading');
    release();
    await expect(page.getByRole('row')).toHaveCount(8);
    await page.unroute(QUEUE);

    await page.route(QUEUE, (route) =>
      route.fulfill({ json: { data: [], meta: { next_cursor: null, has_more: false } } }),
    );
    await page.goto('/auditor/cases');
    await expect(page.getByText('No cases are assigned to you')).toBeVisible();
    await shot('empty-queue');
    await page.unroute(QUEUE);

    await page.goto('/auditor/cases?status=RESOLVED');
    await expect(page.getByText('No cases match these filters')).toBeVisible();
    await shot('no-matches');

    await page.goto('/auditor/cases?status=FAILED_PROCESSING');
    await expect(page.getByRole('row')).toHaveCount(2);
    await shot('queue-failed-filter');

    await page.route(QUEUE, (route) => route.abort('connectionrefused'));
    await page.goto('/auditor/cases');
    await expect(page.getByText('Case data is temporarily unavailable')).toBeVisible();
    await shot('unavailable');
    await page.unroute(QUEUE);

    await page.route(/\/api\/v1\//, asOtherTenant);
    await page.goto('/auditor/cases');
    await expect(page.getByText('You do not have access')).toBeVisible();
    await shot('denied');
    await page.unroute(/\/api\/v1\//);

    await page.goto('/auditor/taxpayers/20000000-0000-4000-8000-000000000099');
    await expect(page.getByText('Taxpayer not found')).toBeVisible();
    await shot('taxpayer-not-found');
  });
}

test('interaction states at 1440px', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/auditor/cases');
  await ready(page);
  await page.getByRole('columnheader', { name: 'Case' }).getByRole('button').focus();
  await page.screenshot({ path: `${out}/focus-table-sort-1440.png` });
  await page.getByRole('link', { name: 'SYN-002' }).focus();
  await page.screenshot({ path: `${out}/focus-case-link-1440.png` });
  await page
    .getByRole('button', { name: /Status/ })
    .first()
    .click();
  await page.screenshot({ path: `${out}/status-filter-open-1440.png` });
});
