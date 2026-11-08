import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test as base, type Locator, type Page } from '@playwright/test';
import {
  hasVisibleFocus,
  horizontalOverflow,
  pageReady,
  seriousAxeViolations,
} from './support/checks';
import {
  DECLARATION,
  field,
  fillDetails,
  fillInvoice,
  pdf,
  toInvoices,
  upload,
} from './support/claim-flow';
import { AUDITOR, TAXPAYER } from './support/env';
import { startFixtureApi, useFixtureApi } from './support/fixture-api';
import { auditorCase, isMobile } from './support/matrix';
import { mockTaxpayerSession, OPEN_REQUEST_ID } from './support/sessions';

// TAX-055 end-to-end regression: the five synthetic scenario routes
// (data/scenarios/cases.json) walked through the auditor workspace from queue to
// recorded outcome, and the taxpayer journey from claim to evidence response,
// at every matrix width, with axe/reflow checks on every state.
//
// - Auditor: built app + the synthetic fixture API. Each test gets its own fresh
//   fixture server (support/fixture-api.ts), because approvals and evidence
//   requests change server state.
// - Taxpayer: the session mocks (support/sessions.ts), as in the other specs.
// - The two portals are not connected in fixture mode: an evidence request an
//   officer records on the auditor side is not delivered to the taxpayer mock.
//   Each half is tested against its own synthetic source (see README).
//
// CAPTURE_SCREENSHOTS=1 writes review screenshots to docs/evidence/TAX-055.
const capture = Boolean(process.env.CAPTURE_SCREENSHOTS);
const out = path.resolve(__dirname, '../../docs/evidence/TAX-055');
const width = (project: string) => project.split('-')[1];

interface Scenario {
  id: string;
  name: string;
  scenario: string;
  refund_claim: string;
  expected: string;
}
const SCENARIOS = JSON.parse(
  readFileSync(path.resolve(__dirname, '../../data/scenarios/cases.json'), 'utf8'),
) as Scenario[];
const scenario = (id: string) => {
  const found = SCENARIOS.find((s) => s.id === id);
  if (!found) throw new Error(`No scenario ${id}`);
  return { ...found, case: auditorCase(id) };
};

const lsl = (amount: string) =>
  `LSL ${Number(amount).toLocaleString('en-US', { minimumFractionDigits: 2 })}`;

/** Accusatory or verdict wording that must never appear for a finding or a taxpayer. */
const ACCUSATION = /fraud|evasion|guilty|criminal|penalt|suspicious/i;

const test = base.extend<{ isolatedApi: void }>({
  isolatedApi: [
    async ({ page }, use) => {
      const api = await startFixtureApi();
      await useFixtureApi(page, api);
      await use();
      api.stop();
    },
    { auto: false },
  ],
});

async function accessible(page: Page) {
  expect(await seriousAxeViolations(page)).toEqual([]);
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
}

async function shot(page: Page, name: string, w: string, target?: Locator) {
  if (!capture) return;
  const file = path.join(out, `${name}-${w}.png`);
  if (target) await target.screenshot({ path: file });
  else await page.screenshot({ path: file, fullPage: true });
}

async function actAs(page: Page, role: 'Auditor' | 'Specialist' | 'Supervisor') {
  await page.getByRole('combobox', { name: 'Acting as (fixture)' }).selectOption({ label: role });
  await expect(page.getByText(`Reviewing as ${role}`)).toBeVisible();
}

const decision = (page: Page) => page.getByRole('region', { name: /decision/i }).first();
const dialog = (page: Page) => page.getByRole('dialog');
const caseStatus = (page: Page, label: string) =>
  page.getByRole('main').getByText(label, { exact: true }).first();

/**
 * The common route: queue row → case overview → evidence tab → review page.
 * Returns once the review page is ready.
 */
async function walkToReview(page: Page, s: ReturnType<typeof scenario>, w: string) {
  const slug = s.id.toLowerCase();
  await page.goto(AUDITOR + '/auditor/cases');
  await pageReady(page, 'My case queue');
  const row = page.getByRole('row').filter({ has: page.getByRole('link', { name: s.id }) });
  await expect(row).toContainText(s.name);
  await expect(row).toContainText(lsl(s.refund_claim));
  await accessible(page);

  await row.getByRole('link', { name: s.id }).click();
  await pageReady(page, `${s.id} · ${s.name}`);
  const why = page.getByRole('region', { name: 'Why this case was selected' });
  await expect(why).toContainText('They are not findings, assessments or decisions.');
  await expect(page.getByRole('region', { name: 'Case summary' })).toContainText(
    lsl(s.refund_claim),
  );
  await accessible(page);
  await shot(page, `${slug}-1-case`, w);

  await page.getByRole('tab', { name: 'Evidence' }).click();
  await expect(page.getByRole('tabpanel', { name: 'Evidence' })).toBeVisible();
  await page.waitForLoadState('networkidle');
  await accessible(page);
  await shot(page, `${slug}-2-evidence`, w, page.getByRole('tabpanel', { name: 'Evidence' }));

  await page.getByRole('tab', { name: 'Findings' }).click();
  const findings = page.getByRole('tabpanel', { name: 'Findings' });
  await expect(findings).toBeVisible();
  await page.waitForLoadState('networkidle');
  await expect(findings).not.toContainText(ACCUSATION);
  await accessible(page);
  await shot(page, `${slug}-3-findings`, w, findings);

  await page
    .getByRole('link', { name: 'Review and decide' })
    .or(page.getByRole('button', { name: 'Review and decide' }))
    .click();
  await pageReady(page, `Review and decide · ${s.id}`);
  await expect(page.getByText('Reviewing as Auditor')).toBeVisible();
  const suggestions = page.getByRole('region', { name: 'Suggestions to consider' });
  await expect(suggestions).toContainText('Suggestions never act on their own.');
  await expect(suggestions).toContainText('(not legal authority)');
  await expect(suggestions).not.toContainText(ACCUSATION);
  await accessible(page);
  await shot(page, `${slug}-4-review`, w);
}

/** Open a confirm dialog with `trigger`, check it, then confirm with `confirm`. */
async function confirmIn(
  page: Page,
  trigger: Locator,
  title: string,
  confirm: string,
  shotName: string,
  w: string,
) {
  await trigger.click();
  const d = dialog(page);
  await expect(d.getByRole('heading', { name: title })).toBeVisible();
  await accessible(page);
  await shot(page, shotName, w, d);
  await d.getByRole('button', { name: confirm }).click();
  await expect(d).toHaveCount(0);
}

/** The outcome message, announced through the panel's polite live region. */
async function result(page: Page, title: string) {
  const alert = page.getByRole('status').filter({ hasText: title });
  await expect(alert).toBeVisible();
  return alert;
}

/** Use the suggestion whose finding heading matches `finding` as a starting point. */
async function startFromSuggestion(page: Page, finding: RegExp | string) {
  const item = page
    .getByRole('region', { name: 'Suggestions to consider' })
    .getByRole('listitem')
    .filter({ has: page.getByRole('heading', { name: finding }) });
  await item.getByRole('button', { name: 'Use as a starting point' }).click();
}

async function completeEvidenceRequest(page: Page, description: string) {
  const form = page.getByRole('form', { name: 'Request evidence' });
  const doc = form.getByRole('textbox', { name: /^Document 1 description/ });
  if (!(await doc.inputValue())) await doc.fill(description);
  const message = form.getByRole('textbox', { name: /^Message to the taxpayer/ });
  if (!(await message.inputValue())) {
    await message.fill(
      'Synthetic: please upload the document listed so we can finish reviewing your claim.',
    );
  }
  return form;
}

test.describe('five scenario routes: auditor workspace (TAX-055)', () => {
  test('SYN-001 legitimate refund → human clearance by a different supervisor', async ({
    page,
    isolatedApi,
  }, testInfo) => {
    void isolatedApi;
    const w = width(testInfo.project.name);
    const s = scenario('SYN-001');
    expect(s.expected).toBe('human_clearance');
    await walkToReview(page, s, w);

    // The proposing auditor cannot approve their own clearance (separation of duties).
    await expect(
      decision(page).getByRole('heading', { name: 'Decision waiting for approval' }),
    ).toBeVisible();
    await expect(page.getByRole('button', { name: 'Approve…' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Approve…' })).toHaveAccessibleDescription(
      /a different supervisor must approve/,
    );

    await actAs(page, 'Supervisor');
    await expect(page.getByRole('button', { name: 'Approve…' })).toBeEnabled();
    await accessible(page);
    await shot(page, 'syn-001-5-supervisor', w, decision(page));
    await confirmIn(
      page,
      page.getByRole('button', { name: 'Approve…' }),
      'Approve this decision?',
      'Approve and apply in Jube',
      'syn-001-6-approve-dialog',
      w,
    );
    await result(page, 'Decision approved');
    await expect(
      page.getByText('This case is resolved. No further decision can be proposed.'),
    ).toBeVisible();
    await expect(caseStatus(page, 'Resolved')).toBeVisible();
    // The disabled suggestion gives the real reason: nothing is waiting (TAX-104).
    const use = page
      .getByRole('region', { name: 'Suggestions to consider' })
      .getByRole('button', { name: 'Use as a starting point' })
      .first();
    await expect(use).toBeDisabled();
    await expect(use).toHaveAccessibleDescription('This case is resolved.');
    await accessible(page);
    await shot(page, 'syn-001-7-resolved', w);
  });

  test('SYN-002 related-party risk → targeted evidence request', async ({
    page,
    isolatedApi,
  }, testInfo) => {
    void isolatedApi;
    const w = width(testInfo.project.name);
    const s = scenario('SYN-002');
    expect(s.expected).toBe('targeted_evidence_request');
    await walkToReview(page, s, w);

    await startFromSuggestion(page, /^No synthetic service agreement was supplied/);
    const form = await completeEvidenceRequest(
      page,
      'Synthetic: the signed management services agreement with the related company.',
    );
    await expect(
      form.getByRole('checkbox', { name: /^No synthetic service agreement was supplied/ }),
    ).toBeChecked();
    await accessible(page);
    await shot(page, 'syn-002-5-request-form', w, form);
    await confirmIn(
      page,
      form.getByRole('button', { name: 'Review request…' }),
      'Confirm the evidence request',
      'Record evidence request',
      'syn-002-6-request-dialog',
      w,
    );
    const alert = await result(page, 'Evidence request recorded');
    await expect(alert).toContainText('the case status is unchanged');
    await expect(caseStatus(page, 'Investigating')).toBeVisible();
    await expect(
      page.getByRole('list', { name: 'Review trail, newest first' }).getByRole('listitem').first(),
    ).toContainText(/evidence/i);
    await accessible(page);
    await shot(page, 'syn-002-7-recorded', w);
  });

  test('SYN-003 ambiguous service → specialist review', async ({ page, isolatedApi }, testInfo) => {
    void isolatedApi;
    const w = width(testInfo.project.name);
    const s = scenario('SYN-003');
    expect(s.expected).toBe('specialist_review');
    await walkToReview(page, s, w);

    const suggestions = page.getByRole('region', { name: 'Suggestions to consider' });
    await expect(suggestions).toContainText('No verified legal route');
    await expect(suggestions).toContainText(
      'Suggested next step: Refer to a specialist for review',
    );
    await startFromSuggestion(page, /^Synthetic payslips and contractor invoices/);
    const form = page.getByRole('form', { name: 'Propose a decision' });
    await expect(form.getByRole('radio', { name: /^Refer to a specialist/ })).toBeChecked();
    await form
      .getByRole('textbox', { name: /^Rationale in your own words/ })
      .fill(
        'Synthetic: the payments fit both employment and consultancy; a specialist should classify them.',
      );
    await accessible(page);
    await shot(page, 'syn-003-5-propose-form', w, form);
    await confirmIn(
      page,
      form.getByRole('button', { name: 'Review and propose…' }),
      'Confirm the proposed decision',
      'Propose decision',
      'syn-003-6-propose-dialog',
      w,
    );
    await result(page, 'Decision proposed');

    // A specialist can see the proposal but cannot approve it (TAX-029 roles).
    await actAs(page, 'Specialist');
    await expect(
      decision(page).getByRole('heading', { name: 'Decision waiting for approval' }),
    ).toBeVisible();
    await expect(page.getByRole('button', { name: 'Approve…' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Reject…' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Approve…' })).toHaveAccessibleDescription(
      'Only a supervisor who did not propose this decision can approve or reject it.',
    );
    await accessible(page);
    await shot(page, 'syn-003-7-specialist', w, decision(page));

    await actAs(page, 'Supervisor');
    await confirmIn(
      page,
      page.getByRole('button', { name: 'Approve…' }),
      'Approve this decision?',
      'Approve and apply in Jube',
      'syn-003-7-approve-dialog',
      w,
    );
    await result(page, 'Decision approved');
    await accessible(page);
    await shot(page, 'syn-003-8-referred', w);
  });

  test('SYN-004 imported-services gap → targeted evidence request, keyboard only', async ({
    page,
    isolatedApi,
  }, testInfo) => {
    void isolatedApi;
    const w = width(testInfo.project.name);
    const s = scenario('SYN-004');
    expect(s.expected).toBe('targeted_evidence_request');
    await walkToReview(page, s, w);

    // The case already waits for evidence, so that outcome is unavailable, with a reason.
    await expect(
      page.getByRole('radio', { name: /^Wait for further evidence The case is already/ }),
    ).toBeDisabled();

    // Keyboard only from here: suggestion → form → dialog → confirm.
    const use = page
      .getByRole('region', { name: 'Suggestions to consider' })
      .getByRole('button', { name: 'Use as a starting point' })
      .first();
    await use.focus();
    await page.keyboard.press('Enter');
    const form = page.getByRole('form', { name: 'Request evidence' });
    const doc = form.getByRole('textbox', { name: /^Document 1 description/ });
    await doc.focus();
    if (!(await doc.inputValue())) {
      await page.keyboard.type(
        'Synthetic: the withholding or reverse-charge record for the imported service.',
      );
    }
    const message = form.getByRole('textbox', { name: /^Message to the taxpayer/ });
    await message.focus();
    if (!(await message.inputValue())) {
      await page.keyboard.type('Synthetic: please upload the record listed.');
    }
    const review = form.getByRole('button', { name: 'Review request…' });
    await review.focus();
    await page.keyboard.press('Enter');
    const d = dialog(page);
    await expect(d.getByRole('heading', { name: 'Confirm the evidence request' })).toBeVisible();
    // Focus is inside the dialog; Escape cancels and keeps the draft.
    await expect.poll(() => d.evaluate((el) => el.contains(document.activeElement))).toBe(true);
    await page.keyboard.press('Escape');
    await expect(d).toHaveCount(0);
    await expect(message).not.toHaveValue('');

    await review.focus();
    await page.keyboard.press('Enter');
    const confirm = d.getByRole('button', { name: 'Record evidence request' });
    await confirm.focus();
    await page.keyboard.press('Enter');
    await result(page, 'Evidence request recorded');
    await expect(caseStatus(page, 'Awaiting evidence')).toBeVisible();
    await accessible(page);
    await shot(page, 'syn-004-5-recorded', w);
  });

  test('SYN-005 unverified legal route → policy-gap review by a supervisor', async ({
    page,
    isolatedApi,
  }, testInfo) => {
    void isolatedApi;
    const w = width(testInfo.project.name);
    const s = scenario('SYN-005');
    expect(s.expected).toBe('policy_gap_review');
    await walkToReview(page, s, w);

    const suggestions = page.getByRole('region', { name: 'Suggestions to consider' });
    await expect(suggestions).toContainText('No verified legal route');
    await expect(suggestions).toContainText(
      'Suggested next step: Review the policy gap with a supervisor',
    );

    // The supervisor rejects continuing the investigation; a reason is required.
    await actAs(page, 'Supervisor');
    await page.getByRole('button', { name: 'Reject…' }).click();
    const reason = page.getByRole('textbox', { name: /^Reason for rejecting/ });
    await page.getByRole('button', { name: 'Review rejection…' }).click();
    await expect(page.getByText(/Give the reason for rejecting/)).toBeVisible();
    await expect(reason).toBeFocused();
    await reason.fill('Synthetic: record this as a policy gap for supervisor review instead.');
    await accessible(page);
    await shot(page, 'syn-005-5-reject-reason', w, decision(page));
    await confirmIn(
      page,
      page.getByRole('button', { name: 'Review rejection…' }),
      'Reject this decision?',
      'Reject decision',
      'syn-005-6-reject-dialog',
      w,
    );
    const alert = await result(page, 'Decision rejected');
    await expect(alert).toContainText('Nothing was sent to Jube.');
    // The case is unchanged and open for a new proposal.
    await expect(caseStatus(page, 'Awaiting officer review')).toBeVisible();
    await expect(page.getByRole('form', { name: 'Propose a decision' })).toBeVisible();
    await accessible(page);
    await shot(page, 'syn-005-7-rejected', w);
  });
});

test.describe('taxpayer journey (TAX-055)', () => {
  test('claim with complete evidence, then answer an evidence request', async ({
    page,
  }, testInfo) => {
    // Two portal flows in one test; allow for the dev server under parallel load.
    test.slow();
    const w = width(testInfo.project.name);
    const mobile = isMobile(testInfo.project.name);
    const s = scenario('SYN-001');
    const mock = await mockTaxpayerSession(page, 'taxpayer');
    // The sidebar on wider screens, the bottom bar (shorter labels) below 768px.
    const nav = page.getByRole('navigation', { name: 'Taxpayer portal' }).filter({ visible: true });

    await page.goto(TAXPAYER + '/taxpayer/submissions');
    await pageReady(page, 'My cases');
    await accessible(page);
    await shot(page, 'taxpayer-1-submissions', w);

    await nav
      .getByRole('link', { name: mobile ? 'Simulator' : 'Online Refunds simulator' })
      .click();
    await pageReady(page, 'Online Refunds simulator');
    await fillDetails(page, {
      claimed: s.refund_claim,
      outputVat: '0',
      inputVat: s.refund_claim,
    });
    const invoice = await upload(page, pdf('synthetic-invoice-2026-09-001.pdf'));
    await expect(invoice.getByText('Uploaded')).toBeVisible();
    const contract = await upload(page, pdf('synthetic-supply-contract-2026.pdf'));
    await expect(contract.getByText('Uploaded')).toBeVisible();
    await accessible(page);
    await shot(page, 'taxpayer-2-documents', w);

    await toInvoices(page);
    const row = page.getByRole('group', { name: 'Invoice 1' });
    const values: Record<string, string> = {
      'Invoice number': 'INV-SYN-001-0912',
      'Invoice date': '2026-09-12',
      Supplier: 'Synthetic Highland Supplies',
      'Amount excluding VAT (LSL)': '100000.00',
      'VAT (LSL)': s.refund_claim,
      'Total including VAT (LSL)': '115000.00',
      'Amount paid (LSL, optional)': '115000.00',
    };
    for (const [label, value] of Object.entries(values)) await field(row, label).fill(value);
    await chooseOption(
      page,
      row.getByRole('combobox', { name: /^Invoice document/ }),
      'synthetic-invoice-2026-09-001.pdf',
    );
    await chooseOption(
      page,
      row.getByRole('combobox', { name: /^Contract or purchase order/ }),
      'synthetic-supply-contract-2026.pdf',
    );
    await expect(page.getByLabel('Invoice totals')).toContainText('LSL 115,000.00');
    await accessible(page);
    await shot(page, 'taxpayer-3-invoices', w);

    await page.getByRole('button', { name: 'Continue to review' }).click();
    const checks = page.getByRole('region', { name: 'Before you send' });
    await expect(checks.getByText('Your figures add up')).toBeVisible();
    await expect(checks).toContainText(lsl(s.refund_claim));
    await expect(checks).not.toContainText(ACCUSATION);
    await expect(checks).not.toContainText(/lane/i);
    await accessible(page);
    await shot(page, 'taxpayer-4-review', w);

    await page.getByLabel(DECLARATION).check();
    await page.getByRole('button', { name: 'Send claim' }).click();
    await expect(page.getByRole('heading', { name: 'Claim sent' })).toBeFocused();
    await expect(page.getByText('REF-2026-')).toBeVisible();
    await accessible(page);
    await shot(page, 'taxpayer-5-sent', w);
    expect(mock.claims.size).toBe(1);

    // Evidence requests through the portal navigation (bottom bar below 768px).
    await nav.getByRole('link', { name: mobile ? 'Requests' : 'Evidence requests' }).click();
    await pageReady(page, 'Evidence requests');
    await expect(page.getByText(ACCUSATION)).toHaveCount(0);
    await page.goto(TAXPAYER + `/taxpayer/requests/${OPEN_REQUEST_ID}`);
    await pageReady(page, 'Evidence request');
    await page
      .getByLabel('Choose a file for item 1')
      .setInputFiles(pdf('synthetic-agreement-2026.pdf'));
    await expect(
      page
        .getByRole('list', { name: 'Documents for item 1' })
        .getByRole('listitem')
        .filter({ hasText: 'synthetic-agreement-2026.pdf' })
        .getByText('Uploaded', { exact: true }),
    ).toBeVisible();
    await page.getByLabel('I cannot provide this document (item 2)').check();
    await page
      .getByLabel("Why can't you provide it? (item 2)")
      .fill('Synthetic: the bank account was closed.');
    await accessible(page);
    await shot(page, 'taxpayer-6-respond', w);
    await page.getByRole('button', { name: 'Send response' }).click();
    await expect(page.getByRole('heading', { name: 'Your response' })).toBeFocused();
    await expect(page.getByText('Response sent', { exact: true }).first()).toBeVisible();
    await accessible(page);
    await shot(page, 'taxpayer-7-responded', w);
    expect(mock.responses.size).toBe(1);
  });
});

test.describe('accessibility states (TAX-055)', () => {
  test('forced colours and reduced motion: approval dialog and claim checks stay usable', async ({
    page,
    isolatedApi,
  }, testInfo) => {
    void isolatedApi;
    test.slow();
    const w = width(testInfo.project.name);
    await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' });

    // Auditor: SYN-001 approval dialog, opened and confirmed from the keyboard.
    await page.goto(AUDITOR + `/auditor/cases/${scenario('SYN-001').case.case_id}/review`);
    await pageReady(page, 'Review and decide · SYN-001');
    await actAs(page, 'Supervisor');
    const approve = page.getByRole('button', { name: 'Approve…' });
    await approve.focus();
    expect(await hasVisibleFocus(approve)).toBe(true);
    await page.keyboard.press('Enter');
    const d = dialog(page);
    await expect(d.getByRole('heading', { name: 'Approve this decision?' })).toBeVisible();
    await accessible(page);
    await shot(page, 'forced-colors-approve-dialog', w, d);
    const confirm = d.getByRole('button', { name: 'Approve and apply in Jube' });
    await confirm.focus();
    expect(await hasVisibleFocus(confirm)).toBe(true);
    await page.keyboard.press('Enter');
    await result(page, 'Decision approved');

    // Taxpayer: the pre-submission checks with prompts.
    await mockTaxpayerSession(page, 'taxpayer');
    await page.goto(TAXPAYER + '/taxpayer/submissions/new');
    await pageReady(page, 'Online Refunds simulator');
    await fillDetails(page);
    await (await upload(page)).getByText('Uploaded').waitFor();
    await toInvoices(page);
    await fillInvoice(page);
    await page.getByRole('button', { name: 'Continue to review' }).click();
    const checks = page.getByRole('region', { name: 'Before you send' });
    await expect(checks.getByRole('list', { name: 'Things to check' })).toBeVisible();
    const change = checks.getByRole('button', { name: /^Change/ }).first();
    // Reach it by keyboard so :focus-visible applies, as it would for a keyboard user.
    await change.focus();
    await page.keyboard.press('Shift+Tab');
    await page.keyboard.press('Tab');
    await expect(change).toBeFocused();
    expect(await hasVisibleFocus(change)).toBe(true);
    await accessible(page);
    await shot(page, 'forced-colors-claim-checks', w, checks);
  });
});

/** frappe-ui select (combobox + listbox): open and choose an option by name. */
async function chooseOption(page: Page, combobox: Locator, option: string) {
  await combobox.click();
  await page.getByRole('option', { name: option }).click();
  await expect(combobox).toContainText(option);
}
