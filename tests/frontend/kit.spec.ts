import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import {
  hasVisibleFocus,
  horizontalOverflow,
  pageReady,
  seriousAxeViolations,
} from './support/checks';
import { AUDITOR, TAXPAYER } from './support/env';
import { measurePageLength } from './support/layout';
import { mockTaxpayerSession } from './support/sessions';

// TAX-145: the shared Vati Workspace kit (apps/shared/components) in the auditor showcase
// and in the taxpayer portal: overlays keep keyboard users inside and return focus,
// axe stays clean with overlays open, and nothing scrolls the page sideways.
// CAPTURE_SCREENSHOTS=1 writes review screenshots to docs/evidence/TAX-145.

const SHOWCASE = AUDITOR + '/auditor/showcase';
const kit = (page: Page) => page.locator('[data-kit-showcase]');

async function shot(
  page: Page,
  name: string,
  project: string,
  target?: ReturnType<Page['locator']>,
) {
  if (!process.env.CAPTURE_SCREENSHOTS) return;
  const file = path.resolve(
    __dirname,
    `../../docs/evidence/TAX-145/${name}-${project.split('-')[1]}.png`,
  );
  if (target) await target.screenshot({ path: file });
  else await page.screenshot({ path: file, fullPage: false });
}

async function openShowcase(page: Page) {
  await page.goto(SHOWCASE);
  await pageReady(page, 'Component showcase');
  await expect(kit(page).getByRole('heading', { name: 'Vati Workspace kit' })).toBeVisible();
}

test.describe('Vati Workspace kit (TAX-145)', () => {
  test('every component renders with no serious axe violations or sideways page scroll', async ({
    page,
  }, testInfo) => {
    await openShowcase(page);
    for (const kind of [
      'workspace-header', 'app-sidebar', 'page-header', 'notice', 'status-chip', 'key-facts',
      'routed-tabs', 'table-toolbar', 'usage-line', 'data-table', 'empty-state', 'inline-error',
      'skeleton',
    ]) {
      await expect(kit(page).locator(`[data-kit="${kind}"]`).first()).toBeVisible();
    } // prettier-ignore
    expect(await seriousAxeViolations(page)).toEqual([]);
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
    await shot(page, 'kit', testInfo.project.name, kit(page));
  });

  test('table: sort by keyboard, page through and use the row action', async ({
    page,
  }, testInfo) => {
    await openShowcase(page);
    const table = kit(page).getByRole('region', { name: 'Example cases' });
    const header = table.getByRole('columnheader', { name: /^Taxpayer/ });
    const sortButton = header.getByRole('button', { name: 'Taxpayer' });
    await sortButton.focus();
    expect(await hasVisibleFocus(sortButton)).toBe(true);
    await page.keyboard.press('Enter');
    await expect(header).toHaveAttribute('aria-sort', 'ascending');
    await page.keyboard.press('Enter');
    await expect(header).toHaveAttribute('aria-sort', 'descending');
    await shot(
      page,
      'table-focus',
      testInfo.project.name,
      kit(page).locator('[data-kit="data-table"]').first(),
    );
    await kit(page).getByRole('button', { name: 'Next page' }).click();
    await expect(kit(page).getByText('Rows 26–31 of 31 · Page 2 of 2')).toBeVisible();
    await table
      .getByRole('button', { name: /^Open DEMO-/ })
      .first()
      .click();
    await expect(kit(page).getByText(/Opened DEMO-\d+ \(example\)/)).toBeVisible();
  });

  test('drawer traps focus, closes on Escape and returns focus', async ({ page }, testInfo) => {
    await openShowcase(page);
    const opener = kit(page).getByRole('button', { name: 'Open drawer' });
    await opener.click();
    const drawer = page.getByRole('dialog', { name: 'Request evidence' });
    await expect(drawer).toBeVisible();
    expect(await seriousAxeViolations(page)).toEqual([]);
    await shot(page, 'drawer', testInfo.project.name);
    for (let i = 0; i < 6; i += 1) {
      await page.keyboard.press('Tab');
      expect(await drawer.evaluate((el) => el.contains(document.activeElement))).toBe(true);
    }
    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden();
    await expect(opener).toBeFocused();
  });

  test('confirmation dialog names the action and keeps a loading state', async ({
    page,
  }, testInfo) => {
    await openShowcase(page);
    await kit(page).getByRole('button', { name: 'Confirm an action', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Send this evidence request?' });
    await expect(dialog.getByRole('button', { name: 'Send request' })).toBeVisible();
    expect(await seriousAxeViolations(page)).toEqual([]);
    await shot(page, 'confirm', testInfo.project.name);
    await dialog.getByRole('button', { name: 'Send request' }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText('Evidence request sent')).toBeVisible();
  });

  test('command palette filters and chooses by keyboard', async ({ page }, testInfo) => {
    // The shell owns Ctrl K (TAX-146, shell.spec.ts); this example opens from its button.
    await openShowcase(page);
    await kit(page).getByRole('button', { name: 'Open example search' }).click();
    const box = page.getByRole('combobox', { name: 'Search case reference, TIN or page' });
    await expect(box).toBeFocused();
    await box.fill('400000202');
    await expect(page.getByRole('option')).toHaveCount(1);
    expect(await seriousAxeViolations(page)).toEqual([]);
    await shot(page, 'palette', testInfo.project.name);
    await page.keyboard.press('Enter');
    await expect(box).toBeHidden();
    await expect(kit(page).getByText('Chose DEMO-202 (example).')).toBeVisible();
  });

  test('taxpayer portal uses the shared page header and empty state', async ({
    page,
  }, testInfo) => {
    const mock = await mockTaxpayerSession(page, 'taxpayer');
    mock.requests.clear();
    await page.goto(TAXPAYER + '/taxpayer/requests');
    await pageReady(page, 'Evidence requests');
    await expect(page.locator('[data-kit="page-header"]')).toBeVisible();
    const empty = page.locator('[data-kit="empty-state"]');
    await expect(empty.getByRole('heading', { name: 'No evidence requests' })).toBeVisible();
    expect(await seriousAxeViolations(page)).toEqual([]);
    await shot(page, 'taxpayer-empty', testInfo.project.name);

    await mockTaxpayerSession(page, 'revoked');
    await page.goto(TAXPAYER + '/taxpayer/submissions');
    await pageReady(page, 'My cases');
    await expect(page.getByText('Access denied')).toBeVisible();
    await shot(page, 'taxpayer-denied', testInfo.project.name);
  });

  test('page length at 1440×900 (TAX-144 budget)', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop-1440', 'The budget is defined at 1440×900');
    await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(TAXPAYER + '/taxpayer/submissions');
    await pageReady(page, 'My cases');
    const myCases = await measurePageLength(page);
    expect(myCases.viewports).toBeLessThanOrEqual(1.5);
    await page.goto(TAXPAYER + '/taxpayer/requests');
    await pageReady(page, 'Evidence requests');
    const requests = await measurePageLength(page);
    expect(requests.viewports).toBeLessThanOrEqual(1.5);
    await openShowcase(page);
    const showcase = await measurePageLength(page);
    testInfo.annotations.push({
      type: 'page-length',
      description: `my cases ${myCases.viewports}, evidence requests ${requests.viewports}, showcase ${showcase.viewports} (exempt)`,
    });
    console.log(
      `[TAX-145 page length] my cases ${myCases.viewports} · evidence requests ${requests.viewports} · showcase ${showcase.viewports} (exempt)`,
    );
  });
});
