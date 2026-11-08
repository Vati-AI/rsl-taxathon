import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import {
  hasVisibleFocus,
  horizontalOverflow,
  pageReady,
  seriousAxeViolations,
  tabUntilFocused,
} from './support/checks';
import { DECLARATION, fillDetails, toReview, upload } from './support/claim-flow';
import { TAXPAYER } from './support/env';
import { isMobile, TAXPAYER_ROUTES } from './support/matrix';
import {
  mockTaxpayerSession,
  OTHER_TAXPAYER_SUBMISSION_ID,
  PORTAL_SUBMISSIONS,
  SYNTHETIC_TAXPAYER,
} from './support/sessions';

// The synthetic taxpayer's own case (SUB-SYN-0001 per docs/PERMISSIONS.md): a claim filed
// in RSL's Online Refunds Application and read into Vati (TAX-122, TAX-125).
const own = PORTAL_SUBMISSIONS[0] as {
  id: string;
  reference: string;
  tax_type: string;
  rsl: { claim_ref: string; status_label: string };
};
// The open request in support/sessions.ts (OPEN_REQUEST_ID).
const OPEN_REQUEST = '45000000-0000-4000-8000-000000000001';

// The desktop sidebar and the mobile bottom bar share this accessible name.
const nav = (page: Page) => page.getByRole('navigation', { name: 'Taxpayer portal' });
const newLabel = (project: string) =>
  isMobile(project) ? 'Simulator' : 'Online Refunds simulator';

test.describe('taxpayer portal', () => {
  test('positive navigation: evidence requests → my cases → case → breadcrumb → simulator → account', async ({
    page,
  }, testInfo) => {
    await mockTaxpayerSession(page, 'taxpayer');
    // TAX-125: the portal opens on evidence requests, first in the navigation.
    await page.goto(TAXPAYER + '/taxpayer/');
    await expect(page).toHaveURL(/\/taxpayer\/requests$/);
    await pageReady(page, 'Evidence requests');
    await expect(nav(page).getByRole('link').first()).toHaveAccessibleName(
      isMobile(testInfo.project.name) ? 'Requests' : 'Evidence requests',
    );
    // TAX-092: the server says the data is live, so the fixture notice is not shown.
    await expect(page.getByText('Synthetic fixture data')).toHaveCount(0);

    await nav(page).getByRole('link', { name: 'My cases' }).click();
    await expect(page).toHaveURL(/\/taxpayer\/submissions$/);
    await pageReady(page, 'My cases');
    await expect(page.getByRole('list', { name: 'My cases' }).getByRole('listitem')).toHaveCount(
      PORTAL_SUBMISSIONS.length,
    );
    // Read-only: nothing on the page files or changes a claim.
    await expect(page.getByRole('main').getByRole('button', { name: /new|claim/i })).toHaveCount(0);

    await page.getByRole('link', { name: new RegExp(`RSL claim ${own.rsl.claim_ref}`) }).click();
    await expect(page).toHaveURL(new RegExp(`/taxpayer/submissions/${own.id}$`));
    await pageReady(page, new RegExp(`^${own.tax_type} · `));
    await expect(page.getByText('What happens next')).toBeVisible();

    await page.getByRole('link', { name: 'My cases', exact: true }).first().click();
    await expect(page).toHaveURL(/\/taxpayer\/submissions$/);
    await pageReady(page, 'My cases');

    await nav(page)
      .getByRole('link', { name: newLabel(testInfo.project.name) })
      .click();
    await expect(page).toHaveURL(/\/taxpayer\/submissions\/new$/);
    await pageReady(page, 'Online Refunds simulator');

    await nav(page).getByRole('link', { name: 'Account' }).click();
    await pageReady(page, 'Account');
    await expect(page.getByText(SYNTHETIC_TAXPAYER.user)).toBeVisible();
  });

  test('a case filed with RSL shows RSL reference, status and source, and no internal detail', async ({
    page,
  }) => {
    await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(TAXPAYER + `/taxpayer/submissions/${own.id}`);
    await pageReady(page, new RegExp(`^${own.tax_type} · `));
    const rsl = page.getByRole('region', { name: "In RSL's Online Refunds Application" });
    await expect(rsl.getByText(own.rsl.claim_ref)).toBeVisible();
    await expect(rsl.getByText(own.rsl.status_label)).toBeVisible();
    await expect(rsl.getByText(/recorded synthetic data, not a live RSL connection/)).toBeVisible();
    // No RSL link is configured here: the page says where to look instead of guessing one.
    await expect(rsl.getByRole('link')).toHaveCount(0);
    await expect(rsl.getByText(/from RSL's website/)).toBeVisible();
    const text = await page.getByRole('main').innerText();
    expect(text).not.toMatch(/risk|score|lane|jube|finding|fraud|officer'?s name/i);
  });

  test("a configured RSL link opens RSL's Online Refunds Application in a new tab", async ({
    page,
  }) => {
    const url = 'https://refunds.example.test/dashboard';
    await mockTaxpayerSession(page, 'taxpayer', { rslUrl: url });
    await page.goto(TAXPAYER + '/taxpayer/submissions');
    await pageReady(page, 'My cases');
    const link = page.getByRole('link', { name: /Open RSL's Online Refunds Application/ });
    await expect(link).toHaveAttribute('href', url);
    await expect(link).toHaveAttribute('target', '_blank');
    await expect(link).toHaveAttribute('rel', /noopener/);
  });

  test('without the simulator the portal has no claim form or link to one', async ({ page }) => {
    const mock = await mockTaxpayerSession(page, 'taxpayer', { simulatorEnabled: false });
    await page.goto(TAXPAYER + '/taxpayer/requests');
    await pageReady(page, 'Evidence requests');
    await expect(nav(page).getByRole('link', { name: /simulator/i })).toHaveCount(0);
    await expect(page.getByText('Fixture tools')).toHaveCount(0);
    await page.goto(TAXPAYER + '/taxpayer/submissions/new');
    await pageReady(page, 'Page not found');
    await expect(page.getByLabel('Amount claimed (LSL)')).toHaveCount(0);
    expect(mock.calls).not.toContain('vati_tax.portal.api.get_correction_reminders');
  });

  test('a link from RSL to one evidence request returns to it after login', async ({ page }) => {
    const mock = await mockTaxpayerSession(page, 'guest');
    await page.goto(TAXPAYER + `/taxpayer/requests/${OPEN_REQUEST}`);
    await expect(page).toHaveURL(
      new RegExp(`/login\\?redirect-to=%2Ftaxpayer%2Frequests%2F${OPEN_REQUEST}$`),
    );
    expect(mock.calls).toEqual(['vati_tax.portal.api.get_session']);
    // Signed in, the same link opens that request.
    mock.persona = 'taxpayer';
    await page.goto(TAXPAYER + `/taxpayer/requests/${OPEN_REQUEST}`);
    await pageReady(page, 'Evidence request');
  });

  test('denied access: a guest is sent to login with a portal-only return path', async ({
    page,
  }) => {
    const mock = await mockTaxpayerSession(page, 'guest');
    await page.goto(TAXPAYER + `/taxpayer/submissions/${own.id}`);
    await expect(page).toHaveURL(
      new RegExp(`/login\\?redirect-to=%2Ftaxpayer%2Fsubmissions%2F${own.id}$`),
    );
    await expect(page.getByRole('heading', { level: 1, name: 'Login' })).toBeVisible();
    // Only the session check ran: no submission data was requested for a guest.
    expect(mock.calls).toEqual(['vati_tax.portal.api.get_session']);
  });

  test('denied access: a signed-in user without a taxpayer membership is refused', async ({
    page,
  }) => {
    const mock = await mockTaxpayerSession(page, 'no-membership');
    await page.goto(TAXPAYER + '/taxpayer/submissions');
    // The client treats a refused session as no portal session and leaves for login;
    // the Frappe page guard (TAX-029) already answers such users with 403.
    await expect(page).toHaveURL(/\/login\?redirect-to=%2Ftaxpayer%2Fsubmissions$/);
    expect(mock.calls).toEqual(['vati_tax.portal.api.get_session']);
  });

  test("denied access: another taxpayer's submission is concealed as not found", async ({
    page,
  }) => {
    await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(TAXPAYER + `/taxpayer/submissions/${OTHER_TAXPAYER_SUBMISSION_ID}`);
    await pageReady(page, 'Case not found');
    // frappe-ui renders a routed Button as a link.
    const back = { name: 'Back to my cases' };
    await expect(page.getByRole('link', back).or(page.getByRole('button', back))).toBeVisible();
    await expect(page.getByText('What happens next')).toHaveCount(0);
  });

  test('denied access: a membership revoked after loading shows no submissions', async ({
    page,
  }) => {
    await mockTaxpayerSession(page, 'revoked');
    await page.goto(TAXPAYER + '/taxpayer/submissions');
    await pageReady(page, 'My cases');
    await expect(page.getByText('Access denied')).toBeVisible();
    await expect(page.getByText('You do not have access to this information.')).toBeVisible();
    await expect(page.getByText(/VAT-2026-|PAYE-2026-/)).toHaveCount(0);
  });

  test('logout ends the session and a later visit goes to login', async ({ page }) => {
    const mock = await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(TAXPAYER + '/taxpayer/account');
    await pageReady(page, 'Account');
    await page.getByRole('main').getByRole('button', { name: 'Log out' }).click();
    await expect(page).toHaveURL(/\/login$/);
    expect(mock.calls).toContain('logout');
    await page.goto(TAXPAYER + '/taxpayer/account');
    await expect(page).toHaveURL(/\/login\?redirect-to=%2Ftaxpayer%2Faccount$/);
  });

  test('keyboard: skip link and navigation activation', async ({ page }) => {
    await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(TAXPAYER + '/taxpayer/submissions');
    await pageReady(page, 'My cases');

    await page.keyboard.press('Tab');
    const skip = page.getByRole('link', { name: 'Skip to main content' });
    await expect(skip).toBeFocused();
    await expect(skip).toBeInViewport();
    await page.keyboard.press('Enter');
    // <main> is not itself focusable here; the skip link moves the sequential
    // focus start, so the next Tab must land inside the main region.
    await page.keyboard.press('Tab');
    expect(await page.evaluate(() => Boolean(document.activeElement?.closest('main#main')))).toBe(
      true,
    );

    const account = nav(page).getByRole('link', { name: 'Account' });
    await tabUntilFocused(page, account, 40);
    expect(await hasVisibleFocus(account)).toBe(true);
    await page.keyboard.press('Enter');
    await pageReady(page, 'Account');
  });

  test('keyboard: an incomplete form keeps values and focuses the first invalid field', async ({
    page,
  }) => {
    await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(TAXPAYER + '/taxpayer/submissions/new');
    await pageReady(page, 'Online Refunds simulator');

    const period = page.getByLabel('Tax period');
    await period.fill('2026-09');
    await period.press('Enter');

    await expect(
      page.getByRole('alert').filter({ hasText: 'Fix 4 fields to continue' }),
    ).toBeVisible();
    const amount = page.getByLabel('Amount claimed (LSL)');
    await expect(amount).toBeFocused();
    await expect(amount).toHaveAttribute('aria-invalid', 'true');
    await expect(page.getByLabel('Reason for the claim')).toHaveAttribute('aria-invalid', 'true');
    await expect(period).toHaveValue('2026-09');
  });

  for (const route of TAXPAYER_ROUTES) {
    test(`matrix ${route.name}: no serious axe violations, no page-wide horizontal scroll`, async ({
      page,
    }) => {
      await mockTaxpayerSession(page, 'taxpayer');
      await page.goto(TAXPAYER + route.path);
      await pageReady(page, route.heading);
      expect(await seriousAxeViolations(page)).toEqual([]);
      expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
    });
  }
});

// CAPTURE_SCREENSHOTS=1 writes TAX-092 review screenshots to docs/evidence/TAX-092.
async function shot092(page: Page, name: string, project: string) {
  if (!process.env.CAPTURE_SCREENSHOTS) return;
  const out = path.resolve(__dirname, '../../docs/evidence/TAX-092');
  await page.screenshot({
    path: path.join(out, `${name}-${project.split('-')[1]}.png`),
    fullPage: true,
  });
}

test.describe('live submissions (TAX-092)', () => {
  test('the fixture notice appears only when the server labels its data as fixture', async ({
    page,
  }, testInfo) => {
    const desktop = !isMobile(testInfo.project.name);
    await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(TAXPAYER + '/taxpayer/submissions');
    await pageReady(page, 'My cases');
    await expect(page.getByText('Synthetic fixture data')).toHaveCount(0);
    if (desktop) await expect(nav(page).getByText('Live data')).toBeVisible();
    await shot092(page, 'list-live', testInfo.project.name);

    const fixture = await mockTaxpayerSession(page, 'taxpayer', { dataMode: 'fixture' });
    await page.reload();
    await pageReady(page, 'My cases');
    expect(fixture.dataMode).toBe('fixture');
    await expect(page.getByText('Synthetic fixture data')).toBeVisible();
    if (desktop) await expect(nav(page).getByText('Synthetic fixture')).toBeVisible();
    expect(await seriousAxeViolations(page)).toEqual([]);
    await shot092(page, 'list-fixture-mode', testInfo.project.name);
  });

  test('a claim the taxpayer sends appears in their list with its status', async ({
    page,
  }, testInfo) => {
    test.slow();
    await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(TAXPAYER + '/taxpayer/submissions/new');
    await pageReady(page, 'Online Refunds simulator');
    await fillDetails(page);
    await (await upload(page)).getByText('Uploaded').waitFor();
    await toReview(page);
    await page.getByLabel(DECLARATION).check();
    await page.getByRole('button', { name: 'Send claim' }).click();
    await expect(page.getByRole('heading', { name: 'Claim sent' })).toBeFocused();
    const reference = (await page
      .getByText(/^REF-2026-\d{6}$/)
      .first()
      .textContent())!.trim();

    await nav(page).getByRole('link', { name: 'My cases', exact: true }).click();
    await pageReady(page, 'My cases');
    const items = page.getByRole('list', { name: 'My cases' }).getByRole('listitem');
    await expect(items).toHaveCount(PORTAL_SUBMISSIONS.length + 1);
    const sent = items.first();
    await expect(sent).toContainText(reference);
    await expect(sent).toContainText('Under review');
    await expect(sent).toContainText('LSL 48,210.55');
    await expect(sent).not.toContainText(/risk|lane|score|jube|finding/i);
    expect(await seriousAxeViolations(page)).toEqual([]);
    await shot092(page, 'list-with-sent-claim', testInfo.project.name);

    await sent.getByRole('link').click();
    await pageReady(page, /^VAT refund claim · /);
    await expect(page.getByText(reference).first()).toBeVisible();
    await expect(page.getByText('An officer is reviewing your submission.')).toBeVisible();
    expect(await seriousAxeViolations(page)).toEqual([]);
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
    await shot092(page, 'sent-claim-status', testInfo.project.name);
  });
});

// CAPTURE_SCREENSHOTS=1 writes TAX-125 review screenshots to docs/evidence/TAX-125.
async function shot125(page: Page, name: string, project: string) {
  if (!process.env.CAPTURE_SCREENSHOTS) return;
  const out = path.resolve(__dirname, '../../docs/evidence/TAX-125');
  await page.screenshot({
    path: path.join(out, `${name}-${project.split('-')[1]}.png`),
    fullPage: true,
  });
}

test.describe('evidence-first portal (TAX-125)', () => {
  test('screens: home, my cases, an RSL case, the simulator and its absence', async ({
    page,
  }, testInfo) => {
    const project = testInfo.project.name;
    await mockTaxpayerSession(page, 'taxpayer', {
      rslUrl: 'https://refunds.example.test/dashboard',
    });
    await page.goto(TAXPAYER + '/taxpayer/');
    await pageReady(page, 'Evidence requests');
    expect(await seriousAxeViolations(page)).toEqual([]);
    await shot125(page, 'home-requests', project);

    await page.goto(TAXPAYER + '/taxpayer/submissions');
    await pageReady(page, 'My cases');
    expect(await seriousAxeViolations(page)).toEqual([]);
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
    await shot125(page, 'my-cases', project);

    await page.goto(TAXPAYER + `/taxpayer/submissions/${own.id}`);
    await pageReady(page, new RegExp(`^${own.tax_type} · `));
    expect(await seriousAxeViolations(page)).toEqual([]);
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
    await shot125(page, 'rsl-case', project);

    await page.goto(TAXPAYER + '/taxpayer/submissions/new');
    await pageReady(page, 'Online Refunds simulator');
    await shot125(page, 'simulator', project);

    await mockTaxpayerSession(page, 'taxpayer', { simulatorEnabled: false });
    await page.goto(TAXPAYER + '/taxpayer/submissions/new');
    await pageReady(page, 'Page not found');
    await shot125(page, 'simulator-absent', project);
  });
});
