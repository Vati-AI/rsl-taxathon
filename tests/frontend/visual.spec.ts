import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { pageReady } from './support/checks';
import { AUDITOR, TAXPAYER } from './support/env';
import { AUDITOR_ROUTES, TAXPAYER_ROUTES, type MatrixRoute } from './support/matrix';
import {
  mockAuditorAccess,
  mockTaxpayerSession,
  OTHER_TAXPAYER_SUBMISSION_ID,
} from './support/sessions';

// Visual checks at every matrix width, in two opt-in modes:
//
//   CAPTURE_SCREENSHOTS=1  write review screenshots to docs/evidence/TAX-023.
//                          Evidence for human review only; nothing is compared.
//   VISUAL_COMPARE=1       compare against approved baselines in baselines/.
//                          Baselines are added (with --update-snapshots) only
//                          after the human frontend owner accepts the screens;
//                          agents never self-approve them (docs/DESIGN_STANDARD.md).
const capture = Boolean(process.env.CAPTURE_SCREENSHOTS);
const compare = Boolean(process.env.VISUAL_COMPARE);
test.skip(!capture && !compare, 'Set CAPTURE_SCREENSHOTS=1 or VISUAL_COMPARE=1');

const out = path.resolve(__dirname, '../../docs/evidence/TAX-023');

async function check(page: Page, file: string) {
  if (capture) {
    await page.screenshot({ path: path.join(out, `${file}.png`), fullPage: true });
  }
  if (compare) {
    await expect(page).toHaveScreenshot(`${file}.png`, { fullPage: true, animations: 'disabled' });
  }
}

async function visit(page: Page, base: string, route: MatrixRoute) {
  await page.goto(base + route.path);
  await pageReady(page, route.heading);
}

const width = (name: string) => name.split('-')[1];

test('auditor screens and denied state', async ({ page }, testInfo) => {
  const w = width(testInfo.project.name);
  for (const route of AUDITOR_ROUTES.filter((r) => r.name !== 'showcase')) {
    await visit(page, AUDITOR, route);
    await check(page, `auditor-${route.name}-${w}`);
  }
  await mockAuditorAccess(page, 'denied');
  await page.goto(AUDITOR + AUDITOR_ROUTES.find((r) => r.name === 'case-detail')!.path);
  await expect(page.getByRole('alert').filter({ hasText: 'You do not have access' })).toBeVisible();
  await check(page, `auditor-denied-${w}`);
});

test('taxpayer screens and denied states', async ({ page }, testInfo) => {
  const w = width(testInfo.project.name);
  const mock = await mockTaxpayerSession(page, 'taxpayer');
  for (const route of TAXPAYER_ROUTES) {
    await visit(page, TAXPAYER, route);
    await check(page, `taxpayer-${route.name}-${w}`);
  }
  await page.goto(TAXPAYER + `/taxpayer/submissions/${OTHER_TAXPAYER_SUBMISSION_ID}`);
  await pageReady(page, 'Case not found');
  await check(page, `taxpayer-other-taxpayer-${w}`);
  mock.persona = 'revoked';
  await page.goto(TAXPAYER + '/taxpayer/submissions');
  await expect(page.getByText('Access denied')).toBeVisible();
  await check(page, `taxpayer-denied-${w}`);
});

test('keyboard focus states', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-1440', 'Focus evidence is captured at 1440px');
  await visit(page, AUDITOR, AUDITOR_ROUTES[0]);
  await page.keyboard.press('Tab');
  await check(page, 'auditor-focus-skip-link-1440');

  await mockTaxpayerSession(page, 'taxpayer');
  await page.goto(TAXPAYER + '/taxpayer/submissions/new');
  await pageReady(page, 'Online Refunds simulator');
  await page.getByLabel('Tax period').press('Enter');
  await expect(page.getByLabel('Tax period')).toBeFocused();
  await check(page, 'taxpayer-focus-first-invalid-1440');
});
