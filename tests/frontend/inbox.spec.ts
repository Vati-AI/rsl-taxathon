import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { hasVisibleFocus, pageReady, seriousAxeViolations } from './support/checks';
import { TAXPAYER } from './support/env';
import { BUDGET_VIEWPORTS, findComponentNames, measurePageLength } from './support/layout';
import { isMobile } from './support/matrix';
import { mockTaxpayerSession, OPEN_REQUEST_ID } from './support/sessions';

// TAX-152: the taxpayer portal's evidence requests as an inbox. A list grouped by
// reply-by date beside a reading pane (list then detail at 390px), and the response as
// short steps that each fit one viewport and keep what was entered.
// CAPTURE_SCREENSHOTS=1 writes docs/evidence/TAX-152.

const INBOX = `${TAXPAYER}/taxpayer/requests`;
const LIST_API = '**/vati_tax.portal.api.get_evidence_requests*';

async function shot(page: Page, name: string, project: string) {
  if (!process.env.CAPTURE_SCREENSHOTS) return;
  await page.waitForFunction(() =>
    document
      .getAnimations()
      .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
      .every((a) => a.playState !== 'running'),
  );
  await page.screenshot({
    path: path.resolve(
      __dirname,
      `../../docs/evidence/TAX-152/${name}-${project.split('-')[1]}.png`,
    ),
    fullPage: true,
  });
}

async function checkPage(page: Page, project: string) {
  expect(await seriousAxeViolations(page)).toEqual([]);
  expect(await findComponentNames(page)).toEqual([]);
  // Taxpayers never see officer navigation or risk signals.
  await expect(page.getByRole('navigation', { name: 'Auditor workspace' })).toHaveCount(0);
  await expect(page.getByText(/risk score|selection reason|fraud|supervisor/i)).toHaveCount(0);
  if (project === 'desktop-1440') {
    const length = await measurePageLength(page);
    expect(length.viewports, 'page-length budget').toBeLessThanOrEqual(BUDGET_VIEWPORTS);
  }
}

const list = (page: Page) => page.getByRole('navigation', { name: 'Evidence requests' });
const pane = (page: Page) => page.locator('[data-kit="reading-pane"]');

test.describe('evidence-request inbox (TAX-152)', () => {
  test('list-detail on desktop, list then detail on a phone', async ({ page }, testInfo) => {
    await mockTaxpayerSession(page, 'taxpayer', { dataMode: 'fixture' });
    await page.goto(INBOX);
    await pageReady(page, 'Evidence requests');
    // Grouped like messages: open requests by reply-by date, then answered, then closed.
    await expect(list(page).getByRole('heading', { level: 2 })).toHaveText([
      /^Reply/,
      /^Answered/,
      /^No longer needed/,
    ]);
    // Fixture mode is labelled in the header strip only.
    await expect(page.locator('[data-kit="fixture-strip"]')).toBeVisible();
    await expect(
      page.locator('footer').getByRole('link', { name: 'Open-source notices' }),
    ).toBeVisible();
    await checkPage(page, testInfo.project.name);
    await shot(page, 'inbox', testInfo.project.name);

    const first = list(page).getByRole('link').first();
    await first.focus();
    expect(await hasVisibleFocus(first)).toBe(true);
    await page.keyboard.press('Enter');
    await pageReady(page, 'Evidence request');
    await expect(pane(page).getByText('Message from the officer')).toBeVisible();
    if (isMobile(testInfo.project.name)) {
      await expect(list(page)).toBeHidden();
      await pane(page).getByRole('link', { name: 'All evidence requests' }).click();
      await expect(list(page)).toBeVisible();
    } else {
      await expect(list(page)).toBeVisible();
      await expect(list(page).locator('a[aria-current="page"]')).toHaveCount(1);
      const listBox = (await list(page).boundingBox())!;
      const paneBox = (await pane(page).boundingBox())!;
      expect(paneBox.x).toBeGreaterThan(listBox.x + listBox.width - 1);
    }
    await checkPage(page, testInfo.project.name);
    await shot(page, 'request', testInfo.project.name);
  });

  test('each response step fits one viewport and keeps values on error', async ({
    page,
  }, testInfo) => {
    await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(`${INBOX}/${OPEN_REQUEST_ID}`);
    await pageReady(page, 'Evidence request');
    const steps = page.getByRole('list', { name: 'Response steps' });
    const viewport = page.viewportSize()!.height;
    for (const name of ['Item 1', 'Item 2', 'Review and send']) {
      await steps.getByRole('button', { name: new RegExp(`^${name}`) }).click();
      const step = page
        .locator('form section')
        .filter({ has: page.getByRole('heading', { name: new RegExp(`^${name}`) }) });
      const box = (await step.boundingBox())!;
      expect(box.height, `${name} step height`).toBeLessThanOrEqual(viewport);
      await shot(page, `step-${name.toLowerCase().replaceAll(' ', '-')}`, testInfo.project.name);
    }
    // Enter values, send with an item unanswered: the values stay, the step shows why.
    await steps.getByRole('button', { name: /^Item 2/ }).click();
    await page.getByLabel('I cannot provide this document (item 2)').check();
    await page
      .getByLabel("Why can't you provide it? (item 2)")
      .fill('Synthetic: the supplier closed.');
    await steps.getByRole('button', { name: 'Review and send' }).click();
    await page.getByLabel('Message to the officer (optional)').fill('Synthetic: see item 2.');
    await page.getByRole('button', { name: 'Send response' }).click();
    await expect(page.getByRole('heading', { name: /^Item 1:/ })).toBeFocused();
    await expect(
      page.getByRole('alert').filter({ hasText: '1 item needs an answer' }),
    ).toBeVisible();
    await shot(page, 'step-error', testInfo.project.name);
    await steps.getByRole('button', { name: /^Item 2/ }).click();
    await expect(page.getByLabel("Why can't you provide it? (item 2)")).toHaveValue(
      'Synthetic: the supplier closed.',
    );
    await steps.getByRole('button', { name: 'Review and send' }).click();
    await expect(page.getByLabel('Message to the officer (optional)')).toHaveValue(
      'Synthetic: see item 2.',
    );
    expect(await seriousAxeViolations(page)).toEqual([]);
  });

  test('loading, empty, error and denied states', async ({ page }, testInfo) => {
    const mock = await mockTaxpayerSession(page, 'taxpayer');
    let release: () => void = () => {};
    const held = new Promise<void>((r) => (release = r));
    await page.route(LIST_API, async (route) => {
      await held;
      await route.fallback();
    });
    await page.goto(INBOX);
    await expect(page.getByText('Loading evidence requests…')).toBeVisible();
    await shot(page, 'state-loading', testInfo.project.name);
    release();
    await expect(list(page)).toBeVisible();
    await page.unroute(LIST_API);

    mock.requests.clear();
    await page.goto(INBOX);
    await expect(page.getByText('No evidence requests')).toBeVisible();
    await checkPage(page, testInfo.project.name);
    await shot(page, 'state-empty', testInfo.project.name);

    await page.route(LIST_API, (route) =>
      route.fulfill({ status: 503, json: { exc_type: 'ServiceUnavailable' } }),
    );
    await page.goto(INBOX);
    await expect(page.getByText('Evidence requests could not be loaded')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
    await shot(page, 'state-error', testInfo.project.name);
    await page.unroute(LIST_API);

    await page.route(LIST_API, (route) =>
      route.fulfill({
        status: 403,
        json: { exc_type: 'PermissionError', exception: 'PermissionError: Not permitted' },
      }),
    );
    await page.goto(INBOX);
    await expect(page.getByText('Access denied')).toBeVisible();
    await shot(page, 'state-denied', testInfo.project.name);
  });
});
