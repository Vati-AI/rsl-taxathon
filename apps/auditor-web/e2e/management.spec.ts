import path from 'node:path';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';

// Management dashboard (TAX-060) against the fixture server's getManagementSummary.
// Figures come from integration/api/fixtures/management.json and auditor_queue.json.
const SUMMARY = /\/api\/v1\/management\/summary/;
const nav = (page: Page) => page.getByRole('navigation', { name: 'Auditor workspace' });
const section = (page: Page, name: string) => page.getByRole('region', { name });
/** Open a Reports tab (TAX-149) and wait for its figures. */
async function openTab(page: Page, name: string) {
  await page
    .getByRole('navigation', { name: 'Report sections' })
    .getByRole('link', { name })
    .click();
  await expect(page.getByTestId('window-line')).toBeVisible();
}
const shots = path.resolve(__dirname, '../../../docs/evidence/TAX-060');
const capture = Boolean(process.env.CAPTURE_SCREENSHOTS);

/** Replace part of the real fixture response, keeping its headers. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- free-form edits of a JSON test body
function edit(change: (data: Record<string, any>) => void) {
  return async (route: Route) => {
    const response = await route.fetch();
    const body = await response.json();
    change(body.data);
    await route.fulfill({ response, json: body });
  };
}

/**
 * Act as a fixture officer (TAX-042 "Acting as" switch, stored by session.ts and
 * sent as X-Vati-Fixture-Role). Init scripts run in order, so a later call wins.
 */
async function actAs(page: Page, role: 'auditor' | 'specialist' | 'supervisor') {
  await page.addInitScript((value) => localStorage.setItem('vati.fixtureRole', value), role);
}

async function ready(page: Page) {
  await expect(page.getByRole('heading', { level: 1, name: 'Management dashboard' })).toBeVisible();
  // TAX-149: the dashboard is Reports tabs; every tab shows the measured window.
  await expect(page.getByTestId('window-line')).toBeVisible();
}

async function shot(page: Page, name: string) {
  if (capture) await page.screenshot({ path: path.join(shots, `${name}.png`), fullPage: true });
}

async function seriousViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  return results.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => v.id);
}

test.describe('management dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await actAs(page, 'supervisor');
  });

  test('supervisor sees measured figures with their denominators', async ({ page }) => {
    await page.goto('/auditor/');
    await nav(page).getByRole('link', { name: 'Management dashboard' }).click();
    await expect(page).toHaveURL(/\/auditor\/management$/);
    await ready(page);
    await expect(nav(page).getByRole('link', { name: 'Management dashboard' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    await expect(page.getByTestId('window-line')).toContainText(
      '5 Oct 2026 to 4 Nov 2026 (30 days)',
    );

    const workload = page.locator('section[aria-labelledby="workload-heading"]');
    await expect(workload.locator('dt', { hasText: /^Open cases$/ }).locator('..')).toContainText(
      '9',
    );
    const officers = section(page, 'Open cases by officer');
    await expect(officers.getByRole('row')).toHaveCount(3);
    await expect(officers.getByRole('row', { name: /Synthetic Officer A/ })).toContainText('7');

    await openTab(page, 'Timeliness');
    await expect(page).toHaveURL(/\/auditor\/management\/timeliness$/);
    const elapsed = section(page, 'Elapsed time');
    await expect(elapsed.getByRole('row', { name: /Age of open cases/ })).toContainText('25.8 h');
    await expect(elapsed.getByRole('row', { name: /Time to close/ })).toContainText(
      '1 case not measured',
    );

    await openTab(page, 'Outcomes');
    const closures = page.locator('section[aria-labelledby="closures-heading"]');
    await expect(closures).toContainText('3 of 6 cases (50%)');
    await expect(closures).toContainText('1 closed case has no recorded outcome');
    const routes = page.locator('section[aria-labelledby="routes-heading"]');
    await expect(routes).toContainText('3 of 7 findings (43%)');
    await openTab(page, 'Evidence');
    const evidence = page.locator('section[aria-labelledby="evidence-heading"]');
    await expect(evidence).toContainText('Past their due date');

    await expect(page.getByText('Some records could not be measured')).toBeVisible();

    await openTab(page, 'Ledger assurance');
    const assurance = page.locator('section[aria-labelledby="assurance-heading"]');
    await expect(assurance).toContainText('Intact: every entry verifies');
    // Officer A's refused self-approvals are shown; the viewer's own overrides are withheld.
    await expect(assurance).toContainText('Repeated refused self-approvals or overrides');
    await expect(assurance).not.toContainText('Repeated overrides');
    await expect(assurance).toContainText('1 signal concerns you and is not shown here');
    // Review signals never name anyone on this dashboard.
    await expect(assurance).not.toContainText('@example.test');
    await shot(page, 'dashboard-1440');
  });

  test('changing the window remeasures closures and keeps the choice in the URL', async ({
    page,
  }) => {
    await page.goto('/auditor/management/outcomes');
    await ready(page);
    await page.getByRole('combobox', { name: 'Reporting window' }).click();
    await page.getByRole('option', { name: 'Last 7 days' }).click();
    await expect(page).toHaveURL(/window=7/);
    const closures = page.locator('section[aria-labelledby="closures-heading"]');
    await expect(closures).toContainText('1 of 4 cases (25%)');
    await page.reload();
    await expect(closures).toContainText('1 of 4 cases (25%)');
    await expect(page.getByTestId('window-line')).toContainText('(7 days)');
  });

  test('the server refuses an auditor or specialist and no figures are shown', async ({ page }) => {
    for (const role of ['auditor', 'specialist'] as const) {
      await actAs(page, role);
      await page.goto('/auditor/management');
      const alert = page.getByRole('alert').filter({ hasText: 'Supervisors only' });
      await expect(alert, role).toBeVisible();
      await expect(alert).toContainText(/Reference: [0-9a-f-]{36}/);
      await expect(page.getByRole('heading', { level: 2 })).toHaveCount(0);
      await expect(page.getByText('Synthetic Officer A')).toHaveCount(0);
      await expect(page.getByRole('combobox', { name: 'Reporting window' })).toBeDisabled();
    }
    await shot(page, 'denied-1440');
  });

  test('only supervisors see the nav entry, and switching role reloads the page', async ({
    page,
  }) => {
    await actAs(page, 'auditor');
    await page.goto('/auditor/');
    await expect(page.getByRole('heading', { level: 1, name: 'Overview' })).toBeVisible();
    const entry = nav(page).getByRole('link', { name: 'Management dashboard' });
    await expect(entry).toHaveCount(0);

    const actingAs = page.getByRole('combobox', { name: 'Acting as (fixture)' });
    await actingAs.selectOption('supervisor');
    await expect(entry).toBeVisible();
    await entry.click();
    await ready(page);

    // Back to auditor on the dashboard: the entry goes and the server refuses the reload.
    await actingAs.selectOption('auditor');
    await expect(entry).toHaveCount(0);
    await expect(page.getByRole('alert').filter({ hasText: 'Supervisors only' })).toBeVisible();
    await expect(page.getByRole('heading', { level: 2 })).toHaveCount(0);
  });

  test('service unavailable offers a retry that recovers', async ({ page }) => {
    await page.route(SUMMARY, (route) => route.abort('connectionrefused'));
    await page.goto('/auditor/management');
    const alert = page
      .getByRole('alert')
      .filter({ hasText: 'Management figures are temporarily unavailable' });
    await expect(alert).toBeVisible();
    await page.unroute(SUMMARY);
    await alert.getByRole('button', { name: 'Try again' }).click();
    await ready(page);
  });

  test('an inconsistent response is refused rather than shown', async ({ page }) => {
    await page.route(
      SUMMARY,
      edit((data) => (data.closure_outcomes.no_further_action.numerator = 99)),
    );
    await page.goto('/auditor/management');
    await expect(page.getByText('Data could not be shown safely')).toBeVisible();
    await expect(page.getByRole('heading', { level: 2 })).toHaveCount(0);
  });

  test('nothing to measure shows "No data", never 0%', async ({ page }) => {
    await page.route(
      SUMMARY,
      edit((data) => {
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
      }),
    );
    await page.goto('/auditor/management');
    await ready(page);
    await expect(page.getByText('No cases to measure')).toBeVisible();
    await expect(page.getByText('No open case is assigned to an officer.')).toBeVisible();
    await openTab(page, 'Outcomes');
    const closures = page.locator('section[aria-labelledby="closures-heading"]');
    await expect(closures.getByText('No data')).toHaveCount(2);
    await expect(page.getByText('0%')).toHaveCount(0);
    await shot(page, 'empty-1440');
  });

  test('ledger unavailable and ledger broken are shown as such', async ({ page }) => {
    await page.route(
      SUMMARY,
      edit((data) => {
        data.assurance = {
          available: false,
          reason: 'The audit ledger is not available in this environment.',
        };
      }),
    );
    await page.goto('/auditor/management/assurance');
    await ready(page);
    await expect(page.getByText('Audit ledger figures are not available')).toBeVisible();
    await page.unroute(SUMMARY);

    await page.route(
      SUMMARY,
      edit((data) => {
        data.assurance.ledger_intact = false;
        data.assurance.review_signals.push({ kind: 'ledger_integrity', count: 1 });
      }),
    );
    await page.reload();
    await ready(page);
    const assurance = page.locator('section[aria-labelledby="assurance-heading"]');
    await expect(assurance).toContainText('Failed: entries changed or missing');
    await expect(assurance).toContainText('Audit ledger check failed');
    await shot(page, 'ledger-broken-1440');
  });

  test('loading state is announced', async ({ page }) => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    await page.route(SUMMARY, async (route) => {
      await gate;
      await route.continue();
    });
    await page.goto('/auditor/management');
    await expect(
      page.getByRole('status').filter({ hasText: 'Loading management figures' }),
    ).toBeVisible();
    await shot(page, 'loading-1440');
    release();
    await ready(page);
  });

  test('keyboard reaches the window control and table regions with visible focus', async ({
    page,
  }) => {
    await page.goto('/auditor/management');
    await ready(page);
    const control = page.getByRole('combobox', { name: 'Reporting window' });
    await control.focus();
    await expect(control).toBeFocused();
    await page.keyboard.press('Tab');
    const region = section(page, 'Open cases by status');
    for (
      let i = 0;
      i < 10 && !(await region.evaluate((el) => el === document.activeElement));
      i++
    ) {
      await page.keyboard.press('Tab');
    }
    await expect(region).toBeFocused();
    const outline = await region.evaluate((el) => getComputedStyle(el).boxShadow);
    expect(outline).not.toBe('none');
  });

  for (const width of [1440, 1024, 390]) {
    test(`no serious axe violations or page-wide scroll at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/auditor/management');
      await ready(page);
      expect(await seriousViolations(page)).toEqual([]);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
      await shot(page, `dashboard-${width}`);
    });
  }
});
