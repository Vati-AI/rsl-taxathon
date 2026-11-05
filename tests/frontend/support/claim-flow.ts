import { expect, type Locator, type Page } from '@playwright/test';

/**
 * Taxpayer VAT refund claim steps (TAX-043 form, TAX-044 invoice schedule),
 * shared by claim.spec.ts and the TAX-055 end-to-end journeys. Defaults are the
 * synthetic claim claim.spec.ts has always used.
 */

/** A tiny synthetic PDF-looking upload; the mock judges files by name only. */
export function pdf(name = 'synthetic-invoice-2026-09.pdf') {
  return { name, mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n% synthetic\n') };
}

/** frappe-ui 1.1.0 renders the select as a combobox with a listbox, not a native <select>. */
export async function chooseReason(page: Page, label: string) {
  await page.getByRole('combobox', { name: 'Reason for the claim' }).click();
  await page.getByRole('option', { name: label }).click();
  await expect(page.getByRole('combobox', { name: 'Reason for the claim' })).toContainText(label);
}

export interface ClaimDetails {
  period: string;
  claimed: string;
  outputVat: string;
  inputVat: string;
}

export const DEFAULT_DETAILS: ClaimDetails = {
  period: '2026-09',
  claimed: '48,210.55',
  outputVat: '0',
  inputVat: '48210.55',
};

export async function fillDetails(page: Page, details: Partial<ClaimDetails> = {}) {
  const d = { ...DEFAULT_DETAILS, ...details };
  await page.getByLabel('Tax period').fill(d.period);
  await page.getByLabel('Amount claimed (LSL)').fill(d.claimed);
  await page.getByLabel('Output VAT for the period (LSL)').fill(d.outputVat);
  await page.getByLabel('Input VAT for the period (LSL)').fill(d.inputVat);
  await chooseReason(page, 'More input VAT than output VAT');
  await page.getByLabel('Contact email (optional)').fill('accounts@example.test');
  await page.getByRole('button', { name: 'Continue to documents' }).click();
  await expect(page.getByRole('heading', { name: 'Supporting documents' })).toBeFocused();
}

export async function upload(page: Page, file = pdf()) {
  await page.getByLabel('Choose a file to upload').setInputFiles(file);
  return page
    .getByRole('list', { name: 'Supporting documents' })
    .getByRole('listitem')
    .filter({ has: page.getByText(file.name, { exact: true }) });
}

/** A row field by its visible label; frappe-ui appends " (required)" to required ones. */
export function field(row: Locator, label: string) {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // Match the accessible name, which frappe-ui computes as "<label> (required)".
  return row.getByRole('textbox', { name: new RegExp(`^${escaped}( \\(required\\))?$`) });
}

export const DEFAULT_INVOICE: Record<string, string> = {
  'Invoice number': 'INV-2026-0912',
  'Invoice date': '2026-09-12',
  Supplier: 'Synthetic Highland Supplies',
  'Amount excluding VAT (LSL)': '321403.67',
  'VAT (LSL)': '48210.55',
  'Total including VAT (LSL)': '369614.22',
};

/** TAX-044: one purchase invoice behind the claim. */
export async function fillInvoice(page: Page, overrides: Record<string, string> = {}) {
  const row = page.getByRole('group', { name: 'Invoice 1' });
  for (const [label, value] of Object.entries({ ...DEFAULT_INVOICE, ...overrides })) {
    await field(row, label).fill(value);
  }
}

export async function toInvoices(page: Page) {
  await page.getByRole('button', { name: 'Continue to invoices' }).click();
  await expect(page.getByRole('heading', { name: 'Purchase invoices' })).toBeFocused();
}

export async function toReview(page: Page, invoice: Record<string, string> = {}) {
  await toInvoices(page);
  await fillInvoice(page, invoice);
  await page.getByRole('button', { name: 'Continue to review' }).click();
  await expect(page.getByRole('heading', { name: 'Check your claim' })).toBeFocused();
  await expect(page.getByRole('heading', { name: 'Before you send' })).toBeVisible();
}

export const DECLARATION = 'I declare that the information I have given is true and complete.';
