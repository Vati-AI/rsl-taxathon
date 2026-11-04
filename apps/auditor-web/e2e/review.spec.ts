import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Request } from '@playwright/test';

// Officer review (TAX-042) against the stateful fixture (scripts/fixture_review.py),
// served on its own port by the "review" project. Each test uses its own case.
const id = (n: number) => `40000000-0000-4000-8000-00000000000${n}`;
const TENANT = '10000000-0000-4000-8000-000000000001';
const reviewUrl = (n: number) => `/auditor/cases/${id(n)}/review`;

function trackPosts(page: Page) {
  const posts: Request[] = [];
  page.on('request', (r) => {
    if (r.method() === 'POST') posts.push(r);
  });
  return posts;
}

async function actAs(page: Page, role: 'auditor' | 'specialist' | 'supervisor') {
  await page.addInitScript((value) => window.localStorage.setItem('vati.fixtureRole', value), role);
}

async function ready(page: Page) {
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Review and decide');
  await expect(page.getByText(/Reviewing as/)).toBeVisible();
}

async function axe(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(results.violations.map((v) => v.id)).toEqual([]);
}

test('an agent suggestion only fills the form; the officer confirms by keyboard', async ({
  page,
}) => {
  const posts = trackPosts(page);
  await page.goto(reviewUrl(3));
  await ready(page);
  await axe(page);

  await page.getByRole('button', { name: 'Use as a starting point' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('radio', { name: /Refer to a specialist/ })).toBeChecked();
  expect(posts).toHaveLength(0);

  await page
    .getByLabel('Rationale in your own words')
    .fill(
      'Payslips and contractor invoices overlap; a specialist should review the classification.',
    );
  await page.getByRole('button', { name: 'Review and propose…' }).focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog', { name: 'Confirm the proposed decision' });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('Nothing is sent to Jube until then.');
  await axe(page);

  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  expect(posts).toHaveLength(0);

  await page.getByRole('button', { name: 'Review and propose…' }).focus();
  await page.keyboard.press('Enter');
  await dialog.getByRole('button', { name: 'Propose decision' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('status').getByText('Decision proposed')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Decision waiting for approval' })).toBeVisible();
  expect(posts).toHaveLength(1);
  expect(posts[0].headers()['idempotency-key']).toBeTruthy();
  expect(posts[0].postDataJSON()).toMatchObject({
    outcome: 'refer_to_specialist',
    expected_version: 6,
    finding_ids: ['70000000-0000-4000-8000-000000000301'],
  });
});

test('the proposer cannot approve; a different supervisor approves and Jube applies it', async ({
  page,
}) => {
  await page.goto(reviewUrl(1));
  await ready(page);
  await expect(page.getByRole('button', { name: 'Approve…' })).toBeDisabled();
  await expect(page.getByText('You proposed this decision')).toBeVisible();

  await page.getByLabel('Acting as (fixture)').selectOption('supervisor');
  await expect(page.getByText('Reviewing as Supervisor')).toBeVisible();
  await page.getByRole('button', { name: 'Approve…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Approve this decision?' });
  await expect(dialog).toContainText('Move the case to Resolved');
  await dialog.getByRole('button', { name: 'Approve and apply in Jube' }).click();
  await expect(
    page.getByRole('status').getByText('Applied in Jube: case is now Resolved.'),
  ).toBeVisible();
  await expect(page.getByText('Resolved', { exact: true }).first()).toBeVisible();
  await expect(
    page.getByText('This case is resolved. No further decision can be proposed.'),
  ).toBeVisible();
});

test('a stale view is refused, offers a reload and keeps the officer text', async ({
  page,
  request,
}) => {
  await page.goto(reviewUrl(4));
  await ready(page);
  await page.getByRole('radio', { name: /Continue the investigation/ }).check();
  const text = 'The withholding question needs more work before the case can move on.';
  await page.getByLabel('Rationale in your own words').fill(text);
  await page.getByRole('button', { name: 'Review and propose…' }).click();

  // Meanwhile a supervisor records an evidence request on the same case.
  const other = await request.post(`/api/v1/cases/${id(4)}/request-evidence`, {
    headers: {
      'X-Vati-Tenant-ID': TENANT,
      'X-Vati-Fixture-Role': 'supervisor',
      'Idempotency-Key': 'e2e-stale-view-0001',
    },
    data: {
      expected_version: 7,
      finding_ids: ['70000000-0000-4000-8000-000000000401'],
      items: [{ document_type: 'other', description: 'Reverse-charge declaration.' }],
      due_date: new Date(Date.now() + 10 * 86_400_000).toISOString().slice(0, 10),
      message_to_taxpayer: 'Please provide the reverse-charge declaration.',
    },
  });
  expect(other.status()).toBe(202);

  const dialog = page.getByRole('dialog', { name: 'Confirm the proposed decision' });
  await dialog.getByRole('button', { name: 'Propose decision' }).click();
  await expect(dialog).toContainText('This case changed while you were reviewing it');
  await dialog.getByRole('button', { name: 'Back to the form' }).click();
  await expect(page.getByLabel('Rationale in your own words')).toHaveValue(text);
});

test('the server refuses an action the role does not permit', async ({ page }) => {
  await page.goto(reviewUrl(7));
  await ready(page);
  // The screen thinks this is an auditor, but the server sees a specialist.
  await page.route('**/decisions', (route) =>
    route.continue({
      headers: { ...route.request().headers(), 'x-vati-fixture-role': 'specialist' },
    }),
  );
  await page.getByRole('radio', { name: /Refer to a specialist/ }).check();
  await page
    .getByLabel('Rationale in your own words')
    .fill('Validation flagged a classification question for a specialist.');
  await page.getByRole('button', { name: 'Review and propose…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Confirm the proposed decision' });
  await dialog.getByRole('button', { name: 'Propose decision' }).click();
  await expect(dialog).toContainText('Your role does not permit this action');
  await expect(dialog.getByRole('button', { name: 'Try again' })).toHaveCount(0);
});

test('a specialist is not offered evidence requests or referrals', async ({ page }) => {
  await actAs(page, 'specialist');
  await page.goto(reviewUrl(2));
  await ready(page);
  await expect(
    page
      .locator('section[aria-labelledby="evidence-request-heading"]')
      .getByText('Only auditors and supervisors can authorise evidence requests'),
  ).toBeVisible();
  await expect(page.getByRole('radio', { name: /Refer to a specialist/ })).toBeDisabled();
  await expect(page.getByRole('radio', { name: /Continue the investigation/ })).toBeDisabled();
});

test('an expired proposal cannot be approved', async ({ page }) => {
  await actAs(page, 'supervisor');
  await page.goto(reviewUrl(5));
  await ready(page);
  await page.getByRole('button', { name: 'Approve…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Approve this decision?' });
  await dialog.getByRole('button', { name: 'Approve and apply in Jube' }).click();
  await expect(dialog).toContainText('This proposal is too old to approve');
});

test('an evidence request is confirmed and recorded without moving the case', async ({ page }) => {
  await page.goto(reviewUrl(2));
  await ready(page);
  await page.getByRole('button', { name: 'Use as a starting point' }).click();
  await page.getByLabel('Document 1 type').click();
  await page.getByRole('option', { name: 'Contract' }).click();
  await page.getByLabel('Document 1 description').fill('Signed management services agreement.');
  await page
    .getByLabel('Message to the taxpayer')
    .fill('Please upload the signed management services agreement for the period.');
  await page.getByRole('button', { name: 'Review request…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Confirm the evidence request' });
  await expect(dialog).toContainText('It does not change the case status');
  await dialog.getByRole('button', { name: 'Record evidence request' }).click();
  await expect(page.getByRole('status').getByText('Evidence request recorded')).toBeVisible();
  await expect(page.getByText('Investigating', { exact: true }).first()).toBeVisible();
});

test('a retry after a lost response does not record the decision twice', async ({ page }) => {
  const posts = trackPosts(page);
  await page.goto(reviewUrl(9));
  await ready(page);
  let lose = true;
  await page.route('**/decisions', async (route) => {
    if (!lose) return route.continue();
    lose = false;
    await route.fetch(); // the server records it...
    await route.abort('connectionreset'); // ...but the response never arrives
  });
  await page.getByRole('radio', { name: /Continue the investigation/ }).check();
  await page
    .getByLabel('Rationale in your own words')
    .fill('The submission needs investigation before any clearance.');
  await page.getByRole('button', { name: 'Review and propose…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Confirm the proposed decision' });
  await dialog.getByRole('button', { name: 'Propose decision' }).click();
  await expect(dialog).toContainText('The case service is unavailable');
  await dialog.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByRole('status').getByText('Decision proposed')).toBeVisible();
  const keys = posts.map((p) => p.headers()['idempotency-key']);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  const trail = page.getByRole('list', { name: 'Review trail, newest first' });
  await expect(trail.getByText('Decision proposed')).toHaveCount(1);
});
