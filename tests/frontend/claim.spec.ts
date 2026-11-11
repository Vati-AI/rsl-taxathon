import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { horizontalOverflow, pageReady, seriousAxeViolations } from './support/checks';
import { TAXPAYER } from './support/env';
import {
  chooseReason,
  field,
  fillDetails,
  fillInvoice,
  pdf,
  toInvoices,
  toReview,
  upload,
} from './support/claim-flow';
import { mockTaxpayerSession, type PortalMock } from './support/sessions';

// TAX-043 VAT refund claim form, at every matrix width. The portal API and the
// TAX-032 upload are answered by the session mocks (support/sessions.ts) in the
// shapes the Frappe methods use; the server-side rules themselves are covered by
// vati_tax/portal/test_refund_claim_bench.py on a disposable Frappe site.
//
// CAPTURE_SCREENSHOTS=1 also writes review screenshots to docs/evidence/TAX-044
// (the claim form as extended by TAX-044; docs/evidence/TAX-043 stays as merged)
// (evidence for human review only; nothing is compared or approved here).
const capture = Boolean(process.env.CAPTURE_SCREENSHOTS);
const out = path.resolve(__dirname, '../../docs/evidence/TAX-044');
const NEW_CLAIM = '/taxpayer/submissions/new';
const HEADING = 'Online Refunds simulator';

async function shot(page: Page, name: string, width: string) {
  if (!capture) return;
  await page.screenshot({ path: path.join(out, `${name}-${width}.png`), fullPage: true });
}

async function accessible(page: Page) {
  expect(await seriousAxeViolations(page)).toEqual([]);
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
}

async function open(page: Page): Promise<PortalMock> {
  const mock = await mockTaxpayerSession(page, 'taxpayer');
  await page.goto(TAXPAYER + NEW_CLAIM);
  await pageReady(page, HEADING);
  return mock;
}

const width = (project: string) => project.split('-')[1];

test.describe('VAT refund claim (TAX-043)', () => {
  test('valid submission: details, document upload, review and confirmation', async ({
    page,
  }, testInfo) => {
    const w = width(testInfo.project.name);
    const mock = await open(page);
    await expect(page.getByText('All fields are required unless marked optional.')).toBeVisible();
    await accessible(page);
    await shot(page, 'claim-details', w);

    await fillDetails(page);
    await accessible(page);
    await shot(page, 'documents-empty', w);

    const row = await upload(page);
    await expect(row.getByText('Uploaded')).toBeVisible();
    // Announced to screen readers through the polite live region.
    await expect(
      page.getByRole('status').filter({ hasText: 'synthetic-invoice-2026-09.pdf uploaded.' }),
    ).toBeAttached();
    await accessible(page);
    await shot(page, 'documents-uploaded', w);

    await toReview(page);
    // The claimed amount, as shown in the claim details on the review page.
    await expect(page.getByText('LSL 48,210.55').first()).toBeVisible();
    await expect(page.getByText('September 2026')).toBeVisible();
    await expect(page.getByText('More input VAT than output VAT')).toBeVisible();
    await accessible(page);
    await shot(page, 'review', w);

    await page
      .getByLabel('I declare that the information I have given is true and complete.')
      .check();
    await page.getByRole('button', { name: 'Send claim' }).click();
    await expect(page.getByRole('heading', { name: 'Claim sent' })).toBeFocused();
    await expect(page.getByText('REF-2026-')).toBeVisible();
    await expect(page.getByText('Submitted', { exact: true })).toBeVisible();
    await accessible(page);
    await shot(page, 'sent', w);

    expect(mock.claims.size).toBe(1);
    const [stored] = [...mock.claims.values()];
    expect(stored.body).toContain('"48210.55"');
    expect(stored.body).toContain(mock.evidence[0]);
  });

  test('required-field guidance: errors keep values and focus the first problem', async ({
    page,
  }, testInfo) => {
    const w = width(testInfo.project.name);
    await open(page);
    await page.getByLabel('Amount claimed (LSL)').fill('0');
    await chooseReason(page, 'Another reason');
    await page.getByRole('button', { name: 'Continue to documents' }).click();

    await expect(page.getByRole('alert').filter({ hasText: 'Fix 5 fields' })).toBeVisible();
    await expect(page.getByLabel('Tax period')).toBeFocused();
    await expect(page.getByText('The amount claimed must be more than 0.00.')).toBeVisible();
    await expect(page.getByText('Explain why you are claiming a refund.')).toBeVisible();
    await expect(page.getByLabel('Amount claimed (LSL)')).toHaveValue('0');
    await accessible(page);
    await shot(page, 'details-errors', w);

    // The declaration is required before sending.
    await page.getByLabel('Tax period').fill('2026-09');
    await page.getByLabel('Amount claimed (LSL)').fill('100');
    await page.getByLabel('Output VAT for the period (LSL)').fill('0');
    await page.getByLabel('Input VAT for the period (LSL)').fill('100');
    await page.getByLabel('Explanation').fill('Synthetic: equipment bought before trading began.');
    await page.getByRole('button', { name: 'Continue to documents' }).click();
    await page.getByRole('button', { name: 'Continue to invoices' }).click();
    await expect(page.getByText(/Upload at least one supporting document/).first()).toBeVisible();
    await expect(page.getByLabel('Choose a file to upload')).toBeFocused();
    await (await upload(page)).getByText('Uploaded').waitFor();
    await toReview(page);
    await page.getByRole('button', { name: 'Send claim' }).click();
    await expect(page.getByText('Confirm the declaration to send.')).toBeVisible();
    await expect(
      page.getByLabel('I declare that the information I have given is true and complete.'),
    ).toBeFocused();
    await shot(page, 'review-declaration-required', w);
  });

  test('duplicate retry: a lost response is resent safely and creates one claim', async ({
    page,
  }, testInfo) => {
    const w = width(testInfo.project.name);
    const mock = await open(page);
    await fillDetails(page);
    await (await upload(page)).getByText('Uploaded').waitFor();
    await toReview(page);
    await page
      .getByLabel('I declare that the information I have given is true and complete.')
      .check();

    mock.dropNextSendResponse = true;
    await page.getByRole('button', { name: 'Send claim' }).click();
    const unconfirmed = page
      .getByRole('alert')
      .filter({ hasText: 'We could not confirm that your claim was received' });
    await expect(unconfirmed).toBeVisible();
    await accessible(page);
    await shot(page, 'send-unconfirmed', w);

    await page.getByRole('button', { name: 'Send claim' }).click();
    await expect(page.getByRole('heading', { name: 'Claim sent' })).toBeVisible();
    await expect(page.getByText('No second claim was created.')).toBeVisible();
    await shot(page, 'sent-replayed', w);

    const sends = mock.calls.filter((c) => c === 'vati_tax.portal.api.submit_refund_claim');
    expect(sends).toHaveLength(2);
    expect(mock.claims.size).toBe(1);
  });

  test('invalid documents are refused with a reason and can be removed', async ({
    page,
  }, testInfo) => {
    const w = width(testInfo.project.name);
    const mock = await open(page);
    await fillDetails(page);
    const input = page.getByLabel('Choose a file to upload');

    // Refused in the browser: nothing is sent.
    await input.setInputFiles({
      name: 'tool.exe',
      mimeType: 'application/octet-stream',
      buffer: Buffer.from('MZ'),
    });
    // The visible error is tied to the input; the live region repeats it for screen readers.
    const fileError = page.locator('#document-file-error');
    await expect(fileError).toHaveText(/This file type cannot be used/);
    await expect(input).toHaveAttribute('aria-invalid', 'true');
    await expect(input).toHaveAttribute('aria-describedby', /document-file-error/);
    await expect(
      page.getByRole('status').filter({ hasText: 'tool.exe was not uploaded' }),
    ).toBeAttached();
    await input.setInputFiles({
      name: 'empty.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.alloc(0),
    });
    await expect(fileError).toHaveText('This file is empty. Choose the saved document again.');
    expect(mock.calls).not.toContain('vati_tax.evidence.api.upload_evidence');

    // Refused by the server after checking the content.
    const renamed = await upload(page, pdf('renamed-photo.pdf'));
    await expect(renamed.getByText('Not uploaded')).toBeVisible();
    await expect(renamed.getByText(/does not match its name/)).toBeVisible();
    const broken = await upload(page, pdf('broken-scan.pdf'));
    await expect(broken.getByText(/could not be read/)).toBeVisible();
    await accessible(page);
    await shot(page, 'documents-rejected', w);

    await page.getByRole('button', { name: 'Continue to invoices' }).click();
    await expect(
      page.getByRole('alert').filter({ hasText: 'Retry or remove the documents' }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Remove renamed-photo.pdf' }).click();
    await page.getByRole('button', { name: 'Remove broken-scan.pdf' }).click();
    await (await upload(page)).getByText('Uploaded').waitFor();
    await toReview(page);
    expect(mock.evidence).toHaveLength(1);
  });

  test("cross-scope evidence: the server's refusal sends the taxpayer back to documents", async ({
    page,
  }, testInfo) => {
    const w = width(testInfo.project.name);
    const mock = await open(page);
    await fillDetails(page);
    await (await upload(page)).getByText('Uploaded').waitFor();
    await toReview(page);
    await page
      .getByLabel('I declare that the information I have given is true and complete.')
      .check();

    mock.rejectEvidenceOnSend = true;
    await page.getByRole('button', { name: 'Send claim' }).click();
    await expect(page.getByRole('heading', { name: 'Supporting documents' })).toBeFocused();
    await expect(
      page.getByText('A document could not be found. Remove it and upload the file again.').first(),
    ).toBeVisible();
    expect(mock.claims.size).toBe(0);
    await accessible(page);
    await shot(page, 'evidence-not-found', w);
  });

  test('changed answers under the same key are refused as a conflict', async ({ page }) => {
    const mock = await open(page);
    await fillDetails(page);
    await (await upload(page)).getByText('Uploaded').waitFor();
    await toReview(page);
    await page
      .getByLabel('I declare that the information I have given is true and complete.')
      .check();
    mock.dropNextSendResponse = true;
    await page.getByRole('button', { name: 'Send claim' }).click();
    await expect(page.getByText('We could not confirm that your claim was received')).toBeVisible();

    // The first send was stored; changing the amount and resending must not create another.
    await page.getByRole('button', { name: 'Change claim details' }).click();
    await page.getByLabel('Amount claimed (LSL)').fill('50000');
    await page.getByRole('button', { name: 'Continue to documents' }).click();
    await toReview(page);
    await page.getByRole('button', { name: 'Send claim' }).click();
    await expect(
      page.getByRole('alert').filter({ hasText: 'already sent with different details' }),
    ).toBeVisible();
    expect(mock.claims.size).toBe(1);
  });

  test('denied access: a revoked membership cannot upload or send', async ({ page }) => {
    const mock = await open(page);
    await fillDetails(page);
    mock.persona = 'revoked';
    const row = await upload(page);
    await expect(row.getByText('You cannot upload documents for this taxpayer.')).toBeVisible();
    expect(mock.evidence).toHaveLength(0);
  });

  test('keyboard: the whole claim can be completed without a mouse', async ({ page }) => {
    await open(page);
    await page.getByLabel('Tax period').focus();
    await page.keyboard.type('2026-09');
    await page.keyboard.press('Tab');
    await page.keyboard.type('15000');
    // The reason combobox opens with Enter, moves with the arrow keys and selects with
    // Enter (listbox pattern); focus returns to the combobox.
    const reason = page.getByRole('combobox', { name: 'Reason for the claim' });
    await reason.focus();
    await page.keyboard.press('Enter');
    const first = page.getByRole('option', { name: 'Choose a reason' });
    const target = page.getByRole('option', { name: 'More input VAT than output VAT' });
    await expect(first).toBeFocused();
    // The frappe-ui (Reka) listbox can ignore a key pressed while it is still
    // opening (seen on CI at 390px). Press ArrowDown only while focus is still on
    // the first option, so a slow first press is never followed by an overshoot.
    await expect(async () => {
      const isFocused = (el: Element) => el === document.activeElement;
      if (!(await target.evaluate(isFocused)) && (await first.evaluate(isFocused))) {
        await page.keyboard.press('ArrowDown');
      }
      await expect(target).toBeFocused({ timeout: 1_000 });
    }).toPass({ timeout: 10_000 });
    await page.keyboard.press('Enter');
    await expect(reason).toContainText('More input VAT than output VAT');
    await expect(reason).toBeFocused();
    for (const [label, value] of [
      ['Output VAT for the period (LSL)', '0'],
      ['Input VAT for the period (LSL)', '15000'],
    ]) {
      await page.getByLabel(label).focus();
      await page.keyboard.type(value);
    }
    await page.getByLabel('Contact email (optional)').press('Enter');
    await expect(page.getByRole('heading', { name: 'Supporting documents' })).toBeFocused();

    await (await upload(page)).getByText('Uploaded').waitFor();
    await page.getByRole('button', { name: 'Continue to invoices' }).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'Purchase invoices' })).toBeFocused();
    const row = page.getByRole('group', { name: 'Invoice 1' });
    for (const [label, value] of [
      ['Invoice number', 'INV-1'],
      ['Invoice date', '2026-09-12'],
      ['Supplier', 'Synthetic Supplies'],
      ['Amount excluding VAT (LSL)', '100000'],
      ['VAT (LSL)', '15000'],
      ['Total including VAT (LSL)', '115000'],
    ]) {
      await field(row, label).focus();
      await page.keyboard.type(value);
    }
    await page.getByRole('button', { name: 'Continue to review' }).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'Check your claim' })).toBeFocused();
    await page
      .getByLabel('I declare that the information I have given is true and complete.')
      .focus();
    await page.keyboard.press('Space');
    await page.getByRole('button', { name: 'Send claim' }).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'Claim sent' })).toBeFocused();
  });
  test('pre-submission checks: figures and correction prompts, never accusations', async ({
    page,
  }, testInfo) => {
    const w = width(testInfo.project.name);
    const mock = await open(page);
    await fillDetails(page);
    await (await upload(page)).getByText('Uploaded').waitFor();
    await toInvoices(page);
    await shot(page, 'invoices-empty', w);
    // The claim (48,210.55) is more than this invoice's VAT, and no payment or contract.
    await fillInvoice(page, { 'VAT (LSL)': '40000.00', 'Total including VAT (LSL)': '361403.67' });
    await expect(page.getByLabel('Invoice totals')).toContainText('LSL 40,000.00');
    await accessible(page);
    await shot(page, 'invoices-filled', w);
    await page.getByRole('button', { name: 'Continue to review' }).click();
    const checks = page.getByRole('region', { name: 'Before you send' });
    await expect(checks.getByText('VAT on the invoices you listed', { exact: true })).toBeVisible();
    await expect(checks.getByText('LSL 8,210.55 more than the VAT')).toBeVisible();
    await expect(checks.getByText('Invoice 1: add the amount paid')).toBeVisible();
    await expect(checks).not.toContainText(/fraud|abuse|suspicious|penalt|risk|lane/i);
    expect(mock.reviews).toBe(1);
    await accessible(page);
    await shot(page, 'review-with-checks', w);
    // The panel itself, whole: page captures stop at the shell's scroll container.
    if (capture) await checks.screenshot({ path: path.join(out, `checks-panel-${w}.png`) });

    // A prompt links back to the invoice; fixing it clears the guidance.
    // Each Change button is described by its own prompt for screen readers.
    const change = checks.getByRole('button', { name: 'Change invoice 1' }).first();
    await expect(change).toHaveAccessibleDescription(/Invoice 1:/);
    await change.click();
    await expect(page.getByRole('heading', { name: 'Purchase invoices' })).toBeFocused();
    await fillInvoice(page, {
      'Amount paid (LSL, optional)': '369614.22',
    });
    await page.getByRole('button', { name: 'Continue to review' }).click();
    await expect(checks.getByText('Invoice 1: add the amount paid')).toHaveCount(0);
    await expect(checks.getByText(/if you have a contract or purchase order/)).toBeVisible();
    // Prompts never block sending.
    await page
      .getByLabel('I declare that the information I have given is true and complete.')
      .check();
    await page.getByRole('button', { name: 'Send claim' }).click();
    await expect(page.getByRole('heading', { name: 'Claim sent' })).toBeFocused();
    const [stored] = [...mock.claims.values()];
    expect(stored.body).toContain('INV-2026-0912');
  });

  test('invoice rows are validated and focus moves to the first problem', async ({
    page,
  }, testInfo) => {
    const w = width(testInfo.project.name);
    await open(page);
    await fillDetails(page);
    await (await upload(page)).getByText('Uploaded').waitFor();
    await toInvoices(page);
    await fillInvoice(page, { 'Total including VAT (LSL)': '1.00', 'Invoice date': '2026-10-02' });
    await page.getByRole('button', { name: 'Continue to review' }).click();
    const row = page.getByRole('group', { name: 'Invoice 1' });
    await expect(
      page.getByRole('alert').filter({ hasText: 'Invoices need attention' }),
    ).toBeVisible();
    await expect(
      row.getByText('The invoice date is after the end of the claim period.'),
    ).toBeVisible();
    await expect(row.getByText('must equal the total')).toBeVisible();
    await expect(field(row, 'Invoice date')).toBeFocused();
    await accessible(page);
    await shot(page, 'invoices-errors', w);
  });
});
