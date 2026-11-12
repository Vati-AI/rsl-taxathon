import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { horizontalOverflow, pageReady, seriousAxeViolations } from './support/checks';
import { TAXPAYER } from './support/env';
import {
  mockTaxpayerSession,
  OPEN_REQUEST_ID,
  SYNTHETIC_REMINDERS,
  type PortalMock,
} from './support/sessions';

// TAX-126: checks on an evidence response before it is sent, and reminders from earlier
// claims on the response page, at every matrix width. `review_evidence_response` is
// answered by the session mock, which keys the outcome off the uploaded file's name;
// the server checks are covered by vati_tax/responses/test_checks.py and
// test_responses_bench.py. Guidance never blocks sending, whatever it says or if it fails.
// CAPTURE_SCREENSHOTS=1 writes docs/evidence/TAX-126.

const capture = Boolean(process.env.CAPTURE_SCREENSHOTS);
const out = path.resolve(__dirname, '../../docs/evidence/TAX-126');
const width = (project: string) => project.split('-')[1];

function pdf(name: string) {
  return { name, mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n% synthetic\n') };
}

async function shot(page: Page, name: string, project: string) {
  if (!capture) return;
  // The reading pane scrolls inside the page, so a full-page capture would stop at the
  // viewport: grow the viewport to the pane's content for the capture, then restore it.
  const viewport = page.viewportSize()!;
  const height = await page
    .locator('[data-kit="request-detail"]')
    .evaluate((el) => el.getBoundingClientRect().top + el.scrollHeight + 48);
  await page.setViewportSize({
    width: viewport.width,
    height: Math.max(viewport.height, Math.ceil(height)),
  });
  await page.screenshot({ path: path.join(out, `${name}-${width(project)}.png`), fullPage: true });
  await page.setViewportSize(viewport);
}

async function accessible(page: Page) {
  expect(await seriousAxeViolations(page)).toEqual([]);
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
}

async function openRequest(page: Page, setup: (mock: PortalMock) => void = () => {}) {
  const mock = await mockTaxpayerSession(page, 'taxpayer');
  setup(mock);
  await page.goto(TAXPAYER + `/taxpayer/requests/${OPEN_REQUEST_ID}`);
  await pageReady(page, 'Evidence request');
  return mock;
}

const steps = (page: Page) => page.getByRole('list', { name: 'Response steps' });
const goToStep = (page: Page, name: string) =>
  steps(page)
    .getByRole('button', { name: new RegExp(`^${name}`) })
    .click();
const checks = (page: Page) => page.locator('[data-kit="response-checks"]');
const itemChecks = (page: Page, index: number) => checks(page).locator(`[data-item="${index}"]`);

async function upload(page: Page, item: number, name: string) {
  await goToStep(page, `Item ${item}`);
  await page.getByLabel(`Choose a file for item ${item}`).setInputFiles(pdf(name));
  await expect(
    page
      .getByRole('list', { name: `Documents for item ${item}` })
      .getByRole('listitem')
      .filter({ hasText: name })
      .getByText('Uploaded', { exact: true }),
  ).toBeVisible();
}

test.describe('Checks before an evidence response is sent (TAX-126)', () => {
  test('each answered item shows guidance, and the response still sends', async ({
    page,
  }, testInfo) => {
    const mock = await openRequest(page, (m) => {
      m.reminders = [
        ...SYNTHETIC_REMINDERS,
        { reminder_id: '7a2e4c10-0000-4000-8000-000000000126', topic: 'period_figures',
          title: "Check the period's VAT figures",
          explanation: 'Your May 2026 claim used figures from April. Use documents for the claim period.',
          prompt_codes: ['return_arithmetic_mismatch'], recorded_on: '2026-07-02', updated_on: null }, // prettier-ignore
      ];
    });
    await upload(page, 1, 'invoice-0912-blurry.pdf');
    await upload(page, 2, 'statement-january.pdf');
    await goToStep(page, 'Review and send');

    await expect(checks(page).getByText('2 things to look at')).toBeVisible();
    await expect(itemChecks(page, 0).locator('[data-check="document_not_readable"]')).toContainText(
      'We could not read any text in invoice-0912-blurry.pdf',
    );
    const period = itemChecks(page, 1).locator('[data-check="document_period_differs"]');
    await expect(period).toContainText('not in September 2026, the period of your claim');
    // The server marked the period reminder on this check.
    await expect(period).toContainText('Also noted on an earlier claim.');
    await accessible(page);
    await shot(page, 'review-with-guidance', testInfo.project.name);

    // "Change item 2" goes back to that step; replacing the document re-checks.
    await itemChecks(page, 1).getByRole('button', { name: 'Change item 2' }).click();
    await expect(page.getByRole('heading', { name: /^Item 2:/ })).toBeFocused();
    await page
      .getByRole('list', { name: 'Documents for item 2' })
      .getByRole('button', { name: /^Remove/ })
      .click();
    await upload(page, 2, 'statement-september.pdf');
    const before = mock.responseReviews;
    await goToStep(page, 'Review and send');
    await expect(
      itemChecks(page, 1).locator('[data-check="document_matches_request"]'),
    ).toBeVisible();
    expect(mock.responseReviews).toBe(before + 1);
    await expect(checks(page).getByText('1 thing to look at')).toBeVisible();

    // Guidance is not a gate: the unreadable invoice is sent as it is.
    await page.getByRole('button', { name: 'Send response' }).click();
    await expect(page.getByRole('heading', { name: 'Your response' })).toBeVisible();
    expect(mock.calls).toContain('vati_tax.portal.api.respond_to_evidence_request');
  });

  test('a document still being read can be checked again', async ({ page }, testInfo) => {
    const mock = await openRequest(page);
    await upload(page, 1, 'invoice-reading.pdf');
    await goToStep(page, 'Review and send');
    await expect(
      itemChecks(page, 0).locator('[data-check="document_still_being_read"]'),
    ).toContainText('You can send now');
    await shot(page, 'review-still-reading', testInfo.project.name);
    // Going back and forth with the same documents does not ask again.
    await goToStep(page, 'Item 1');
    await goToStep(page, 'Review and send');
    expect(mock.responseReviews).toBe(1);
    await checks(page).getByRole('button', { name: 'Check again' }).click();
    await expect(
      itemChecks(page, 0).locator('[data-check="document_matches_request"]'),
    ).toBeVisible();
    await expect(checks(page).getByText('Nothing to point out in your documents')).toBeVisible();
    await expect(checks(page).getByRole('button', { name: 'Check again' })).toHaveCount(0);
    await accessible(page);
    await shot(page, 'review-clear', testInfo.project.name);
  });

  test('checks that cannot run never block sending', async ({ page }, testInfo) => {
    const mock = await openRequest(page, (m) => (m.responseReviewUnavailable = true));
    await upload(page, 1, 'invoice-0912.pdf');
    await goToStep(page, 'Item 2');
    await page.getByLabel('I cannot provide this document (item 2)').check();
    await page.getByLabel("Why can't you provide it? (item 2)").fill('Synthetic: account closed.');
    await goToStep(page, 'Review and send');
    await expect(checks(page).getByText('The checks could not run just now')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Send response' })).toBeEnabled();
    await accessible(page);
    await shot(page, 'review-checks-unavailable', testInfo.project.name);
    await page.getByRole('button', { name: 'Send response' }).click();
    await expect(page.getByRole('heading', { name: 'Your response' })).toBeVisible();
    // Items answered with a reason are not sent for checking.
    expect(mock.responseReviews).toBe(1);
  });
});

test.describe('Reminders from earlier claims on the response page (TAX-126)', () => {
  test('only reminders about the requested documents are shown', async ({ page }, testInfo) => {
    await openRequest(page, (m) => {
      m.reminders = [
        ...SYNTHETIC_REMINDERS,
        { reminder_id: '7a2e4c10-0000-4000-8000-000000000127', topic: 'contract_documents',
          title: 'Attach contracts or purchase orders', explanation: 'Synthetic: attach contracts.',
          prompt_codes: ['contract_evidence_missing'], recorded_on: '2026-07-02', updated_on: null }, // prettier-ignore
      ];
    });
    const disclosure = page.locator('[data-kit="response-reminders"]');
    // Closed by default so the response steps stay in view; it says how many there are.
    await expect(disclosure.locator('summary')).toHaveText('2 reminders from your earlier claims');
    await disclosure.locator('summary').click();
    const panel = page.getByRole('region', { name: 'From your earlier claims' });
    await expect(panel).toBeVisible();
    await expect(panel).toContainText('They may help you choose the documents for this request.');
    // The request asks for an invoice and a bank statement, not a contract.
    await expect(panel.locator('[data-reminder]')).toHaveCount(2);
    await expect(panel.locator('[data-reminder="contract_documents"]')).toHaveCount(0);
    await accessible(page);
    await shot(page, 'reminders', testInfo.project.name);
  });

  test('reminders that cannot load never block the response', async ({ page }, testInfo) => {
    const mock = await openRequest(page, (m) => (m.remindersUnavailable = true));
    await expect(
      page.getByText('Reminders from your earlier claims could not be loaded'),
    ).toBeVisible();
    await expect(page.getByText('You can still answer and send this request.')).toBeVisible();
    await shot(page, 'reminders-unavailable', testInfo.project.name);
    await upload(page, 1, 'invoice-0912.pdf');
    await upload(page, 2, 'statement-september.pdf');
    await goToStep(page, 'Review and send');
    await page.getByRole('button', { name: 'Send response' }).click();
    await expect(page.getByRole('heading', { name: 'Your response' })).toBeVisible();
    expect(mock.calls).toContain('vati_tax.portal.api.respond_to_evidence_request');
  });

  test('a first-time claimant sees no reminders panel', async ({ page }) => {
    await openRequest(page);
    await expect(page.locator('[data-kit="response-reminders"]')).toHaveCount(0);
    await expect(
      page.getByText('Reminders from your earlier claims could not be loaded'),
    ).toHaveCount(0);
  });
});
