import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { horizontalOverflow, pageReady, seriousAxeViolations } from './support/checks';
import { TAXPAYER } from './support/env';
import {
  ANSWERED_REQUEST_ID,
  mockTaxpayerSession,
  OPEN_REQUEST_ID,
  OTHER_TAXPAYER_REQUEST_ID,
  WITHDRAWN_REQUEST_ID,
  type PortalMock,
} from './support/sessions';

// TAX-045 evidence request responses, at every matrix width. The portal API and
// the TAX-032 upload are answered by the session mocks (support/sessions.ts);
// the server rules are covered by vati_tax/responses/test_responses_bench.py.
//
// CAPTURE_SCREENSHOTS=1 also writes review screenshots to docs/evidence/TAX-045
// (evidence for human review only; nothing is compared or approved here).
const capture = Boolean(process.env.CAPTURE_SCREENSHOTS);
const out = path.resolve(__dirname, '../../docs/evidence/TAX-045');
const width = (project: string) => project.split('-')[1];

function pdf(name: string) {
  return { name, mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n% synthetic\n') };
}

async function shot(page: Page, name: string, w: string) {
  if (!capture) return;
  await page.screenshot({ path: path.join(out, `${name}-${w}.png`), fullPage: true });
}

async function accessible(page: Page) {
  expect(await seriousAxeViolations(page)).toEqual([]);
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
}

async function openRequest(page: Page, id = OPEN_REQUEST_ID): Promise<PortalMock> {
  const mock = await mockTaxpayerSession(page, 'taxpayer');
  await page.goto(TAXPAYER + `/taxpayer/requests/${id}`);
  await pageReady(page, 'Evidence request');
  return mock;
}

async function uploadFor(page: Page, item: number, name: string) {
  await page.getByLabel(`Choose a file for item ${item}`).setInputFiles(pdf(name));
  await expect(
    page
      .getByRole('list', { name: `Documents for item ${item}` })
      .getByRole('listitem')
      .filter({ hasText: name })
      .getByText('Uploaded', { exact: true }),
  ).toBeVisible();
}

const declineItem2 = async (page: Page) => {
  await page.getByLabel('I cannot provide this document (item 2)').check();
  await page
    .getByLabel("Why can't you provide it? (item 2)")
    .fill('Synthetic: the bank account was closed.');
};

test.describe('evidence request responses (TAX-045)', () => {
  test('list shows only taxpayer-facing statuses and opens a request', async ({
    page,
  }, testInfo) => {
    const w = width(testInfo.project.name);
    await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(TAXPAYER + '/taxpayer/requests');
    await pageReady(page, 'Evidence requests');
    const list = page.getByRole('list', { name: 'Evidence requests' });
    await expect(list.getByRole('listitem')).toHaveCount(3);
    for (const label of ['Response needed', 'Response sent', 'No longer needed']) {
      await expect(list.getByText(label, { exact: true })).toBeVisible();
    }
    // Internal case data never appears.
    await expect(page.getByText(/case_id|finding|authoris|supervisor/i)).toHaveCount(0);
    await accessible(page);
    await shot(page, 'requests-list', w);

    await list.getByRole('link').first().click();
    await pageReady(page, 'Evidence request');
    await expect(page.getByText('Message from the officer')).toBeVisible();
    await accessible(page);
    await shot(page, 'request-open', w);
  });

  test('structured response: documents for one item, a reason for the other', async ({
    page,
  }, testInfo) => {
    const w = width(testInfo.project.name);
    const mock = await openRequest(page);
    await uploadFor(page, 1, 'invoice-2026-09-001.pdf');
    await declineItem2(page);
    await page
      .getByLabel('Message to the officer (optional)')
      .fill('Synthetic: one invoice attached.');
    await accessible(page);
    await shot(page, 'request-answered-form', w);

    await page.getByRole('button', { name: 'Send response' }).click();
    await expect(page.getByRole('heading', { name: 'Your response' })).toBeFocused();
    await expect(page.getByText('Response sent', { exact: true }).first()).toBeVisible();
    await expect(
      page.getByText('Not provided: Synthetic: the bank account was closed.'),
    ).toBeVisible();
    await accessible(page);
    await shot(page, 'request-sent', w);

    // The attachment is linked to the exact item it answers.
    const [stored] = [...mock.responses.values()];
    const sent = JSON.parse(stored.body) as [
      string,
      { item_index: number; evidence_ids: string[] }[],
    ];
    expect(sent[0]).toBe(OPEN_REQUEST_ID);
    expect(sent[1][0]).toMatchObject({ item_index: 0, evidence_ids: [mock.evidence[0]] });
    expect(sent[1][1]).toMatchObject({ item_index: 1, evidence_ids: [] });
  });

  test('every item needs an answer; focus moves to the first unanswered item', async ({
    page,
  }, testInfo) => {
    const w = width(testInfo.project.name);
    const mock = await openRequest(page);
    await page.getByRole('button', { name: 'Send response' }).click();
    await expect(
      page.getByRole('alert').filter({ hasText: '2 items need an answer' }),
    ).toBeVisible();
    await expect(page.getByRole('heading', { name: /^Item 1:/ })).toBeFocused();
    // The focused heading announces its problem to screen readers.
    await expect(page.getByRole('heading', { name: /^Item 1:/ })).toHaveAccessibleDescription(
      /Upload a document for this item/,
    );
    await page.getByLabel('I cannot provide this document (item 2)').check();
    await page.getByRole('button', { name: 'Send response' }).click();
    await expect(page.getByText('Explain why you cannot provide this document.')).toBeVisible();
    expect(mock.calls).not.toContain('vati_tax.portal.api.respond_to_evidence_request');
    await accessible(page);
    await shot(page, 'request-errors', w);
  });

  test('a lost response is resent safely and stored once', async ({ page }, testInfo) => {
    const w = width(testInfo.project.name);
    const mock = await openRequest(page);
    await uploadFor(page, 1, 'invoice-2026-09-001.pdf');
    await declineItem2(page);
    mock.dropNextRespondResponse = true;
    await page.getByRole('button', { name: 'Send response' }).click();
    await expect(
      page
        .getByRole('alert')
        .filter({ hasText: 'We could not confirm that your response was received' }),
    ).toBeVisible();
    await shot(page, 'request-unconfirmed', w);
    await page.getByRole('button', { name: 'Send response' }).click();
    await expect(page.getByText('Nothing was sent twice.')).toBeVisible();
    expect(mock.responses.size).toBe(1);
  });

  test('answered and withdrawn requests are read-only', async ({ page }, testInfo) => {
    const w = width(testInfo.project.name);
    await openRequest(page, ANSWERED_REQUEST_ID);
    await expect(page.getByRole('heading', { name: 'Your response' })).toBeVisible();
    await expect(page.getByText('invoice-0912.pdf')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Send response' })).toHaveCount(0);
    await shot(page, 'request-answered', w);

    await page.goto(TAXPAYER + `/taxpayer/requests/${WITHDRAWN_REQUEST_ID}`);
    await pageReady(page, 'Evidence request');
    await expect(page.getByText('This request is no longer needed')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Send response' })).toHaveCount(0);
    await accessible(page);
    await shot(page, 'request-withdrawn', w);
  });

  test("denied access: another taxpayer's request is concealed as not found", async ({
    page,
  }, testInfo) => {
    const w = width(testInfo.project.name);
    await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(TAXPAYER + `/taxpayer/requests/${OTHER_TAXPAYER_REQUEST_ID}`);
    await pageReady(page, 'Request not found');
    await expect(page.getByText('Message from the officer')).toHaveCount(0);
    await accessible(page);
    await shot(page, 'request-not-found', w);
  });

  test("the server's evidence refusal is shown and nothing is marked sent", async ({ page }) => {
    const mock = await openRequest(page);
    await uploadFor(page, 1, 'invoice-2026-09-001.pdf');
    await declineItem2(page);
    mock.evidence.length = 0; // the server no longer recognises the document as the caller's own
    await page.getByRole('button', { name: 'Send response' }).click();
    await expect(
      page.getByRole('alert').filter({ hasText: 'A document could not be found' }),
    ).toBeVisible();
    await expect(page.getByRole('button', { name: 'Send response' })).toBeVisible();
    expect(mock.responses.size).toBe(0);
  });

  test('keyboard: answer and send without a mouse', async ({ page }) => {
    await openRequest(page);
    await uploadFor(page, 1, 'invoice-2026-09-001.pdf');
    const decline = page.getByLabel('I cannot provide this document (item 2)');
    await decline.focus();
    await page.keyboard.press('Space');
    await expect(decline).toBeChecked();
    await page.getByLabel("Why can't you provide it? (item 2)").focus();
    await page.keyboard.type('Synthetic: never issued.');
    await page.getByRole('button', { name: 'Send response' }).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'Your response' })).toBeFocused();
  });
});
