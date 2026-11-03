import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';

// Review evidence for the reviewer's visual approval (docs/DESIGN_STANDARD.md). Not a
// baseline comparison: the frontend QA owner (Kananelo) captures approved baselines after acceptance.
// Run with CAPTURE_SCREENSHOTS=1; output goes to docs/evidence/TAX-020.
test.skip(
  !process.env.CAPTURE_SCREENSHOTS,
  'Set CAPTURE_SCREENSHOTS=1 to capture review screenshots',
);

const out = path.resolve(__dirname, '../../../docs/evidence/TAX-020');
const widths = [1440, 1024, 390];
const pages = [
  { name: 'overview', path: '/auditor/' },
  { name: 'queue', path: '/auditor/cases' },
  { name: 'case-detail', path: '/auditor/cases/SYN-002' },
  { name: 'showcase', path: '/auditor/showcase' },
];

async function ready(page: Page) {
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await page.waitForLoadState('networkidle');
}

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
    await page.route('**/api/cases', async (route) => {
      await gate;
      await route.continue();
    });
    await page.goto('/auditor/cases');
    await expect(page.getByText('Loading cases…').first()).toBeVisible();
    await shot('loading');
    release();
    await expect(page.getByRole('row')).toHaveCount(6);
    await page.unroute('**/api/cases');

    await page.route('**/api/cases', (route) =>
      route.fulfill({ json: { mode: 'fixture', synthetic: true, items: [] } }),
    );
    await page.goto('/auditor/cases');
    await expect(page.getByText('No cases waiting for review')).toBeVisible();
    await shot('empty');
    await page.unroute('**/api/cases');

    await page.goto('/auditor/cases?q=no-such-taxpayer');
    await expect(page.getByText('No cases match these filters')).toBeVisible();
    await shot('no-matches');

    await page.route('**/api/cases', (route) => route.abort('connectionrefused'));
    await page.goto('/auditor/cases');
    await expect(page.getByText('Case data is temporarily unavailable')).toBeVisible();
    await shot('error');
    await page.unroute('**/api/cases');

    await page.route('**/api/cases/SYN-001', (route) =>
      route.fulfill({ status: 403, json: { error: 'forbidden' } }),
    );
    await page.goto('/auditor/cases/SYN-001');
    await expect(page.getByText('You do not have access')).toBeVisible();
    await shot('denied');

    await page.goto('/auditor/cases/SYN-999');
    await expect(page.getByText('Case not found')).toBeVisible();
    await shot('not-found');

    await page.goto('/auditor/cases/SYN-002');
    await page.getByRole('tab', { name: 'Findings' }).click();
    await expect(page.getByText('Findings are not available in fixture mode')).toBeVisible();
    await shot('detail-unavailable-tab');
  });
}

test('interaction states at 1440px', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/auditor/cases');
  await ready(page);
  await page.keyboard.press('Tab');
  await page.screenshot({ path: `${out}/focus-skip-link-1440.png` });
  await page.getByRole('columnheader', { name: 'Case' }).getByRole('button').focus();
  await page.screenshot({ path: `${out}/focus-table-sort-1440.png` });

  await page.goto('/auditor/showcase');
  await ready(page);
  const buttons = page
    .getByRole('region', { name: 'Buttons' })
    .or(page.locator('section[aria-labelledby="sc-buttons"]'));
  await page.getByRole('button', { name: 'Primary action' }).hover();
  await buttons.first().screenshot({ path: `${out}/showcase-hover-1440.png` });
  await page.getByRole('button', { name: 'Secondary' }).focus();
  await buttons.first().screenshot({ path: `${out}/showcase-focus-1440.png` });

  await page.getByRole('button', { name: 'Validate example' }).click();
  await expect(page.getByText('Enter a case reference in the format SYN-001.')).toBeVisible();
  await page
    .locator('section[aria-labelledby="sc-form"]')
    .screenshot({ path: `${out}/showcase-validation-1440.png` });

  await page.getByRole('button', { name: 'Open confirmation dialog' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.screenshot({ path: `${out}/showcase-dialog-1440.png` });
});
