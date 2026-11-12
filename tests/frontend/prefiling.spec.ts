import path from 'node:path';
import { expect, test, type Page, type Route } from '@playwright/test';
import {
  horizontalOverflow,
  pageReady,
  seriousAxeViolations,
  tabUntilFocused,
} from './support/checks';
import { TAXPAYER } from './support/env';
import { mockTaxpayerSession } from './support/sessions';

// TAX-129 (ADR-051) check before you file, at every matrix width. The method
// `check_before_filing` is answered here in the shape vati_tax.portal.api returns; the
// server rules (RSL-held data only, guidance only, own taxpayer) are covered by
// vati_tax/prefiling/test_rules.py and test_prefiling_bench.py.
//
// CAPTURE_SCREENSHOTS=1 also writes screenshots to docs/evidence/TAX-129.
const capture = Boolean(process.env.CAPTURE_SCREENSHOTS);
const out = path.resolve(__dirname, '../../docs/evidence/TAX-129');
const HEADING = 'Check before you file';
const METHOD = '**/api/method/vati_tax.portal.api.check_before_filing';
const RSL_URL = 'https://refunds.example.test/dashboard';

const SOURCES = [
  { system: 'etax', label: 'your VAT return in e-Tax', checked: true },
  { system: 'idms', label: 'your purchase invoices in Lekuka', checked: true },
  { system: 'asycuda', label: 'your customs entries in ASYCUDA', checked: true },
];
const NOTE =
  "This check is guidance only. It does not file a claim, does not decide anything and does not stop you filing. File your refund claim in RSL's Online Refunds Application.";

function result(status: 'no_issues_found' | 'check_these', prompts: string[]) {
  return {
    check_version: 'tax129-prefiling-v1',
    period: '2026-09',
    status,
    currency: 'LSL',
    figures: {
      output_vat: '1200.00',
      input_vat_claimed: '9400.00',
      refund_claimed: '8200.00',
      lekuka_input_vat: status === 'check_these' ? '6400.00' : '9400.00',
      asycuda_import_vat: '0.00',
    },
    counts: { lekuka_invoices: 7, asycuda_entries: 0 },
    prompts: prompts.map((message, i) => ({ code: `code_${i}`, message })),
    sources: SOURCES,
    guidance_only: true,
    note: NOTE,
    file_with: { name: 'Online Refunds Application', url: RSL_URL },
  };
}

async function shot(page: Page, name: string, width: string) {
  if (!capture) return;
  await page.screenshot({ path: path.join(out, `${name}-${width}.png`), fullPage: true });
}

async function accessible(page: Page) {
  expect(await seriousAxeViolations(page)).toEqual([]);
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
}

const width = (project: string) => project.split('-')[1];

async function open(page: Page, answer: (route: Route) => Promise<void>) {
  await mockTaxpayerSession(page, 'taxpayer', { rslUrl: RSL_URL });
  const bodies: unknown[] = [];
  await page.route(METHOD, async (route) => {
    const raw = route.request().postData();
    bodies.push(raw ? JSON.parse(raw) : null);
    await answer(route);
  });
  await page.goto(TAXPAYER + '/taxpayer/check');
  await pageReady(page, HEADING);
  return bodies;
}

const json = (body: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
});

test.describe('Check before you file (TAX-129)', () => {
  test('shows what RSL holds, guidance and the link to file in RSL', async ({ page }, testInfo) => {
    const w = width(testInfo.project.name);
    const bodies = await open(page, (route) =>
      route.fulfill(
        json({
          message: {
            mode: 'live',
            data: result('check_these', [
              'Your return claims LSL 3,000.00 more input VAT than the invoices and customs entries RSL holds for 2026-09.',
            ]),
          },
        }),
      ),
    );
    await shot(page, 'empty', w);
    await page.getByLabel('VAT period', { exact: true }).fill('2026-09');
    await page.getByRole('button', { name: 'Check this period' }).click();
    await expect(page.getByRole('heading', { name: 'September 2026' })).toBeVisible();
    await expect(page.getByText('Worth checking', { exact: true }).first()).toBeVisible();
    const held = page.getByRole('region', { name: 'What RSL holds' });
    await expect(held.getByText('VAT on your purchase invoices in Lekuka')).toBeVisible();
    await expect(held.getByText('LSL 6,400.00')).toBeVisible();
    await expect(held.getByText('You did not need to type any of this.')).toBeVisible();
    await expect(page.getByText(/LSL 3,000.00 more input VAT/)).toBeVisible();
    const link = page.getByRole('link', { name: /Go to the Online Refunds Application to file/ });
    await expect(link).toHaveAttribute('href', RSL_URL);
    // Only the period is sent: nothing is typed, and there is no submit-a-claim action.
    expect(bodies).toEqual([{ period: '2026-09' }]);
    await expect(page.getByRole('button', { name: /file|submit|send/i })).toHaveCount(0);
    // Guidance only: no score, lane or decision language reaches the taxpayer.
    const text = (await page.locator('main').innerText()).toLowerCase();
    for (const word of ['score', 'risk', 'lane', 'eligible', 'approved', 'rejected', 'fraud']) {
      expect(text).not.toContain(word);
    }
    await accessible(page);
    await shot(page, 'check-these', w);
  });

  test('a clean period says so and still sends the taxpayer to RSL', async ({ page }, testInfo) => {
    const w = width(testInfo.project.name);
    await open(page, (route) =>
      route.fulfill(json({ message: { mode: 'live', data: result('no_issues_found', []) } })),
    );
    await page.getByRole('button', { name: 'Check this period' }).click();
    await expect(page.getByText('No issues found', { exact: true })).toBeVisible();
    await expect(page.getByText(/Nothing to check/)).toBeVisible();
    await expect(page.getByText(/does not stop you filing/)).toBeVisible();
    await expect(
      page.getByRole('link', { name: /Go to the Online Refunds Application to file/ }),
    ).toBeVisible();
    await accessible(page);
    await shot(page, 'no-issues', w);
  });

  test('a refused period and an unavailable service are shown, never as a result', async ({
    page,
  }) => {
    let calls = 0;
    await open(page, (route) => {
      calls += 1;
      return calls === 1
        ? route.fulfill(
            json(
              { error: { code: 'invalid_period', message: 'Choose a period that has started.' } },
              422,
            ),
          )
        : route.fulfill(json({ exc_type: 'ServiceUnavailable' }, 503));
    });
    await page.getByRole('button', { name: 'Check this period' }).click();
    await expect(page.getByText('Check the period')).toBeVisible();
    await page.getByRole('button', { name: 'Check this period' }).click();
    await expect(page.getByText('The check could not run')).toBeVisible();
    await expect(page.getByText('No issues found', { exact: true })).toHaveCount(0);
    await accessible(page);
  });

  test('the sidebar links to the check', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile-390', 'the sidebar is a menu at phone width');
    await mockTaxpayerSession(page, 'taxpayer', { rslUrl: RSL_URL });
    await page.goto(TAXPAYER + '/taxpayer/requests');
    await page.getByRole('link', { name: HEADING }).first().click();
    await pageReady(page, HEADING);
    await expect(page).toHaveURL(/\/taxpayer\/check$/);
  });

  test('the check is reachable and usable from the keyboard', async ({ page }) => {
    await open(page, (route) =>
      route.fulfill(json({ message: { mode: 'live', data: result('no_issues_found', []) } })),
    );
    await page.getByLabel('VAT period', { exact: true }).focus();
    await expect(page.getByLabel('VAT period', { exact: true })).toBeFocused();
    // The month field has its own month and year parts; Tab moves through them first.
    await tabUntilFocused(page, page.getByRole('button', { name: 'Check this period' }));
    await page.keyboard.press('Enter');
    await expect(page.getByText('No issues found', { exact: true })).toBeVisible();
  });
});
