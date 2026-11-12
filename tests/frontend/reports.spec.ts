import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { hasVisibleFocus, pageReady, seriousAxeViolations } from './support/checks';
import { AUDITOR } from './support/env';
import { BUDGET_VIEWPORTS, findComponentNames, measurePageLength } from './support/layout';

// TAX-149: Reports, the management dashboard and integrity review as routed tabs under
// one reporting window. Every figure comes from the existing summary APIs; only a
// supervisor gets any. CAPTURE_SCREENSHOTS=1 writes docs/evidence/TAX-149.

const BASE = `${AUDITOR}/auditor/management`;
const SUMMARY = /\/api\/v1\/management\/summary/;
const TABS = [
  ['Workload', '', 'Management dashboard'],
  ['Timeliness', '/timeliness', 'Management dashboard'],
  ['Evidence', '/evidence', 'Management dashboard'],
  ['Outcomes', '/outcomes', 'Management dashboard'],
  ['Ledger assurance', '/assurance', 'Management dashboard'],
  ['Integrity', '/integrity', 'Integrity review'],
] as const;

function actAs(page: Page, role: 'auditor' | 'specialist' | 'supervisor') {
  return page.addInitScript((r) => localStorage.setItem('vati.fixtureRole', r), role);
}

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
      `../../docs/evidence/TAX-149/${name}-${project.split('-')[1]}.png`,
    ),
    fullPage: true,
  });
}

const tabs = (page: Page) => page.getByRole('navigation', { name: 'Report sections' });

test.describe('Reports (TAX-149)', () => {
  test('every tab has its own URL, fits the budget and shows only measured figures', async ({
    page,
  }, testInfo) => {
    await actAs(page, 'supervisor');
    for (const [label, suffix, heading] of TABS) {
      await page.goto(BASE + suffix);
      await pageReady(page, heading);
      await expect(tabs(page).getByRole('link', { name: label })).toHaveAttribute(
        'aria-current',
        'page',
      );
      await expect(page.locator('[data-kit="report-panel"]')).toHaveAttribute('aria-label', label);
      await expect(page.getByTestId('window-line').first()).toBeVisible();
      expect(await seriousAxeViolations(page)).toEqual([]);
      expect(await findComponentNames(page)).toEqual([]);
      if (testInfo.project.name === 'desktop-1440') {
        const length = await measurePageLength(page);
        expect(length.viewports, `${label} page length`).toBeLessThanOrEqual(BUDGET_VIEWPORTS);
      }
      await shot(page, `tab-${label.toLowerCase().replace(' ', '-')}`, testInfo.project.name);
    }
  });

  test('the tabs keep the reporting window and work by keyboard', async ({ page }) => {
    await actAs(page, 'supervisor');
    await page.goto(`${BASE}?window=7`);
    await pageReady(page, 'Management dashboard');
    const outcomes = tabs(page).getByRole('link', { name: 'Outcomes' });
    await outcomes.focus();
    expect(await hasVisibleFocus(outcomes)).toBe(true);
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/management\/outcomes\?window=7$/);
    await expect(page.getByTestId('window-line')).toContainText('(7 days)');
    await page.goBack();
    await expect(page).toHaveURL(/\/management\?window=7$/);
  });

  test('non-supervisors cannot reach Reports, including by direct URL', async ({
    page,
  }, testInfo) => {
    for (const role of ['auditor', 'specialist'] as const) {
      await actAs(page, role);
      for (const [, suffix] of TABS) {
        await page.goto(BASE + suffix);
        await expect(page.getByText('Supervisors only')).toBeVisible();
        await expect(page.getByTestId('window-line')).toHaveCount(0);
        await expect(page.getByRole('heading', { level: 2 })).toHaveCount(0);
      }
      const nav = page.getByRole('navigation', { name: 'Auditor workspace' });
      await expect(nav.getByRole('link', { name: 'Management dashboard' })).toHaveCount(0);
    }
    await shot(page, 'state-denied', testInfo.project.name);
  });

  test('loading, empty and error states', async ({ page }, testInfo) => {
    await actAs(page, 'supervisor');
    let release: () => void = () => {};
    const held = new Promise<void>((r) => (release = r));
    await page.route(SUMMARY, async (route) => {
      await held;
      await route.continue();
    });
    await page.goto(BASE);
    await expect(page.getByText('Loading management figures…')).toBeVisible();
    await shot(page, 'state-loading', testInfo.project.name);
    release();
    await expect(page.getByTestId('window-line')).toBeVisible();
    await page.unroute(SUMMARY);

    await page.route(SUMMARY, async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      const data = body.data;
      data.workload = { open_cases: 0, unassigned_open_cases: 0, by_status: [], by_officer: [] };
      data.elapsed.open_case_age = {
        count: 0,
        excluded: 0,
        median_hours: null,
        p90_hours: null,
        max_hours: null,
      };
      data.elapsed.time_to_close = { ...data.elapsed.open_case_age };
      data.closure_outcomes = {
        closed_in_window: 0,
        without_recorded_outcome: 0,
        no_further_action: { numerator: 0, denominator: 0 },
        after_correction: { numerator: 0, denominator: 0 },
      };
      data.legal_route_coverage = {
        verified_by_reviewer: { numerator: 0, denominator: 0 },
        unverified: 0,
        no_verified_route: 0,
      };
      data.unresolved_evidence = {
        outstanding_requests: 0,
        overdue_requests: 0,
        cases_with_outstanding_requests: 0,
        open_cases_awaiting_evidence: 0,
      };
      data.missing_data = [];
      await route.fulfill({ response, json: body });
    });
    await page.goto(BASE);
    await expect(page.getByText('No cases to measure')).toBeVisible();
    await shot(page, 'state-empty', testInfo.project.name);
    await page.unroute(SUMMARY);

    await page.route(SUMMARY, (route) =>
      route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'upstream_unavailable',
            message: 'Unavailable',
            retryable: true,
            correlation_id: 'c0000000-0000-4000-8000-000000000149',
            field_errors: [],
          },
        }),
      }),
    );
    await page.goto(`${BASE}/timeliness`);
    await expect(page.getByText('Management figures are temporarily unavailable')).toBeVisible();
    await expect(tabs(page)).toBeVisible();
    await shot(page, 'state-error', testInfo.project.name);
  });
});
