import path from 'node:path';
import { expect, test as base, type Locator, type Page } from '@playwright/test';
import { horizontalOverflow, pageReady, seriousAxeViolations } from './support/checks';
import { DECLARATION, field, pdf } from './support/claim-flow';
import { AUDITOR, TAXPAYER } from './support/env';
import { startFixtureApi, useFixtureApi } from './support/fixture-api';
import { isMobile } from './support/matrix';
import { ProxyUser } from './support/proxy';
import { mockTaxpayerSession } from './support/sessions';

// TAX-082 persona onboarding walkthroughs, measured with scripted proxy users
// (support/proxy.ts). Each persona starts at its portal's entrance and reaches
// every goal through the screen alone: no URLs after the entrance, no knowledge
// of internal IDs. The measurements (actions, Tab stops, estimated effort and
// friction) are attached to each test; RECORD_DIR also writes them as JSON.
//
// A walkthrough fails only when a goal cannot be completed. Friction is
// recorded, not asserted: it is evidence for review and remediation tasks
// (docs/evidence/TAX-082/README.md), and some of it is expected today.
//
// Synthetic data only: the taxpayer session mocks (support/sessions.ts) and the
// auditor fixture API (scripts/dev.py), a fresh one per auditor walkthrough.
//
// CAPTURE_SCREENSHOTS=1 writes review screenshots to docs/evidence/TAX-082.
const capture = Boolean(process.env.CAPTURE_SCREENSHOTS);
const out = path.resolve(__dirname, '../../docs/evidence/TAX-082');
const width = (project: string) => project.split('-')[1];

const test = base.extend<{ isolatedApi: void }>({
  isolatedApi: [
    async ({ page }, use) => {
      const api = await startFixtureApi();
      await useFixtureApi(page, api);
      await use();
      // Drop the routes first, so a request still in flight cannot fail the test.
      await page.unrouteAll({ behavior: 'ignoreErrors' });
      api.stop();
    },
    { auto: false },
  ],
});

async function shot(page: Page, name: string, w: string) {
  if (capture) await page.screenshot({ path: path.join(out, `${name}-${w}.png`) });
}

/** Record serious axe violations and page-wide sideways scrolling as friction. */
async function audit(proxy: ProxyUser, where: string) {
  for (const violation of await seriousAxeViolations(proxy.page)) {
    proxy.friction(`Accessibility (${where}): ${violation}`);
  }
  const overflow = await horizontalOverflow(proxy.page);
  if (overflow > 0) proxy.friction(`${where} scrolls sideways by ${overflow}px`);
}

/** The visible portal navigation: the sidebar, or the Menu drawer below 1024px (TAX-146). */
const portalNav = (page: Page) =>
  page.getByRole('navigation', { name: 'Taxpayer portal' }).filter({ visible: true });

/** Below 1024px the navigation is behind the header's Menu button: one more action. */
async function openMenu(proxy: ProxyUser, page: Page, mobile: boolean) {
  if (mobile) await proxy.click(page.getByRole('button', { name: 'Menu', exact: true }), 'Menu');
}

test.describe('taxpayer onboarding (TAX-082)', () => {
  test('first-time taxpayer, pointer', async ({ page }, testInfo) => {
    test.slow();
    const w = width(testInfo.project.name);
    const mobile = isMobile(testInfo.project.name);
    const mock = await mockTaxpayerSession(page, 'taxpayer');
    const proxy = new ProxyUser(
      page,
      'taxpayer-first-visit',
      'A business owner using the portal for the first time, with a mouse or touch.',
      'pointer',
    );
    const nav = portalNav(page);

    await proxy.run(
      'check-status',
      'Find out where my submission is and what happens next',
      async () => {
        // TAX-125: the portal opens on evidence requests; cases are one step away.
        await page.goto(TAXPAYER + '/taxpayer/');
        await pageReady(page, 'Evidence requests');
        const open = page.getByText('Response needed');
        if ((await open.count()) === 0) {
          proxy.friction(
            'The landing page (Evidence requests) does not show that a request is waiting for a response',
          );
        }
        await shot(page, 'taxpayer-1-landing', w);
        await openMenu(proxy, page, mobile);
        await proxy.click(nav.getByRole('link', { name: 'My cases' }), 'nav: My cases');
        await pageReady(page, 'My cases');
        await proxy.click(
          page.getByRole('list', { name: 'My cases' }).getByRole('link').first(),
          'first submission',
        );
        await expect(page.getByText('What happens next')).toBeVisible();
        await audit(proxy, 'submission status');
      },
    );

    await proxy.run('answer-request', 'Answer the evidence request an officer sent', async () => {
      await openMenu(proxy, page, mobile);
      await proxy.click(
        nav.getByRole('link', { name: 'Evidence requests' }),
        'nav: Evidence requests',
      );
      await pageReady(page, 'Evidence requests');
      await proxy.click(
        page
          .getByRole('list', { name: 'Evidence requests' })
          .getByRole('link')
          .filter({ hasText: 'Response needed' })
          .first(),
        'request with "Response needed"',
      );
      await pageReady(page, 'Evidence request');
      await proxy.upload(
        page.getByLabel('Choose a file for item 1'),
        pdf('synthetic-agreement-2026.pdf'),
        'upload for item 1',
      );
      await expect(
        page
          .getByRole('list', { name: 'Documents for item 1' })
          .getByText('Uploaded', { exact: true }),
      ).toBeVisible();
      await proxy.check(
        page.getByLabel('I cannot provide this document (item 2)'),
        'cannot provide item 2',
      );
      await proxy.fill(
        page.getByLabel("Why can't you provide it? (item 2)"),
        'Synthetic: the bank account was closed.',
        'reason for item 2',
      );
      await audit(proxy, 'evidence response');
      await shot(page, 'taxpayer-2-respond', w);
      await proxy.click(page.getByRole('button', { name: 'Send response' }), 'Send response');
      await expect(page.getByText('Response sent', { exact: true }).first()).toBeVisible();
      expect(mock.responses.size).toBe(1);
    });

    await proxy.run('send-claim', 'Send a VAT refund claim with one invoice', async () => {
      await openMenu(proxy, page, mobile);
      await proxy.click(
        nav.getByRole('link', { name: 'Online Refunds simulator' }),
        'nav: Online Refunds simulator',
      );
      await pageReady(page, 'Online Refunds simulator');
      await proxy.fill(page.getByLabel('Tax period'), '2026-09', 'Tax period');
      await proxy.fill(page.getByLabel('Amount claimed (LSL)'), '48210.55', 'Amount claimed');
      await proxy.fill(page.getByLabel('Output VAT for the period (LSL)'), '0', 'Output VAT');
      await proxy.fill(page.getByLabel('Input VAT for the period (LSL)'), '48210.55', 'Input VAT');
      await proxy.choose(
        page.getByRole('combobox', { name: 'Reason for the claim' }),
        'More input VAT than output VAT',
        'Reason for the claim',
      );
      await proxy.click(
        page.getByRole('button', { name: 'Continue to documents' }),
        'Continue to documents',
      );
      await proxy.upload(page.getByLabel('Choose a file to upload'), pdf(), 'upload invoice');
      await expect(
        page.getByRole('list', { name: 'Supporting documents' }).getByText('Uploaded').first(),
      ).toBeVisible();
      await proxy.click(
        page.getByRole('button', { name: 'Continue to invoices' }),
        'Continue to invoices',
      );
      const row = page.getByRole('group', { name: 'Invoice 1' });
      const invoice: Record<string, string> = {
        'Invoice number': 'INV-2026-0912',
        'Invoice date': '2026-09-12',
        Supplier: 'Synthetic Highland Supplies',
        'Amount excluding VAT (LSL)': '321403.67',
        'VAT (LSL)': '48210.55',
        'Total including VAT (LSL)': '369614.22',
      };
      for (const [label, value] of Object.entries(invoice)) {
        await proxy.fill(field(row, label), value, label);
      }
      await audit(proxy, 'invoice schedule');
      await shot(page, 'taxpayer-3-invoices', w);
      await proxy.click(
        page.getByRole('button', { name: 'Continue to review' }),
        'Continue to review',
      );
      await expect(page.getByRole('heading', { name: 'Before you send' })).toBeVisible();
      await proxy.check(page.getByLabel(DECLARATION), 'declaration');
      await proxy.click(page.getByRole('button', { name: 'Send claim' }), 'Send claim');
      await expect(page.getByRole('heading', { name: 'Claim sent' })).toBeFocused();
      await audit(proxy, 'claim sent');
      await shot(page, 'taxpayer-4-sent', w);
      expect(mock.claims.size).toBe(1);
    });

    await proxy.run('find-new-claim', 'Find the claim I just sent in my submissions', async () => {
      await openMenu(proxy, page, mobile);
      await proxy.click(nav.getByRole('link', { name: 'My cases' }), 'nav: My cases');
      await pageReady(page, 'My cases');
      const first = page.getByRole('list', { name: 'My cases' }).getByRole('link').first();
      await expect(first).toContainText('REF-2026-');
      await expect(first).toContainText('Under review');
    });

    await proxy.run('find-logout', 'Find my account details and how to log out', async () => {
      await openMenu(proxy, page, mobile);
      await proxy.click(nav.getByRole('link', { name: 'Account' }), 'nav: Account');
      await pageReady(page, 'Account');
      await expect(page.getByRole('main').getByRole('button', { name: 'Log out' })).toBeVisible();
      await audit(proxy, 'account');
    });

    await proxy.record(testInfo);
    proxy.expectAllCompleted();
  });

  test('taxpayer using the keyboard only', async ({ page }, testInfo) => {
    test.slow();
    const w = width(testInfo.project.name);
    const mobile = isMobile(testInfo.project.name);
    const mock = await mockTaxpayerSession(page, 'taxpayer');
    const proxy = new ProxyUser(
      page,
      'taxpayer-keyboard',
      'A taxpayer who uses the keyboard only (screen-reader or motor-impairment proxy).',
      'keyboard',
    );
    const nav = portalNav(page);

    await proxy.run(
      'check-status',
      'Open my first submission and read what happens next',
      async () => {
        await page.goto(TAXPAYER + '/taxpayer/submissions');
        await pageReady(page, 'My cases');
        await proxy.tabTo(
          page.getByRole('link', { name: 'Skip to main content' }),
          'Skip to main content',
        );
        await proxy.press('Enter');
        await proxy.tabTo(
          page.getByRole('list', { name: 'My cases' }).getByRole('link').first(),
          'first submission',
        );
        await proxy.press('Enter');
        await expect(page.getByText('What happens next')).toBeVisible();
      },
    );

    await proxy.run('answer-request', 'Answer an evidence request without a mouse', async () => {
      // Below 1024px the navigation opens from the header's Menu button (TAX-146).
      if (mobile) {
        await proxy.tabTo(page.getByRole('button', { name: 'Menu', exact: true }), 'Menu', {
          backwards: true,
        });
        await proxy.press('Enter');
      }
      const link = nav.getByRole('link', { name: 'Evidence requests' });
      await proxy.tabTo(link, 'nav: Evidence requests');
      await proxy.press('Enter');
      await pageReady(page, 'Evidence requests');
      await proxy.tabTo(
        page.getByRole('link').filter({ hasText: 'Response needed' }).first(),
        'request with "Response needed"',
      );
      await proxy.press('Enter');
      await pageReady(page, 'Evidence request');
      const chooser = page.getByLabel('Choose a file for item 1');
      await proxy.tabTo(chooser, 'file chooser for item 1');
      // Enter would open the operating-system dialog, which the browser cannot drive.
      await proxy.upload(chooser, pdf('synthetic-agreement-2026.pdf'), 'upload for item 1');
      await expect(
        page
          .getByRole('list', { name: 'Documents for item 1' })
          .getByText('Uploaded', { exact: true }),
      ).toBeVisible();
      await proxy.tabTo(
        page.getByLabel('I cannot provide this document (item 2)'),
        'cannot provide item 2',
      );
      await proxy.press('Space');
      await proxy.tabTo(page.getByLabel("Why can't you provide it? (item 2)"), 'reason for item 2');
      await proxy.type('Synthetic: the bank account was closed.');
      await proxy.tabTo(page.getByRole('button', { name: 'Send response' }), 'Send response');
      await shot(page, 'taxpayer-keyboard-send-focused', w);
      await proxy.press('Enter');
      await expect(page.getByRole('heading', { name: 'Your response' })).toBeFocused();
      expect(mock.responses.size).toBe(1);
    });

    await proxy.record(testInfo);
    proxy.expectAllCompleted();
  });
});

async function actAs(proxy: ProxyUser, role: 'Auditor' | 'Specialist' | 'Supervisor') {
  await proxy.select(
    proxy.page.getByRole('combobox', { name: 'Acting as (fixture)' }),
    role,
    `Acting as (fixture): ${role}`,
  );
}

/** The auditor workspace's sidebar link, opening the collapsed navigation first if needed. */
async function sidebarLink(proxy: ProxyUser, name: string): Promise<Locator> {
  const page = proxy.page;
  const link = page
    .getByRole('navigation', { name: 'Auditor workspace' })
    .getByRole('link', { name });
  if (!(await link.isVisible())) {
    const menu = page.getByRole('button', { name: /menu|navigation/i }).first();
    await proxy.click(menu, 'open navigation menu');
  }
  return link;
}

test.describe('officer onboarding (TAX-082)', () => {
  test('new auditor on a first case, pointer', async ({ page, isolatedApi }, testInfo) => {
    void isolatedApi;
    test.slow();
    const w = width(testInfo.project.name);
    const proxy = new ProxyUser(
      page,
      'auditor-first-case',
      'A newly appointed auditor opening the workspace for the first time and working one case.',
      'pointer',
    );

    await proxy.run(
      'find-case',
      'Find the case for Synthetic Border Retail and see why it was selected',
      async () => {
        await page.goto(AUDITOR + '/auditor/');
        await pageReady(page, 'Overview');
        const showcase = page
          .getByRole('navigation', { name: 'Auditor workspace' })
          .getByRole('link', { name: 'Component showcase' });
        if ((await showcase.count()) > 0) {
          proxy.friction(
            'The operational sidebar lists "Component showcase" (a design page) next to the case work',
          );
        }
        await audit(proxy, 'overview');
        await shot(page, 'auditor-1-overview', w);
        await proxy.click(
          page
            .getByRole('button', { name: 'Open my case queue' })
            .or(page.getByRole('link', { name: 'Open my case queue' })),
          'Open my case queue',
        );
        await pageReady(page, 'My case queue');
        await proxy.fill(
          page.getByRole('searchbox', { name: 'Search' }),
          'Border Retail',
          'Search',
        );
        // Like a person, read the filtered result before choosing it.
        await expect(page.getByText('Showing 1–1 of 1 cases')).toBeVisible();
        await expect(page.getByRole('row').filter({ hasText: 'SYN-002' })).toContainText(
          'Synthetic Border Retail',
        );
        const result = page.getByRole('link', { name: 'SYN-002' });
        await proxy.click(result, 'SYN-002');
        const heading = page.getByRole('heading', { level: 1, name: /^SYN-002 · / });
        // A person whose click seems to do nothing clicks again.
        if (
          !(await heading.waitFor({ timeout: 5_000 }).then(
            () => true,
            () => false,
          ))
        ) {
          proxy.friction(
            'Clicking a search result straight after typing left the queue open; a second click was needed',
          );
          await proxy.click(result, 'SYN-002 again');
        }
        await pageReady(page, /^SYN-002 · /);
        await expect(
          page.getByRole('region', { name: 'Why this case was selected' }),
        ).toBeVisible();
        await audit(proxy, 'case overview');
        await shot(page, 'auditor-2-case', w);
      },
    );

    await proxy.run('read-evidence', 'Open the evidence behind the case', async () => {
      await proxy.click(page.getByRole('tab', { name: 'Evidence' }), 'tab: Evidence');
      const panel = page.getByRole('tabpanel', { name: 'Evidence' });
      await expect(panel).toBeVisible();
      await page.waitForLoadState('networkidle');
      await proxy.click(panel.getByRole('link').first(), 'first evidence item');
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await page.waitForLoadState('networkidle');
      const original = page
        .getByRole('link', { name: /(open|view|download).*(original|document)/i })
        .or(page.getByRole('button', { name: /(open|view|download).*(original|document)/i }));
      if ((await original.count()) === 0) {
        proxy.friction(
          'The evidence page has no link or button to open the original document; it says the content is never shown in the workspace',
        );
      }
      await audit(proxy, 'evidence item');
      await shot(page, 'auditor-3-evidence', w);
      await proxy.click(
        page.getByRole('link', { name: /^SYN-002/ }).first(),
        'breadcrumb: back to case',
      );
      await pageReady(page, /^SYN-002 · /);
    });

    await proxy.run('view-taxpayer', "Look at the taxpayer's profile", async () => {
      await proxy.click(
        page.getByRole('link', { name: 'View taxpayer profile' }),
        'View taxpayer profile',
      );
      await pageReady(page, 'Synthetic Border Retail');
      await audit(proxy, 'taxpayer profile');
      const back = page.getByRole('link', { name: 'SYN-002' }).first();
      await proxy.click(back, 'SYN-002 from the profile');
      await pageReady(page, /^SYN-002 · /);
    });

    await proxy.run(
      'request-evidence',
      'Ask the taxpayer for the missing service agreement',
      async () => {
        await proxy.click(
          page
            .getByRole('link', { name: 'Review and decide' })
            .or(page.getByRole('button', { name: 'Review and decide' })),
          'Review and decide',
        );
        await pageReady(page, 'Review and decide · SYN-002');
        await proxy.click(
          page
            .getByRole('region', { name: 'Suggestions to consider' })
            .getByRole('listitem')
            .filter({ has: page.getByRole('heading', { name: /^No synthetic service agreement/ }) })
            .getByRole('button', { name: 'Use as a starting point' }),
          'Use as a starting point',
        );
        const form = page.getByRole('form', { name: 'Request evidence' });
        const doc = form.getByRole('textbox', { name: /^Document 1 description/ });
        if (!(await doc.inputValue())) {
          await proxy.fill(
            doc,
            'Synthetic: the signed management services agreement.',
            'Document 1 description',
          );
        }
        const message = form.getByRole('textbox', { name: /^Message to the taxpayer/ });
        if (!(await message.inputValue())) {
          await proxy.fill(
            message,
            'Synthetic: please upload the agreement listed.',
            'Message to the taxpayer',
          );
        }
        await audit(proxy, 'evidence request form');
        await shot(page, 'auditor-4-request', w);
        await proxy.click(form.getByRole('button', { name: 'Review request…' }), 'Review request…');
        await proxy.click(
          page.getByRole('dialog').getByRole('button', { name: 'Record evidence request' }),
          'Record evidence request',
        );
        await expect(
          page.getByRole('status').filter({ hasText: 'Evidence request recorded' }),
        ).toBeVisible();
      },
    );

    await proxy.record(testInfo);
    proxy.expectAllCompleted();
  });

  test('auditor using the keyboard only', async ({ page, isolatedApi }, testInfo) => {
    void isolatedApi;
    test.slow();
    const w = width(testInfo.project.name);
    const proxy = new ProxyUser(
      page,
      'auditor-keyboard',
      'An auditor who works with the keyboard only, requesting evidence on SYN-004.',
      'keyboard',
    );

    await proxy.run(
      'find-case',
      'Search for Synthetic Highland Imports and open its case',
      async () => {
        await page.goto(AUDITOR + '/auditor/');
        await pageReady(page, 'Overview');
        const queue = page
          .getByRole('button', { name: 'Open my case queue' })
          .or(page.getByRole('link', { name: 'Open my case queue' }));
        await proxy.tabTo(queue, 'Open my case queue');
        await proxy.press('Enter');
        await pageReady(page, 'My case queue');
        await proxy.tabTo(page.getByRole('searchbox', { name: 'Search' }), 'Search');
        await proxy.type('Highland Imports');
        await expect(page.getByText('Showing 1–1 of 1 cases')).toBeVisible();
        await proxy.tabTo(page.getByRole('link', { name: 'SYN-004' }), 'SYN-004');
        await proxy.press('Enter');
        await pageReady(page, /^SYN-004 · /);
      },
    );

    await proxy.run('request-evidence', 'Request the missing import records', async () => {
      await proxy.tabTo(
        page
          .getByRole('link', { name: 'Review and decide' })
          .or(page.getByRole('button', { name: 'Review and decide' })),
        'Review and decide',
      );
      await proxy.press('Enter');
      await pageReady(page, 'Review and decide · SYN-004');
      await proxy.tabTo(
        page
          .getByRole('region', { name: 'Suggestions to consider' })
          .getByRole('button', { name: 'Use as a starting point' })
          .first(),
        'Use as a starting point',
      );
      await proxy.press('Enter');
      const form = page.getByRole('form', { name: 'Request evidence' });
      const doc = form.getByRole('textbox', { name: /^Document 1 description/ });
      await proxy.tabTo(doc, 'Document 1 description');
      if (!(await doc.inputValue())) {
        await proxy.type('Synthetic: the reverse-charge record for the imported service.');
      }
      const message = form.getByRole('textbox', { name: /^Message to the taxpayer/ });
      await proxy.tabTo(message, 'Message to the taxpayer');
      if (!(await message.inputValue()))
        await proxy.type('Synthetic: please upload the record listed.');
      await proxy.tabTo(form.getByRole('button', { name: 'Review request…' }), 'Review request…');
      await proxy.press('Enter');
      const confirm = page
        .getByRole('dialog')
        .getByRole('button', { name: 'Record evidence request' });
      await proxy.tabTo(confirm, 'Record evidence request');
      await shot(page, 'auditor-keyboard-confirm', w);
      await proxy.press('Enter');
      await expect(
        page.getByRole('status').filter({ hasText: 'Evidence request recorded' }),
      ).toBeVisible();
    });

    await proxy.record(testInfo);
    proxy.expectAllCompleted();
  });

  test('new supervisor approving a decision and reading the dashboard', async ({
    page,
    isolatedApi,
  }, testInfo) => {
    void isolatedApi;
    test.slow();
    const w = width(testInfo.project.name);
    const proxy = new ProxyUser(
      page,
      'supervisor-first-day',
      'A supervisor on their first day: find decisions waiting for approval, approve one, then check workload.',
      'pointer',
    );

    await proxy.run(
      'find-approval',
      'Find a decision that is waiting for my approval',
      async () => {
        await page.goto(AUDITOR + '/auditor/');
        await pageReady(page, 'Overview');
        await actAs(proxy, 'Supervisor');
        await proxy.click(
          page.getByRole('link', { name: /Awaiting officer review/ }),
          'Overview: Awaiting officer review',
        );
        await pageReady(page, 'My case queue');
        const links = page.getByRole('table').getByRole('link', { name: /^SYN-\d+$/ });
        await expect(page.getByText(/Showing 1–\d+ of \d+ case/)).toBeVisible();
        const candidates = await links.allInnerTexts();
        if (
          (await page
            .getByRole('table')
            .getByText(/approv/i)
            .count()) === 0
        ) {
          proxy.friction(
            `Neither the overview nor the queue shows which of the ${candidates.length} "Awaiting officer review" cases has a decision waiting for approval`,
          );
        }
        let opened = 0;
        for (const id of candidates) {
          opened++;
          await proxy.click(page.getByRole('link', { name: id, exact: true }), id);
          await pageReady(page, new RegExp(`^${id} · `));
          await proxy.click(
            page
              .getByRole('link', { name: 'Review and decide' })
              .or(page.getByRole('button', { name: 'Review and decide' })),
            'Review and decide',
          );
          await pageReady(page, `Review and decide · ${id}`);
          const panel = page.getByRole('heading', {
            name: /^(Decision waiting for approval|Propose a decision)$/,
          });
          await expect(panel).toBeVisible();
          if ((await panel.textContent())?.includes('waiting')) break;
          // Back twice (review → case → filtered queue), as a person would.
          await proxy.back();
          await proxy.back();
          await pageReady(page, 'My case queue');
        }
        if (opened > 1) proxy.friction(`Opened ${opened} cases to find one waiting for approval`);
        await expect(
          page.getByRole('heading', { name: 'Decision waiting for approval' }),
        ).toBeVisible();
        await audit(proxy, 'approval');
        await shot(page, 'supervisor-1-waiting', w);
      },
    );

    await proxy.run('approve', 'Approve the decision', async () => {
      await proxy.click(page.getByRole('button', { name: 'Approve…' }), 'Approve…');
      await proxy.click(
        page.getByRole('dialog').getByRole('button', { name: 'Approve and apply in Jube' }),
        'Approve and apply in Jube',
      );
      await expect(page.getByRole('status').filter({ hasText: 'Decision approved' })).toBeVisible();
    });

    await proxy.run('read-workload', 'Find how many evidence requests are overdue', async () => {
      await proxy.click(
        await sidebarLink(proxy, 'Management dashboard'),
        'nav: Management dashboard',
      );
      await pageReady(page, 'Management dashboard');
      await expect(page.getByRole('main')).toContainText('Past their due date');
      await audit(proxy, 'management dashboard');
      await shot(page, 'supervisor-2-dashboard', w);
    });

    await proxy.record(testInfo);
    proxy.expectAllCompleted();
  });

  test('specialist finding a referred case', async ({ page, isolatedApi }, testInfo) => {
    void isolatedApi;
    test.slow();
    const w = width(testInfo.project.name);
    const proxy = new ProxyUser(
      page,
      'specialist-first-referral',
      'A specialist asked for an opinion, looking for the case and what they may do on it.',
      'pointer',
    );

    await proxy.run(
      'find-referral',
      'Find cases referred to me and understand what I can do',
      async () => {
        await page.goto(AUDITOR + '/auditor/');
        await pageReady(page, 'Overview');
        await actAs(proxy, 'Specialist');
        const nav = page.getByRole('navigation', { name: 'Auditor workspace' });
        if ((await nav.getByRole('link', { name: /referr|specialist|opinion/i }).count()) === 0) {
          proxy.friction(
            'There is no "Referred to me" view; a specialist sees the same queue as an auditor',
          );
        }
        await proxy.click(
          page.getByRole('link', { name: /Awaiting officer review/ }),
          'Overview: Awaiting officer review',
        );
        await pageReady(page, 'My case queue');
        await proxy.click(page.getByRole('link', { name: 'SYN-005', exact: true }), 'SYN-005');
        await pageReady(page, /^SYN-005 · /);
        await proxy.click(
          page
            .getByRole('link', { name: 'Review and decide' })
            .or(page.getByRole('button', { name: 'Review and decide' })),
          'Review and decide',
        );
        await pageReady(page, 'Review and decide · SYN-005');
        await expect(page.getByRole('button', { name: 'Approve…' })).toBeDisabled();
        await expect(page.getByRole('button', { name: 'Approve…' })).toHaveAccessibleDescription(
          /supervisor/,
        );
        await audit(proxy, 'specialist review');
        await shot(page, 'specialist-1-review', w);
      },
    );

    await proxy.record(testInfo);
    proxy.expectAllCompleted();
  });
});
