import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { pageReady } from './support/checks';
import { AUDITOR, TAXPAYER } from './support/env';
import {
  BUDGET_VIEWPORTS,
  findComponentNames,
  measurePageLength,
  type NameHit,
  type PageLength,
} from './support/layout';
import { AUDITOR_ROUTES, auditorCase, TAXPAYER_ROUTES, type MatrixRoute } from './support/matrix';
import { mockTaxpayerSession } from './support/sessions';

// TAX-144: page-length budget and product-naming checks (docs/DESIGN_STANDARD.md v2,
// ADR-044), measured at 1440×900 where the budget is defined.
//
// Both checks are ratchets against support/layout-baseline.json, which lists the
// screens that broke a rule when TAX-144 landed and the task that fixes each one:
//   - a screen that is not listed fails when it goes over budget or shows a
//     component name;
//   - a listed screen fails when it gets taller (beyond a small rendering
//     tolerance) or shows more component names than recorded.
// Fixing tasks shrink the baseline. TAX-150 emptied the naming list: any component name
// shown outside a data-component-names-allowed region now fails.
//
//   LAYOUT_REPORT=1        also write docs/evidence/TAX-144/inventory-*.{json,md}
//   CAPTURE_SCREENSHOTS=1  also write full-length screenshots to docs/evidence/TAX-144/before

interface Baseline {
  over_budget: Record<string, { heightPx: number; task: string }>;
  component_names: Record<string, { count: number; task: string }>;
  exempt: Record<string, string>;
}
const baseline = JSON.parse(
  readFileSync(path.resolve(__dirname, 'support/layout-baseline.json'), 'utf8'),
) as Baseline;
/** Font rendering differs a little between machines; growth below this is not a regression. */
const TOLERANCE_PX = 48;

const evidenceDir = path.resolve(__dirname, '../../docs/evidence/TAX-144');
const writeReport = Boolean(process.env.LAYOUT_REPORT);
const capture = Boolean(process.env.CAPTURE_SCREENSHOTS);

test.skip(
  ({ viewport }) => viewport?.width !== 1440,
  'The page-length budget is defined at 1440×900',
);

interface Row {
  screen: string;
  path: string;
  role: string;
  length: PageLength;
  overBudget: boolean;
  names: NameHit[];
  verdict: string[];
}

/** Expand the page-level scroll container so a full-page screenshot shows everything. */
async function unrollForScreenshot(page: Page) {
  await page.evaluate(() => {
    for (const el of Array.from(document.querySelectorAll<HTMLElement>('body *'))) {
      const style = getComputedStyle(el);
      if (/(auto|scroll)/.test(style.overflowY) && el.clientHeight >= window.innerHeight * 0.5) {
        for (let a: HTMLElement | null = el; a; a = a.parentElement) {
          a.style.height = 'auto';
          a.style.maxHeight = 'none';
          a.style.overflow = 'visible';
        }
      }
    }
  });
}

async function measure(page: Page, screen: string, route: string, role: string): Promise<Row> {
  const length = await measurePageLength(page);
  const names = await findComponentNames(page);
  const exempt = screen in baseline.exempt;
  const overBudget = !exempt && length.viewports > BUDGET_VIEWPORTS;
  const verdict: string[] = [];

  const knownLength = baseline.over_budget[screen];
  if (exempt) verdict.push(`exempt: ${baseline.exempt[screen]}`);
  else if (knownLength) {
    if (length.heightPx > knownLength.heightPx + TOLERANCE_PX)
      verdict.push(`FAIL grew from ${knownLength.heightPx}px to ${length.heightPx}px`);
    else if (!overBudget) verdict.push('now within budget: remove from the baseline');
    else verdict.push(`known over budget, fixed by ${knownLength.task}`);
  } else if (overBudget) {
    verdict.push(`FAIL ${length.viewports} viewports exceeds the ${BUDGET_VIEWPORTS} budget`);
  }

  const knownNames = baseline.component_names[screen];
  if (names.length > (knownNames?.count ?? 0))
    verdict.push(
      `FAIL ${names.length} component names shown (baseline ${knownNames?.count ?? 0}): ` +
        [...new Set(names.map((n) => n.name))].join(', '),
    );
  else if (knownNames && names.length < knownNames.count)
    verdict.push(`names down to ${names.length}: lower the baseline`);
  else if (knownNames) verdict.push(`known component names, fixed by ${knownNames.task}`);

  if (capture) {
    mkdirSync(path.join(evidenceDir, 'before'), { recursive: true });
    await unrollForScreenshot(page);
    await page.screenshot({
      path: path.join(evidenceDir, 'before', `${screen.replace('/', '-')}-1440.png`),
      fullPage: true,
    });
  }
  return { screen, path: route, role, length, overBudget, names, verdict };
}

function report(portal: string, rows: Row[]) {
  const failures = rows.flatMap((r) =>
    r.verdict.filter((v) => v.startsWith('FAIL')).map((v) => `${r.screen}: ${v}`),
  );
  const offenders = [...rows]
    .filter((r) => r.overBudget)
    .sort((a, b) => b.length.heightPx - a.length.heightPx);
  if (writeReport) {
    mkdirSync(evidenceDir, { recursive: true });
    writeFileSync(
      path.join(evidenceDir, `inventory-${portal}.json`),
      JSON.stringify(
        { budget_viewports: BUDGET_VIEWPORTS, viewport: '1440x900', screens: rows },
        null,
        2,
      ) + '\n',
    );
    const md = [
      `# ${portal} screen inventory at 1440×900 (TAX-144)`,
      '',
      `Budget: ${BUDGET_VIEWPORTS} viewport heights (${BUDGET_VIEWPORTS * 900}px). Generated by \`LAYOUT_REPORT=1 npm run test:frontend:browser -- --project=desktop-1440 layout\`.`,
      '',
      '| Screen | Signed in as | Height | Viewports | Component names shown | Result |',
      '|---|---|---|---|---|---|',
      ...rows.map(
        (r) =>
          `| ${r.screen} | ${r.role} | ${r.length.heightPx}px | ${r.length.viewports} | ${
            r.names.length
              ? `${r.names.length}: ${[...new Set(r.names.map((n) => n.name))].join(', ')}`
              : '—'
          } | ${r.verdict.join('; ') || 'within budget'} |`,
      ),
      '',
      offenders.length
        ? `Worst offenders: ${offenders.map((r) => `${r.screen} (${r.length.viewports})`).join(', ')}.`
        : 'No screen is over budget.',
      '',
      '## Component names seen',
      '',
      ...rows.flatMap((r) =>
        r.names.map((n) => `- ${r.screen} (${n.where}): **${n.name}** — “${n.context}”`),
      ),
      '',
    ].join('\n');
    writeFileSync(path.join(evidenceDir, `inventory-${portal}.md`), md);
  }
  test.info().annotations.push({
    type: 'layout',
    description: offenders.length
      ? `Over budget: ${offenders.map((r) => `${r.screen} ${r.length.viewports}`).join(', ')}`
      : 'No screen over budget',
  });
  expect(failures, failures.join('\n')).toEqual([]);
}

async function visit(page: Page, base: string, route: MatrixRoute) {
  await page.goto(base + route.path);
  await pageReady(page, route.heading);
}

const syn002 = auditorCase('SYN-002');
const CASE_TABS: MatrixRoute[] = ['evidence', 'relationships', 'findings'].map((tab) => ({
  name: `case-${tab}`,
  path: `/auditor/cases/${syn002.case_id}?tab=${tab}`,
  heading: 'SYN-002 · Synthetic Border Retail',
}));

test('auditor screens: page length and product names', async ({ page }) => {
  const rows: Row[] = [];
  for (const route of [...AUDITOR_ROUTES, ...CASE_TABS]) {
    await visit(page, AUDITOR, route);
    rows.push(await measure(page, `auditor/${route.name}`, route.path, 'auditor'));
  }

  // One evidence record, reached from the case's evidence tab.
  await visit(page, AUDITOR, CASE_TABS[0]);
  const record = page.locator('a[href*="/evidence/"]').first();
  const href = await record.getAttribute('href');
  if (href) {
    await page.goto(AUDITOR + href);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await page.waitForLoadState('networkidle');
    rows.push(await measure(page, 'auditor/evidence-record', href, 'auditor'));
  }

  // Supervisor-only screens, through the fixture role switch (apps/auditor-web/src/session.ts).
  await page.addInitScript(() => localStorage.setItem('vati.fixtureRole', 'supervisor'));
  for (const route of AUDITOR_ROUTES.filter((r) =>
    ['management', 'integrity', 'case-review'].includes(r.name),
  )) {
    await visit(page, AUDITOR, route);
    rows.push(await measure(page, `auditor/${route.name}-supervisor`, route.path, 'supervisor'));
  }
  report('auditor', rows);
});

test('taxpayer screens: page length and product names', async ({ page }) => {
  await mockTaxpayerSession(page, 'taxpayer');
  const rows: Row[] = [];
  for (const route of TAXPAYER_ROUTES) {
    await visit(page, TAXPAYER, route);
    rows.push(await measure(page, `taxpayer/${route.name}`, route.path, 'taxpayer'));
  }
  report('taxpayer', rows);
});
