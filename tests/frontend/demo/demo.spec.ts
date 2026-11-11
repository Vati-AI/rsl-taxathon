/**
 * Scripted product walkthrough for the RSL Taxathon demo video and evidence pack.
 * Synthetic fixture data only; the taxpayer session uses the same in-browser
 * mocks as the frontend tests (support/sessions.ts), no Frappe site is needed.
 */
import fs from 'node:fs';
import { expect, test as base, type Page } from '@playwright/test';
import { chooseReason, fillInvoice, pdf } from '../support/claim-flow';
import { mockTaxpayerSession } from '../support/sessions';

const AUDITOR = process.env.DEMO_AUDITOR ?? 'http://127.0.0.1:58080';
const TAXPAYER = process.env.DEMO_TAXPAYER ?? 'http://127.0.0.1:58082';
const SHOTS = 'docs/demo/screenshots';
const FRAMES = 'docs/demo/frames';
/** Voiceover lengths from scripts/make_demo_narration.py, so each caption stays up for its line. */
const NARRATION: Record<string, { seconds: number }> = fs.existsSync(
  'docs/demo/narration/durations.json',
)
  ? JSON.parse(fs.readFileSync('docs/demo/narration/durations.json', 'utf8'))
  : {};
/** Wall-clock time each caption appeared, written per scene for the audio mix. */
let beats: { title: string; ts: number }[] = [];

/**
 * Records each scene as timestamped JPEG frames over the DevTools screencast, so
 * the video does not depend on Playwright's bundled ffmpeg. make_demo_video.sh
 * encodes the frames with the system ffmpeg.
 */
const test = base.extend<{ recorder: void }>({
  recorder: [
    async ({ page }, use, testInfo) => {
      const dir = `${FRAMES}/${testInfo.title.slice(0, 2)}`;
      fs.rmSync(dir, { recursive: true, force: true });
      fs.mkdirSync(dir, { recursive: true });
      const cdp = await page.context().newCDPSession(page);
      const index: string[] = [];
      beats = [];
      cdp.on('Page.screencastFrame', async (f) => {
        const file = `${String(index.length).padStart(6, '0')}.jpg`;
        fs.writeFileSync(`${dir}/${file}`, Buffer.from(f.data, 'base64'));
        index.push(`${file} ${f.metadata.timestamp}`);
        await cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {});
      });
      await cdp.send('Page.startScreencast', {
        format: 'jpeg',
        quality: 90,
        maxWidth: 1440,
        maxHeight: 900,
      });
      await use();
      await page.waitForTimeout(500);
      await cdp.send('Page.stopScreencast').catch(() => {});
      fs.writeFileSync(`${dir}/index.txt`, index.join('\n') + '\n');
      fs.writeFileSync(`${dir}/beats.json`, JSON.stringify(beats, null, 2));
    },
    { auto: true },
  ],
});
let n = 0;

/** A lower-third caption drawn over the page so the recording explains itself. */
async function caption(page: Page, title: string, body = '') {
  await page.evaluate(
    ([t, b]) => {
      let el = document.getElementById('__demo_caption');
      if (!el) {
        el = document.createElement('div');
        el.id = '__demo_caption';
        el.setAttribute('aria-hidden', 'true');
        el.style.cssText =
          'position:fixed;left:24px;bottom:24px;max-width:760px;z-index:2147483647;' +
          'background:rgba(15,23,42,.92);color:#fff;padding:14px 20px;border-radius:12px;' +
          'font:500 18px/1.4 system-ui,sans-serif;box-shadow:0 10px 30px rgba(0,0,0,.35);' +
          'border-left:6px solid #22c55e;pointer-events:none';
        document.body.appendChild(el);
      }
      el.innerHTML =
        '<div style="font-weight:700;font-size:20px">' +
        t +
        '</div>' +
        (b ? '<div style="opacity:.85;font-size:16px;margin-top:4px">' + b + '</div>' : '');
    },
    [title, body],
  );
}

async function beat(page: Page, title: string, body = '', hold = 3500, shot = true) {
  await page.waitForLoadState('networkidle', { timeout: 4000 }).catch(() => {});
  await caption(page, title, body);
  beats.push({ title, ts: Date.now() / 1000 });
  const voice = NARRATION[title]?.seconds;
  if (voice) hold = Math.max(hold, voice * 1000 + 700);
  await page.waitForTimeout(400);
  if (shot) {
    n += 1;
    const name = `${String(n).padStart(2, '0')}-${title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/-+$/, '')}.png`;
    await page.screenshot({ path: `${SHOTS}/${name}` });
  }
  await page.waitForTimeout(hold);
}

async function soft(label: string, fn: () => Promise<void>) {
  try {
    await fn();
  } catch (e) {
    console.warn(`[demo] skipped "${label}": ${(e as Error).message.split('\n')[0]}`);
  }
}

test('01 taxpayer: VAT refund claim', async ({ page }) => {
  await mockTaxpayerSession(page, 'taxpayer', { dataMode: 'fixture' });
  // TAX-125: the portal opens on evidence requests; the demo starts at the taxpayer's cases.
  await page.goto(TAXPAYER + '/taxpayer/submissions');
  await expect(page.getByRole('heading', { name: 'My cases' })).toBeVisible();
  await beat(
    page,
    'Taxpayer portal',
    'A business sees its own submissions and their review status, nothing else.',
  );

  await soft('open submission', async () => {
    await page.getByRole('list', { name: 'My cases' }).getByRole('link').first().click();
    await beat(
      page,
      'Submission status',
      'Plain-language status mirrored from case management. No risk scores or officer detail reach the browser.',
    );
    await page.goBack();
  });

  await page.goto(TAXPAYER + '/taxpayer/submissions/new');
  await beat(
    page,
    'Online Refunds simulator',
    'A guided three-step claim: details, supporting documents and invoices, then review.',
    2500,
  );
  await page.getByLabel('Tax period').pressSequentially('2026-09', { delay: 60 });
  await page.getByLabel('Amount claimed (LSL)').pressSequentially('48,210.55', { delay: 60 });
  await page.getByLabel('Output VAT for the period (LSL)').fill('0');
  await page
    .getByLabel('Input VAT for the period (LSL)')
    .pressSequentially('48210.55', { delay: 40 });
  await chooseReason(page, 'More input VAT than output VAT');
  await page.getByLabel('Contact email (optional)').fill('accounts@example.test');
  await beat(
    page,
    'Claim details',
    'Amounts are validated in the browser before anything is sent.',
  );
  await page.getByRole('button', { name: 'Continue to documents' }).click();

  await page.getByLabel('Choose a file to upload').setInputFiles(pdf());
  await page.waitForTimeout(1200);
  await beat(
    page,
    'Supporting documents',
    'Files are type- and size-checked, then stored as private, hashed evidence.',
  );
  await page.getByRole('button', { name: 'Continue to invoices' }).click();
  await fillInvoice(page);
  await beat(
    page,
    'Purchase invoices',
    'Invoice lines behind the claim feed automated VAT consistency checks.',
  );
  await page.getByRole('button', { name: 'Continue to review' }).click();
  await beat(
    page,
    'Check your claim',
    'The taxpayer reviews everything before signing the declaration.',
  );
  await page
    .getByLabel('I declare that the information I have given is true and complete.')
    .check();
  await page.getByRole('button', { name: 'Send claim' }).click();
  await expect(page.getByRole('heading', { name: 'Claim sent' })).toBeVisible();
  await beat(
    page,
    'Claim sent',
    'One idempotent submission: safe to retry, never duplicated.',
    4000,
  );
});

test('02 auditor: risk-selected case to human decision', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('vati.fixtureRole', 'auditor'));
  await page.goto(AUDITOR + '/auditor/');
  await beat(
    page,
    'Auditor workspace',
    'Officers see assigned cases by status, straight from Investigation, the case management app.',
  );

  await page.goto(AUDITOR + '/auditor/cases');
  await beat(
    page,
    'Risk-selected case queue',
    'Every case shows the signals that selected it, plus search, status and refund-lane filters.',
  );

  const caseUrl = AUDITOR + '/auditor/cases/40000000-0000-4000-8000-000000000003';
  await page.goto(caseUrl);
  await beat(
    page,
    'Case detail',
    'Selection reasons, taxpayer summary and per-service processing in one place.',
  );
  for (const [tab, title, body] of [
    ['Evidence', 'Evidence', 'Documents with extraction state and SHA-256 integrity hashes.'],
    [
      'Relationships',
      'Entity relationships',
      'Linked companies, directors and accounts surfaced from the graph.',
    ],
    [
      'Findings',
      'AI-assisted findings',
      'Each finding cites the evidence page or record it relies on.',
    ],
  ] as const) {
    await soft(tab, async () => {
      await page
        .getByRole('navigation', { name: 'Case sections' })
        .getByRole('link', { name: new RegExp(`^${tab}`) })
        .click();
      await beat(page, title, body);
    });
  }

  await soft('evidence record', async () => {
    await page
      .getByRole('navigation', { name: 'Case sections' })
      .getByRole('link', { name: /^Evidence/ })
      .click();
    await page.getByRole('list', { name: 'Evidence on file' }).getByRole('link').first().click();
    await beat(
      page,
      'Evidence record',
      'Version history, hash, cited page and every finding that relies on it.',
    );
  });

  await page.goto(caseUrl + '/review');
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Review and decide');
  await beat(
    page,
    'Review and decide',
    'Agent suggestions only pre-fill the form. A human officer always decides.',
  );
  await soft('propose', async () => {
    await page.getByRole('button', { name: 'Use as a starting point' }).first().click();
    await page.waitForTimeout(800);
    const rationale = page.getByLabel('Rationale in your own words');
    await rationale.fill('');
    await rationale.pressSequentially(
      'Payslips and contractor invoices overlap for the same five people. Refer to a specialist to check worker classification.',
      { delay: 15 },
    );
    await beat(
      page,
      'Officer rationale',
      'The officer explains the decision in their own words.',
      2000,
    );
    await page.getByRole('button', { name: 'Review and propose…' }).click();
    const dialog = page.getByRole('dialog', { name: 'Confirm the proposed decision' });
    await beat(
      page,
      'Confirm before acting',
      'Every consequential action is confirmed in a dialog first.',
      2500,
    );
    await dialog.getByRole('button', { name: 'Propose decision' }).click();
    await page.waitForTimeout(1200);
    await beat(
      page,
      'Decision proposed',
      'It now waits for supervisor approval: separation of duties built in.',
    );
  });
});

test('03 supervisor: oversight and integrity', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('vati.fixtureRole', 'supervisor'));
  await page.goto(AUDITOR + '/auditor/management');
  await beat(
    page,
    'Management dashboard',
    'Workload, elapsed time, unresolved evidence and legal-route coverage.',
  );
  await page.mouse.wheel(0, 700);
  await beat(
    page,
    'Audit assurance',
    'Figures measured from case, decision, evidence and audit-ledger records.',
  );
  await page.goto(AUDITOR + '/auditor/management/integrity');
  await beat(
    page,
    'Integrity review',
    'Policy exposure, overrides and review signals, aggregated, with small groups combined.',
    4500,
  );
});

/** Title cards for the video (rendered here so the pipeline needs only Playwright and ffmpeg). */
base('00 title cards', async ({ page }) => {
  fs.mkdirSync('docs/demo/cards', { recursive: true });
  const cards: Record<string, [string, string, string]> = {
    intro: [
      'RSL Taxathon · Challenge 2',
      'Vati AI Tax Intelligence',
      'From taxpayer claim to risk-selected case to a human, evidence-backed decision.',
    ],
    s01: [
      'Scene 1',
      'The taxpayer files a VAT refund claim',
      'Guided, validated and idempotent: built on Frappe, mirrored to case management.',
    ],
    s02: [
      'Scene 2',
      'The officer investigates and proposes',
      'Risk-selected cases, cited evidence, entity links and AI findings. Humans decide.',
    ],
    s03: [
      'Scene 3',
      'The supervisor oversees',
      'Separation of duties, workload, audit-ledger assurance and integrity signals.',
    ],
    outro: [
      'Vati AI × Revenue Services Lesotho',
      'Explainable. Evidence-first. Human-decided.',
      'Open source stack: Jube · Frappe · OpenAleph · yente · Docling. Synthetic data shown throughout.',
    ],
  };
  await page.setViewportSize({ width: 1440, height: 900 });
  for (const [name, [kicker, title, sub]] of Object.entries(cards)) {
    await page.setContent(`<html><body style="margin:0;width:1440px;height:900px;display:flex;flex-direction:column;justify-content:center;padding:0 140px;box-sizing:border-box;
      background:radial-gradient(circle at 20% 20%,#1e3a8a 0,#0f172a 55%,#020617 100%);color:#fff;font-family:system-ui,sans-serif">
      <div style="color:#4ade80;font-weight:700;letter-spacing:.12em;text-transform:uppercase;font-size:22px">${kicker}</div>
      <div style="font-size:68px;font-weight:800;line-height:1.1;margin-top:18px">${title}</div>
      <div style="font-size:28px;opacity:.85;margin-top:26px;max-width:1050px;line-height:1.4">${sub}</div>
      <div style="position:absolute;bottom:48px;left:140px;font-size:18px;opacity:.6">Fixture mode · synthetic data only · no real taxpayer records</div>
    </body></html>`);
    await page.screenshot({ path: `docs/demo/cards/${name}.png` });
  }
});
