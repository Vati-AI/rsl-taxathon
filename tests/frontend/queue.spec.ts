import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import {
  hasVisibleFocus,
  pageReady,
  seriousAxeViolations,
  tabUntilFocused,
} from './support/checks';
import { AUDITOR } from './support/env';
import { BUDGET_VIEWPORTS, findComponentNames, measurePageLength } from './support/layout';
import { auditorCase, isMobile } from './support/matrix';
import { mockAuditorAccess } from './support/sessions';
import { sidebar } from './support/shell';

// TAX-147: officer home, case queue and taxpayer profile. Sidebar views (My cases,
// Awaiting evidence, Awaiting approval, Overdue) count the same case list the table
// shows; the queue keeps its view, filters, sort and page in the URL.
// CAPTURE_SCREENSHOTS=1 writes docs/evidence/TAX-147.

const QUEUE = `${AUDITOR}/auditor/cases`;
const CASES_API = /\/api\/v1\/cases(\?|$)/;
const VIEWS = ['My cases', 'Awaiting evidence', 'Awaiting approval', 'Overdue'] as const;

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
      `../../docs/evidence/TAX-147/${name}-${project.split('-')[1]}.png`,
    ),
    fullPage: true,
  });
}

async function checkPage(page: Page, project: string) {
  expect(await seriousAxeViolations(page)).toEqual([]);
  expect(await findComponentNames(page)).toEqual([]);
  if (project === 'desktop-1440') {
    const length = await measurePageLength(page);
    expect(length.viewports, 'page-length budget').toBeLessThanOrEqual(BUDGET_VIEWPORTS);
  }
}

const dataRows = (page: Page) =>
  page.getByRole('region', { name: /assigned cases$/ }).locator('tbody tr');

test.describe('case queue (TAX-147)', () => {
  test('each sidebar view count is the number of rows the view shows', async ({
    page,
  }, testInfo) => {
    await page.goto(QUEUE);
    await pageReady(page, 'My case queue');
    for (const view of VIEWS) {
      const nav = await sidebar(page, 'Auditor workspace');
      const link = nav.getByRole('link', { name: new RegExp(`^${view}`) });
      const label = (await link.textContent()) ?? '';
      const count = Number(label.replace(/\D+/g, ''));
      await link.click();
      await expect(page.getByRole('heading', { level: 1 })).toContainText(
        view === 'My cases' ? 'My case queue' : view,
      );
      if (count) await expect(dataRows(page)).toHaveCount(count);
      else await expect(page.getByText(/^No cases in /)).toBeVisible();
      await expect(page.locator('[data-kit="usage-line"]')).toContainText(`of ${count} cases`);
      await checkPage(page, testInfo.project.name);
      await shot(page, `view-${view.toLowerCase().replaceAll(' ', '-')}`, testInfo.project.name);
    }
  });

  test('one toolbar row; filters and sort survive a reload through the URL', async ({
    page,
  }, testInfo) => {
    await page.goto(QUEUE);
    await pageReady(page, 'My case queue');
    const toolbar = page.locator('[data-kit="table-toolbar"]');
    await expect(toolbar.getByRole('searchbox', { name: 'Search cases' })).toBeVisible();
    await expect(toolbar.getByRole('button', { name: 'Display options' })).toBeVisible();
    await expect(toolbar.getByRole('button', { name: 'Export CSV' })).toBeVisible();
    await expect(toolbar.getByRole('button', { name: 'Open first case' })).toBeVisible();
    if (testInfo.project.name === 'desktop-1440') {
      // One row at the desktop width the design standard defines; it wraps when narrower.
      const tops = await toolbar
        .locator('input, button')
        .evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top / 10)));
      expect(new Set(tops).size, 'toolbar is one row').toBe(1);
    }
    await toolbar.getByRole('searchbox', { name: 'Search cases' }).fill('Synthetic');
    await page
      .getByRole('columnheader', { name: /Refund claimed/ })
      .getByRole('button')
      .click();
    await expect(page).toHaveURL(/q=Synthetic/);
    await expect(page).toHaveURL(/sort=refund_claim/);
    const first = await dataRows(page).first().textContent();
    await page.reload();
    await pageReady(page, 'My case queue');
    await expect(page.getByRole('searchbox', { name: 'Search cases' })).toHaveValue('Synthetic');
    await expect(page.getByRole('columnheader', { name: /Refund claimed/ })).toHaveAttribute(
      'aria-sort',
      'ascending',
    );
    expect(await dataRows(page).first().textContent()).toBe(first);
    // Column help and the Open row action.
    await page.getByRole('button', { name: 'About Status' }).click();
    await expect(page.getByText('It describes processing, not an outcome.')).toBeVisible();
    await page.keyboard.press('Escape');
    const open = page.getByRole('button', { name: /^Open SYN-/ }).first();
    await tabUntilFocused(page, open, 80);
    expect(await hasVisibleFocus(open)).toBe(true);
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/auditor\/cases\/[0-9a-f-]{36}$/);
  });

  test('display options hide columns and export has exactly the visible columns', async ({
    page,
  }, testInfo) => {
    test.skip(isMobile(testInfo.project.name), 'download checked once at desktop and tablet');
    await page.goto(QUEUE);
    await pageReady(page, 'My case queue');
    await page.getByRole('button', { name: 'Display options' }).click();
    await page.getByRole('checkbox', { name: 'Why selected' }).uncheck();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('columnheader', { name: 'Why selected' })).toHaveCount(0);
    await shot(page, 'display-options', testInfo.project.name);
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: 'Export CSV' }).click(),
    ]);
    const csv = readFileSync((await download.path())!, 'utf8');
    const header = csv.split('\r\n')[0];
    expect(header.startsWith('Case,Taxpayer,Status')).toBe(true);
    expect(header).not.toContain('Why selected');
    expect(csv.trim().split('\r\n')).toHaveLength(1 + (await dataRows(page).count()));
    // The choice is kept in this browser.
    await page.reload();
    await pageReady(page, 'My case queue');
    await expect(page.getByRole('columnheader', { name: 'Why selected' })).toHaveCount(0);
  });

  test('loading, empty, error and denied states', async ({ page }, testInfo) => {
    let release: () => void = () => {};
    const held = new Promise<void>((r) => (release = r));
    await page.route(CASES_API, async (route) => {
      await held;
      await route.continue();
    });
    await page.goto(QUEUE);
    await expect(page.getByText('Loading your cases…')).toBeVisible();
    await shot(page, 'state-loading', testInfo.project.name);
    release();
    await expect(dataRows(page).first()).toBeVisible();
    await page.unroute(CASES_API);

    await page.goto(`${QUEUE}?q=no-such-case`);
    await expect(page.getByText('No cases match these filters')).toBeVisible();
    await checkPage(page, testInfo.project.name);
    await shot(page, 'state-empty', testInfo.project.name);

    await page.route(CASES_API, (route) =>
      route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'upstream_unavailable',
            message: 'Unavailable',
            retryable: true,
            correlation_id: 'c0000000-0000-4000-8000-000000000147',
            field_errors: [],
          },
        }),
      }),
    );
    await page.goto(QUEUE);
    await expect(page.getByRole('button', { name: /try again|retry/i }).first()).toBeVisible();
    await shot(page, 'state-error', testInfo.project.name);
    await page.unroute(CASES_API);

    await mockAuditorAccess(page, 'denied');
    await page.goto(QUEUE);
    await expect(page.getByText(/do not have access|not allowed/i).first()).toBeVisible();
    await shot(page, 'state-denied', testInfo.project.name);
  });
});

test.describe('officer home and taxpayer profile (TAX-147)', () => {
  test('overview lists action items whose counts match the queue views', async ({
    page,
  }, testInfo) => {
    await page.goto(`${AUDITOR}/auditor/`);
    await pageReady(page, 'Overview');
    const items = page.locator('[data-kit="action-item"]');
    await expect(items).toHaveCount(4);
    const overdue = items.filter({ hasText: 'Overdue' });
    await expect(overdue).toContainText('SYN-004');
    await overdue.getByRole('link', { name: /^View all/ }).click();
    await expect(page).toHaveURL(/view=overdue/);
    await expect(dataRows(page)).toHaveCount(1);
    await page.goBack();
    await pageReady(page, 'Overview');
    await checkPage(page, testInfo.project.name);
    await shot(page, 'overview', testInfo.project.name);
  });

  test('taxpayer profile: key facts and an open-cases table', async ({ page }, testInfo) => {
    const syn002 = auditorCase('SYN-002');
    await page.goto(`${AUDITOR}/auditor/taxpayers/${syn002.taxpayer_id}`);
    await pageReady(page, 'Synthetic Border Retail');
    await expect(page.locator('[data-kit="key-facts"]')).toContainText('Taxpayer ID number');
    const table = page.getByRole('region', { name: 'Open cases you can view' });
    await expect(table.getByRole('link', { name: 'SYN-002' })).toBeVisible();
    await checkPage(page, testInfo.project.name);
    await shot(page, 'taxpayer', testInfo.project.name);
    await page.getByRole('button', { name: 'Open SYN-002' }).click();
    await pageReady(page, 'SYN-002 · Synthetic Border Retail');
  });
});
