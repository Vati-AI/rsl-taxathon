import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { pageReady, seriousAxeViolations } from './support/checks';
import { AUDITOR } from './support/env';
import { BUDGET_VIEWPORTS, findComponentNames, measurePageLength } from './support/layout';
import { auditorCase, isMobile } from './support/matrix';
import { mockAuditorAccess } from './support/sessions';

// TAX-148: the case workspace. One header (title, status chip, key facts), routed tabs
// (Summary, Evidence, Relationships, Findings, Review) with their own URLs, an evidence
// list with a preview pane, and review actions in right-side drawers whose confirmation
// dialogs are unchanged. CAPTURE_SCREENSHOTS=1 writes docs/evidence/TAX-148.

const syn002 = auditorCase('SYN-002');
const syn007 = auditorCase('SYN-007');
const CASE = `${AUDITOR}/auditor/cases/${syn002.case_id}`;
const TITLE = 'SYN-002 · Synthetic Border Retail';
const ANNUAL_RETURN = '50000000-0000-4000-8000-000000000026';
const RECORDS = /\/api\/v1\/cases\/[^/]+\/(evidence|entities|findings)/;

async function shot(page: Page, name: string, project: string) {
  if (!process.env.CAPTURE_SCREENSHOTS) return;
  // Let dialog and drawer transitions finish so the picture shows the settled state.
  await page.waitForFunction(() =>
    document
      .getAnimations()
      // Infinite animations (the loading skeleton's pulse) never settle.
      .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
      .every((a) => a.playState !== 'running'),
  );
  await page.waitForTimeout(200);
  await page.screenshot({
    path: path.resolve(
      __dirname,
      `../../docs/evidence/TAX-148/${name}-${project.split('-')[1]}.png`,
    ),
    fullPage: true,
  });
}

const tabs = (page: Page) => page.getByRole('navigation', { name: 'Case sections' });
const tab = (page: Page, name: string) =>
  tabs(page).getByRole('link', { name: new RegExp(`^${name}`) });

async function checkPage(page: Page, testInfo: { project: { name: string } }) {
  expect(await seriousAxeViolations(page)).toEqual([]);
  expect(await findComponentNames(page)).toEqual([]);
  if (testInfo.project.name === 'desktop-1440') {
    const length = await measurePageLength(page);
    expect(length.viewports, 'page-length budget').toBeLessThanOrEqual(BUDGET_VIEWPORTS);
  }
}

test.describe('case workspace (TAX-148)', () => {
  test('each tab has its own URL inside one header with status and key facts', async ({
    page,
  }, testInfo) => {
    const urls = {
      Summary: new RegExp(`/cases/${syn002.case_id}$`),
      Evidence: /\/evidence$/,
      Relationships: /\/relationships$/,
      Findings: /\/findings$/,
      Review: /\/review$/,
    } as const;
    await page.goto(CASE);
    await pageReady(page, TITLE);
    await expect(page.locator('[data-kit="key-facts"]')).toContainText('Refund claimed');
    await expect(page.locator('[data-kit="page-header"]')).toContainText('Investigating');
    for (const [name, url] of Object.entries(urls)) {
      await tab(page, name).click();
      await expect(page).toHaveURL(url);
      await expect(tab(page, name)).toHaveAttribute('aria-current', 'page');
      await expect(page.getByRole('region', { name, exact: true })).toBeVisible();
      await page.waitForLoadState('networkidle');
      await checkPage(page, testInfo);
      await shot(page, `tab-${name.toLowerCase()}`, testInfo.project.name);
    }
    // Back returns to the previous tab, because each tab is a history entry.
    await page.goBack();
    await expect(page).toHaveURL(urls.Findings);
  });

  test('evidence list beside a preview pane, and the open document has its own URL', async ({
    page,
  }, testInfo) => {
    await page.goto(`${CASE}/evidence/${ANNUAL_RETURN}`);
    await expect(
      page.getByRole('heading', { level: 2, name: 'Financial statement v1' }),
    ).toBeVisible();
    const list = page.getByRole('list', { name: 'Evidence on file' });
    const preview = page.getByRole('region', { name: 'Evidence preview' });
    if (isMobile(testInfo.project.name)) {
      // Below 1024px the list and the preview take turns.
      await expect(list).toBeHidden();
      await preview.getByRole('link', { name: 'All evidence' }).click();
      await expect(list).toBeVisible();
    } else {
      await expect(list.locator('a[aria-current="page"]')).toContainText('Financial statement v1');
      const listBox = (await list.boundingBox())!;
      const previewBox = (await preview.boundingBox())!;
      expect(previewBox.x).toBeGreaterThan(listBox.x + listBox.width - 1);
    }
    await checkPage(page, testInfo);
    await shot(page, 'evidence-preview', testInfo.project.name);
  });

  test('propose and request evidence open in drawers; the confirmation is unchanged', async ({
    page,
  }, testInfo) => {
    await page.goto(`${CASE}/review`);
    await pageReady(page, 'Review and decide · SYN-002');
    // The review tab no longer stacks forms: no form is on the page until asked for.
    await expect(page.getByRole('form')).toHaveCount(0);
    await checkPage(page, testInfo);
    await shot(page, 'review', testInfo.project.name);

    const propose = page.getByRole('button', { name: 'Propose a decision…' });
    await propose.focus();
    await page.keyboard.press('Enter');
    const drawer = page.getByRole('dialog', { name: 'Propose a decision' });
    await expect(drawer).toBeVisible();
    await expect
      .poll(() => drawer.evaluate((el) => el.contains(document.activeElement)))
      .toBe(true);
    expect(await seriousAxeViolations(page)).toEqual([]);
    await shot(page, 'drawer-propose', testInfo.project.name);
    await page.keyboard.press('Escape');
    await expect(drawer).toHaveCount(0);
    await expect(propose).toBeFocused();

    // A suggestion opens the evidence-request drawer prefilled; nothing is sent from it.
    await page
      .getByRole('listitem')
      .filter({ hasText: 'No synthetic service agreement' })
      .getByRole('button', { name: 'Use as a starting point' })
      .click();
    const request = page.getByRole('dialog', { name: 'Request evidence from the taxpayer' });
    await expect(request.getByRole('form', { name: 'Request evidence' })).toBeVisible();
    await expect(
      request
        .getByRole('group', { name: 'Findings that justify the request' })
        .getByRole('checkbox', { checked: true })
        .first(),
    ).toBeVisible();
    await request.getByLabel('Document 1 description').fill('Synthetic: signed agreement.');
    await request
      .getByLabel('Message to the taxpayer')
      .fill('Synthetic: please upload the signed agreement.');
    await request.getByRole('button', { name: 'Review request…' }).click();
    const confirm = page.getByRole('dialog', { name: 'Confirm the evidence request' });
    await expect(confirm).toContainText('It does not change the case status');
    await shot(page, 'drawer-confirm', testInfo.project.name);
    // Escape closes only the confirmation; the drawer keeps the draft.
    await page.keyboard.press('Escape');
    await expect(confirm).toHaveCount(0);
    await expect(request.getByLabel('Document 1 description')).toHaveValue(
      'Synthetic: signed agreement.',
    );
  });

  test('loading, empty, error and denied states', async ({ page }, testInfo) => {
    // Loading: hold the case records.
    let release: () => void = () => {};
    const held = new Promise<void>((r) => (release = r));
    await page.route(RECORDS, async (route) => {
      await held;
      await route.continue();
    });
    await page.goto(`${CASE}/evidence`);
    await expect(page.getByRole('heading', { level: 1, name: TITLE })).toBeVisible();
    await expect(page.getByRole('status', { name: 'Loading evidence' })).toBeVisible();
    await shot(page, 'state-loading', testInfo.project.name);
    release();
    await expect(page.getByRole('list', { name: 'Evidence on file' })).toBeVisible();
    await page.unroute(RECORDS);

    // Empty: a case with no evidence yet.
    await page.goto(`${AUDITOR}/auditor/cases/${syn007.case_id}/evidence`);
    await expect(page.getByText('No evidence on file')).toBeVisible();
    await checkPage(page, testInfo);
    await shot(page, 'state-empty', testInfo.project.name);

    // Error: the records service fails; the header and tabs stay.
    await page.route(RECORDS, (route) =>
      route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'upstream_unavailable',
            message: 'Unavailable',
            retryable: true,
            correlation_id: 'c0000000-0000-4000-8000-000000000148',
            field_errors: [],
          },
        }),
      }),
    );
    await page.goto(`${CASE}/findings`);
    await pageReady(page, TITLE);
    await expect(page.getByRole('button', { name: /try again|retry/i })).toBeVisible();
    await expect(tabs(page)).toBeVisible();
    await shot(page, 'state-error', testInfo.project.name);
    await page.unroute(RECORDS);
  });

  test('a denied officer sees the denied state', async ({ page }, testInfo) => {
    await mockAuditorAccess(page, 'denied');
    await page.goto(`${CASE}/review`);
    await expect(
      page.getByText(/do not have access|not allowed|cannot open/i).first(),
    ).toBeVisible();
    expect(await findComponentNames(page)).toEqual([]);
    await shot(page, 'state-denied', testInfo.project.name);
  });
});
