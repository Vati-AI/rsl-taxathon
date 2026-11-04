import path from 'node:path';
import { expect, test, type Page, type Route } from '@playwright/test';

// TAX-041 review screenshots for the reviewer's visual check (docs/DESIGN_STANDARD.md). Not
// a baseline comparison: the frontend QA owner (Kananelo) captures approved baselines.
// Run with CAPTURE_SCREENSHOTS=1; output goes to docs/evidence/TAX-041.
test.skip(
  !process.env.CAPTURE_SCREENSHOTS,
  'Set CAPTURE_SCREENSHOTS=1 to capture review screenshots',
);

const out = path.resolve(__dirname, '../../../docs/evidence/TAX-041');
const CASE_2 = '40000000-0000-4000-8000-000000000002';
const CASE_7 = '40000000-0000-4000-8000-000000000007';
const ANNUAL_RETURN = '50000000-0000-4000-8000-000000000026';
const OLD_SCAN = '50000000-0000-4000-8000-000000000024';
const FINDING = '70000000-0000-4000-8000-000000000022';
const RECORDS = /\/api\/v1\/cases\/[^/]+\/(evidence|entities|findings)/;

const screens = [
  {
    name: 'case-evidence',
    path: `/auditor/cases/${CASE_2}?tab=evidence`,
    ready: 'Evidence on file',
  },
  {
    name: 'case-relationships',
    path: `/auditor/cases/${CASE_2}?tab=relationships`,
    ready: 'Ownership records',
  },
  { name: 'case-findings', path: `/auditor/cases/${CASE_2}?tab=findings`, ready: null },
  {
    name: 'evidence-cited-page',
    path: `/auditor/cases/${CASE_2}/evidence/${ANNUAL_RETURN}?page=3&finding=${FINDING}`,
    ready: null,
  },
  {
    name: 'evidence-superseded-failed',
    path: `/auditor/cases/${CASE_2}/evidence/${OLD_SCAN}`,
    ready: null,
  },
];

async function settle(page: Page, region: string | null) {
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  if (region) await expect(page.getByRole('region', { name: region })).toBeVisible();
  await page.waitForLoadState('networkidle');
}

for (const width of [1440, 1024, 390]) {
  test(`TAX-041 screens at ${width}px`, async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width, height: 900 });
    for (const screen of screens) {
      await page.goto(screen.path);
      await settle(page, screen.ready);
      await page.screenshot({ path: `${out}/${screen.name}-${width}.png`, fullPage: true });
    }
  });
}

for (const width of [1440, 390]) {
  test(`TAX-041 states at ${width}px`, async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width, height: 900 });
    const shot = (name: string) => page.screenshot({ path: `${out}/state-${name}-${width}.png` });

    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    await page.route(RECORDS, async (route) => {
      await gate;
      await route.continue();
    });
    await page.goto(`/auditor/cases/${CASE_2}?tab=evidence`);
    await expect(page.getByRole('status', { name: 'Loading evidence' })).toBeVisible();
    await shot('loading');
    release();
    await expect(page.getByRole('region', { name: 'Evidence on file' })).toBeVisible();
    await page.unroute(RECORDS);

    await page.goto(`/auditor/cases/${CASE_7}?tab=findings`);
    await expect(page.getByText('No findings yet')).toBeVisible();
    await shot('empty-findings');

    await page.route(RECORDS, (route: Route) =>
      route.continue({
        headers: {
          ...route.request().headers(),
          'x-vati-tenant-id': '10000000-0000-4000-8000-000000000002',
        },
      }),
    );
    await page.goto(`/auditor/cases/${CASE_2}?tab=relationships`);
    await expect(page.getByText('You do not have access')).toBeVisible();
    await shot('denied');
    await page.unroute(RECORDS);

    await page.route(RECORDS, (route) => route.abort('connectionrefused'));
    await page.goto(`/auditor/cases/${CASE_2}?tab=findings`);
    await expect(page.getByText('Case data is temporarily unavailable')).toBeVisible();
    await shot('unavailable');
    await page.unroute(RECORDS);

    await page.goto(`/auditor/cases/${CASE_2}/evidence/50000000-0000-4000-8000-000000000099`);
    await expect(page.getByText('Evidence not found')).toBeVisible();
    await shot('evidence-not-found');
  });
}

test('TAX-041 keyboard focus at 1440px', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/auditor/cases/${CASE_2}?tab=findings`);
  await expect(page.getByRole('heading', { name: 'Records disagree (1)' })).toBeVisible();
  await page.getByRole('link', { name: 'Financial statement v1 · Page 3' }).focus();
  await page.screenshot({ path: `${out}/focus-citation-link-1440.png` });
  await page.goto(`/auditor/cases/${CASE_2}?tab=findings&finding=${FINDING}`);
  await expect(page.locator(`#finding-${FINDING}`)).toBeFocused();
  await page.screenshot({ path: `${out}/focus-returned-finding-1440.png` });
});
