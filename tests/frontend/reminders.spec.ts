import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { horizontalOverflow, pageReady, seriousAxeViolations } from './support/checks';
import { TAXPAYER } from './support/env';
import { fillDetails, toReview, upload } from './support/claim-flow';
import { mockTaxpayerSession, SYNTHETIC_REMINDERS, type PortalMock } from './support/sessions';

// TAX-078 correction reminders on the VAT refund claim, at every matrix width.
// `get_correction_reminders` and `review_refund_claim` are answered by the
// session mocks (support/sessions.ts) in the shapes the Frappe methods return;
// the server rules (own taxpayer only, withdrawn or overturned reminders
// hidden, prompt marking) are covered by vati_tax/reminders/test_service.py and
// test_reminders_bench.py.
//
// CAPTURE_SCREENSHOTS=1 also writes screenshots to docs/evidence/TAX-078
// (evidence for human review only; nothing is compared or approved here).
const capture = Boolean(process.env.CAPTURE_SCREENSHOTS);
const out = path.resolve(__dirname, '../../docs/evidence/TAX-078');
const NEW_CLAIM = '/taxpayer/submissions/new';
const HEADING = 'New VAT refund claim';
const PANEL = 'From your earlier claims';

async function shot(page: Page, name: string, width: string) {
  if (!capture) return;
  await page.screenshot({ path: path.join(out, `${name}-${width}.png`), fullPage: true });
}

async function accessible(page: Page) {
  expect(await seriousAxeViolations(page)).toEqual([]);
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
}

async function open(page: Page, setup: (mock: PortalMock) => void = () => {}) {
  const mock = await mockTaxpayerSession(page, 'taxpayer');
  setup(mock);
  await page.goto(TAXPAYER + NEW_CLAIM);
  await pageReady(page, HEADING);
  return mock;
}

const width = (project: string) => project.split('-')[1];

test.describe('Correction reminders (TAX-078)', () => {
  test('reminders from earlier claims are shown before the claim is filled in', async ({
    page,
  }, testInfo) => {
    const w = width(testInfo.project.name);
    const mock = await open(page, (m) => (m.reminders = SYNTHETIC_REMINDERS));
    const panel = page.getByRole('region', { name: PANEL });
    await expect(panel).toBeVisible();
    const items = panel.getByRole('list', { name: 'Reminders from your earlier claims' });
    await expect(items.getByRole('listitem')).toHaveCount(2);
    await expect(items.getByText('List every purchase invoice', { exact: true })).toBeVisible();
    await expect(
      items.getByText(/Two invoices from Synthetic Highland Building Supplies/),
    ).toBeVisible();
    // A corrected reminder says when it was last updated; an unchanged one when it was noted.
    await expect(items.getByText('Updated 20 Aug 2026')).toBeVisible();
    await expect(items.getByText('Noted 2 May 2026')).toBeVisible();
    await expect(
      panel.getByText('This is guidance, not a decision about this claim.'),
    ).toBeVisible();
    await expect(panel.getByText('They can correct or withdraw it.')).toBeVisible();
    // Guidance only: no decision, risk or staff details reach the taxpayer.
    const text = (await panel.innerText()).toLowerCase();
    for (const word of ['fraud', 'penalt', 'risk', 'suspic', 'officer@', 'case']) {
      expect(text).not.toContain(word);
    }
    // The form itself is unchanged and still usable from the keyboard.
    await page.getByLabel('Tax period').focus();
    await expect(page.getByLabel('Tax period')).toBeFocused();
    expect(mock.calls).toContain('vati_tax.portal.api.get_correction_reminders');
    await accessible(page);
    await shot(page, 'details-with-reminders', w);
  });

  test('a first-time claimant sees no reminder panel', async ({ page }, testInfo) => {
    const w = width(testInfo.project.name);
    const mock = await open(page);
    await expect(page.getByText('All fields are required unless marked optional.')).toBeVisible();
    await expect(page.getByRole('region', { name: PANEL })).toHaveCount(0);
    expect(mock.calls).toContain('vati_tax.portal.api.get_correction_reminders');
    await accessible(page);
    await shot(page, 'details-no-reminders', w);
  });

  test('if reminders cannot be loaded the claim can still be filled in and retried', async ({
    page,
  }, testInfo) => {
    const w = width(testInfo.project.name);
    const mock = await open(page, (m) => {
      m.reminders = SYNTHETIC_REMINDERS;
      m.remindersUnavailable = true;
    });
    await expect(
      page.getByText('Reminders from your earlier claims could not be loaded'),
    ).toBeVisible();
    await expect(page.getByText('You can still fill in and send this claim.')).toBeVisible();
    await expect(page.getByLabel('Tax period')).toBeEditable();
    await accessible(page);
    await shot(page, 'details-reminders-error', w);

    mock.remindersUnavailable = false;
    await page.getByRole('button', { name: 'Try again' }).click();
    await expect(page.getByRole('region', { name: PANEL })).toBeVisible();
  });

  test('pre-submission prompts the taxpayer was reminded about are labelled', async ({
    page,
  }, testInfo) => {
    const w = width(testInfo.project.name);
    await open(page, (m) => (m.reminders = SYNTHETIC_REMINDERS));
    // Claim more than the invoice VAT and leave out the amount paid: two
    // reminded-about gaps, plus a contract prompt nobody was reminded about.
    await fillDetails(page, { claimed: '50,000.00', inputVat: '50000.00' });
    await upload(page);
    await toReview(page);

    const checks = page.getByRole('list', { name: 'Things to check' });
    const label = 'Also noted on an earlier claim';
    const missing = checks
      .getByRole('listitem')
      .filter({ hasText: 'more than the VAT on the invoices' });
    const paid = checks.getByRole('listitem').filter({ hasText: 'add the amount paid' });
    const contract = checks.getByRole('listitem').filter({ hasText: 'contract or purchase order' });
    await expect(missing.getByText(label)).toBeVisible();
    await expect(paid.getByText(label)).toBeVisible();
    await expect(contract).toBeVisible();
    await expect(contract.getByText(label)).toHaveCount(0);
    // The label is part of the prompt's description for the "Change" button.
    const change = missing.getByRole('button', { name: 'Change the amount or period figures' });
    await expect(change).toHaveAccessibleDescription(/Also noted on an earlier claim/);
    // Guidance never blocks sending.
    await expect(page.getByRole('button', { name: 'Send claim' })).toBeEnabled();
    await accessible(page);
    await shot(page, 'review-recurring-prompts', w);
    if (capture) {
      // The page scrolls inside the app shell, so also capture the checks panel itself.
      const panel = page.getByRole('region', { name: 'Before you send' });
      await panel.scrollIntoViewIfNeeded();
      await panel.screenshot({ path: path.join(out, `review-recurring-checks-${w}.png`) });
    }
  });

  test('withdrawn or hidden reminders leave the checks unlabelled', async ({ page }) => {
    // The server returns no reminder once staff withdraw it or its decision is
    // challenged or overturned; the review then marks nothing.
    await open(page);
    await fillDetails(page, { claimed: '50,000.00', inputVat: '50000.00' });
    await upload(page);
    await toReview(page);
    await expect(page.getByRole('list', { name: 'Things to check' })).toBeVisible();
    await expect(page.getByText('Also noted on an earlier claim')).toHaveCount(0);
  });
});
