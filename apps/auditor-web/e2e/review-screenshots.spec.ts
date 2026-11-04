import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';

// Review evidence for TAX-042 (docs/DESIGN_STANDARD.md): the decision, evidence
// request and referral screens at each width and in their main states. Not a
// baseline comparison. Run with CAPTURE_SCREENSHOTS=1; output goes to
// docs/evidence/TAX-042. Nothing here changes fixture state: refusals are
// simulated by answering the request in the browser.
test.skip(
  !process.env.CAPTURE_SCREENSHOTS,
  'Set CAPTURE_SCREENSHOTS=1 to capture review screenshots',
);

const out = path.resolve(__dirname, '../../../docs/evidence/TAX-042');
const id = (n: number) => `40000000-0000-4000-8000-00000000000${n}`;
const reviewUrl = (n: number) => `/auditor/cases/${id(n)}/review`;

async function actAs(page: Page, role: string) {
  await page.addInitScript((value) => window.localStorage.setItem('vati.fixtureRole', value), role);
}

async function ready(page: Page) {
  await expect(page.getByText(/Reviewing as/)).toBeVisible();
  await page.waitForLoadState('networkidle');
}

for (const width of [1440, 1024, 390]) {
  test(`review screens at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const shot = (name: string) =>
      page.screenshot({ path: `${out}/${name}-${width}.png`, fullPage: true });

    await page.goto(reviewUrl(3));
    await ready(page);
    await shot('propose-referral-suggestion');

    await page.goto(reviewUrl(1));
    await ready(page);
    await shot('awaiting-approval-proposer');

    await page.goto(`/auditor/cases/${id(2)}`);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await page.getByRole('tab', { name: 'Findings' }).click();
    await page.waitForLoadState('networkidle');
    await shot('case-findings-tab');
  });
}

for (const width of [1440, 390]) {
  test(`review states at ${width}px`, async ({ page, context }) => {
    await page.setViewportSize({ width, height: 900 });
    const shot = (name: string) =>
      page.screenshot({ path: `${out}/state-${name}-${width}.png`, fullPage: false });

    // Confirmation dialog for a referral started from the agent suggestion.
    await page.goto(reviewUrl(3));
    await ready(page);
    await page.getByRole('button', { name: 'Use as a starting point' }).click();
    await page
      .getByLabel('Rationale in your own words')
      .fill(
        'Payslips and contractor invoices overlap; a specialist should review the classification.',
      );
    await page.getByRole('button', { name: 'Review and propose…' }).click();
    const dialog = page.getByRole('dialog', { name: 'Confirm the proposed decision' });
    await expect(dialog).toBeVisible();
    await shot('confirm-referral');

    // Stale view refused (simulated 409 so the fixture is not changed).
    await page.route('**/decisions', (route) =>
      route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'version_conflict',
            message: 'stale',
            retryable: false,
            correlation_id: 'c0000000-0000-4000-8000-0000000000ff',
            field_errors: [{ field: 'expected_version', code: 'stale_case_version' }],
            current_version: 7,
          },
        }),
      }),
    );
    await dialog.getByRole('button', { name: 'Propose decision' }).click();
    await expect(dialog).toContainText('This case changed while you were reviewing it');
    await shot('refused-stale-view');
    await page.unroute('**/decisions');

    // Validation errors keep focus on the first problem.
    await page.goto(reviewUrl(7));
    await ready(page);
    await page.getByRole('button', { name: 'Review and propose…' }).click();
    await shot('validation-errors');

    // A supervisor reviewing a colleague's proposal.
    const supervisor = await context.browser()!.newPage({ viewport: { width, height: 900 } });
    await actAs(supervisor, 'supervisor');
    await supervisor.goto(new URL(reviewUrl(1), page.url()).toString());
    await ready(supervisor);
    await supervisor.screenshot({ path: `${out}/state-supervisor-approve-${width}.png` });
    await supervisor.getByRole('button', { name: 'Reject…' }).click();
    await supervisor.screenshot({ path: `${out}/state-supervisor-reject-reason-${width}.png` });
    await supervisor.close();

    // A specialist: no evidence requests or referrals.
    const specialist = await context.browser()!.newPage({ viewport: { width, height: 900 } });
    await actAs(specialist, 'specialist');
    await specialist.goto(new URL(reviewUrl(2), page.url()).toString());
    await ready(specialist);
    await specialist.screenshot({
      path: `${out}/state-specialist-permissions-${width}.png`,
      fullPage: true,
    });
    await specialist.close();
  });
}
