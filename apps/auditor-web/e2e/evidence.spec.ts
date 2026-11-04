import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';

// TAX-041: evidence viewer and relationship view, against the fixture server's
// synthetic case records (integration/api/fixtures/case_records.json).
const CASE_2 = '40000000-0000-4000-8000-000000000002';
const CASE_7 = '40000000-0000-4000-8000-000000000007';
const OWNERSHIP_FINDING = '70000000-0000-4000-8000-000000000022';
const ANNUAL_RETURN = '50000000-0000-4000-8000-000000000026';
const OLD_SCAN = '50000000-0000-4000-8000-000000000024';
const OTHER_TENANT = '10000000-0000-4000-8000-000000000002';
// Words that would state or imply a conclusion about the taxpayer.
const ACCUSING = /fraud|guilt|evasion|evade|suspicious|offence|wrongdoing|abuse/i;

const asOtherTenant = (route: Route) =>
  route.continue({ headers: { ...route.request().headers(), 'x-vati-tenant-id': OTHER_TENANT } });

async function openTab(page: Page, name: 'Evidence' | 'Relationships' | 'Findings') {
  await page.goto(`/auditor/cases/${CASE_2}`);
  await page.getByRole('tab', { name }).click();
}

async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
    .analyze();
  const serious = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  );
  expect(serious.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
}

test.describe('finding to source', () => {
  test('a cited page opens the stored document record and leads back', async ({ page }) => {
    await openTab(page, 'Findings');
    await expect(page).toHaveURL(/tab=findings/);
    const conflict = page.getByRole('article', { name: 'Records disagree: Related party' });
    await expect(conflict).toContainText('60% in the company register extract');
    await conflict.getByRole('link', { name: 'Financial statement v1 · Page 3' }).click();

    await expect(page).toHaveURL(
      new RegExp(`/cases/${CASE_2}/evidence/${ANNUAL_RETURN}\\?page=3&finding=`),
    );
    await expect(
      page.getByRole('heading', { level: 1, name: 'Financial statement v1' }),
    ).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Cited location: Page 3' })).toBeVisible();
    await expect(page.getByText('2f'.repeat(32))).toBeVisible();
    await expect(page.getByText('Page 3 (the location you followed)')).toBeVisible();
    await expect(page.getByText('Document content is never shown')).toBeVisible();

    await page.getByRole('link', { name: 'Back to the finding' }).click();
    await expect(page).toHaveURL(new RegExp(`tab=findings&finding=${OWNERSHIP_FINDING}`));
    await expect(page.locator(`#finding-${OWNERSHIP_FINDING}`)).toBeFocused();
  });

  test('a record locator is shown for spreadsheet evidence, by keyboard only', async ({ page }) => {
    await openTab(page, 'Findings');
    const link = page.getByRole('link', { name: 'Other document v1 · Record row 4' });
    await link.focus();
    await expect(link).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'Cited location: Record row 4' })).toBeVisible();
  });

  test('discrepancies and missing evidence are separate groups', async ({ page }) => {
    await openTab(page, 'Findings');
    await expect(page.getByRole('heading', { name: 'Records disagree (1)' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Evidence missing (1)' })).toBeVisible();
    const missing = page.getByRole('article', { name: 'Evidence missing: Refund reconciliation' });
    await expect(missing).toContainText('LSL 15,000.00');
    await expect(missing).toContainText('Needs officer approval');
    await expect(page.getByRole('main')).not.toContainText(ACCUSING);
  });
});

test.describe('relationships', () => {
  test('uncertain ownership is marked without implying guilt', async ({ page }) => {
    await openTab(page, 'Relationships');
    const table = page.getByRole('region', { name: 'Ownership records' });
    // The 60% and 55% records disagree, and with the 40% holding the stakes exceed 100%:
    // all three rows cite records named by the unresolved finding.
    await expect(table.getByText('Uncertain: records disagree')).toHaveCount(3);
    // Every recorded stake is shown as recorded; none is picked or corrected.
    for (const stake of ['60%', '55%', '40%'])
      await expect(table.getByRole('cell', { name: stake, exact: true })).toBeVisible();
    await expect(page.getByText('Neither figure has been chosen or corrected')).toBeVisible();
    await expect(page.getByRole('main')).not.toContainText(ACCUSING);

    await table.getByRole('link', { name: 'See the unresolved finding' }).first().click();
    await expect(page).toHaveURL(new RegExp(`finding=${OWNERSHIP_FINDING}`));
    await expect(page.locator(`#finding-${OWNERSHIP_FINDING}`)).toBeFocused();
  });

  test('match candidates are proposals and missing records are flagged', async ({ page }) => {
    await openTab(page, 'Relationships');
    for (const label of ['Unreviewed match', 'Confirmed by reviewer', 'Rejected by reviewer'])
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    await expect(page.getByText('A similarity score is not identity').first()).toBeVisible();
    const payments = page.getByRole('region', { name: 'Payment records' });
    await expect(payments.getByText('Record not on file')).toHaveCount(1);
    await payments.getByRole('link', { name: 'Invoice v2' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Invoice v2' })).toBeVisible();
    await expectAccessible(page);
  });
});

test.describe('evidence viewer', () => {
  test('superseded versions and failed extraction are explicit', async ({ page }) => {
    await openTab(page, 'Evidence');
    const table = page.getByRole('region', { name: 'Evidence on file' });
    await expect(table.getByText('Replaced by v2')).toBeVisible();
    await expect(table.getByText('Extraction failed')).toBeVisible();
    await page.goto(`/auditor/cases/${CASE_2}/evidence/${OLD_SCAN}`);
    await expect(page.getByText('A newer version of this document exists')).toBeVisible();
    await expect(page.getByText('Text could not be extracted from this document')).toBeVisible();
    await page.getByRole('link', { name: 'v2' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Invoice v2' })).toBeVisible();
    await expectAccessible(page);
  });

  test('empty, unknown and denied states', async ({ page }) => {
    await page.goto(`/auditor/cases/${CASE_7}?tab=findings`);
    await expect(page.getByText('No findings yet')).toBeVisible();
    await page.getByRole('tab', { name: 'Evidence' }).click();
    await expect(page.getByText('No evidence on file')).toBeVisible();

    await page.goto(`/auditor/cases/${CASE_2}/evidence/50000000-0000-4000-8000-000000000099`);
    await expect(page.getByText('Evidence not found')).toBeVisible();

    await page.route(/\/api\/v1\/cases\/[^/]+\/(evidence|entities|findings)/, asOtherTenant);
    await page.goto(`/auditor/cases/${CASE_2}?tab=relationships`);
    await expect(page.getByText('You do not have access')).toBeVisible();
    await expect(page.getByRole('region', { name: 'Ownership records' })).toHaveCount(0);
  });
});

test.describe('accessibility', () => {
  const loaded = {
    evidence: 'Evidence on file',
    relationships: 'Ownership records',
    findings: null,
  } as const;
  for (const [tab, region] of Object.entries(loaded)) {
    test(`no serious or critical axe violations on the ${tab} tab`, async ({ page }) => {
      await page.goto(`/auditor/cases/${CASE_2}?tab=${tab}`);
      if (region) await expect(page.getByRole('region', { name: region })).toBeVisible();
      else await expect(page.getByRole('heading', { name: 'Records disagree (1)' })).toBeVisible();
      await expectAccessible(page);
    });
  }
});
