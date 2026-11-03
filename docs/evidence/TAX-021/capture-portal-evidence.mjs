// TAX-021 evidence run against the local Frappe developer site (synthetic data only).
// Usage (repo root): PORTAL_USER=... PORTAL_PASS=... node docs/evidence/TAX-021/capture-portal-evidence.mjs docs/evidence/TAX-021
// Needs the local Frappe site (docs/FRAPPE_DEV.md), npm run build:taxpayer and Microsoft Edge (Windows path below).
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const axeSource = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
const BASE = 'http://127.0.0.1:58000';
const OUT = process.argv[2];
const USER = process.env.PORTAL_USER;
const PASS = process.env.PORTAL_PASS;
fs.mkdirSync(OUT, { recursive: true });

const results = { checks: [], axe: [], keyboard: {}, overflow: [], screenshots: [] };
const check = (name, pass, detail = '') => {
  results.checks.push({ name, pass, detail });
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${detail ? ' — ' + detail : ''}`);
};

const browser = await chromium.launch({
  executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
});
const version = browser.version();

// ---------- 1. Signed-out visitor ----------
{
  const ctx = await browser.newContext();
  const req = ctx.request;
  for (const p of ['/taxpayer', '/taxpayer/submissions/SUB-SYN-0001']) {
    const r = await req.get(BASE + p, { maxRedirects: 0 });
    const loc = r.headers()['location'] || '';
    check(`guest GET ${p} redirected to login`, [301, 302, 303, 307].includes(r.status()) && loc.includes('/login?redirect-to='), `${r.status()} -> ${loc}`);
  }
  const hostile = await req.get(BASE + '/taxpayer/..%2F..%2Fdesk', { maxRedirects: 0 });
  check('guest hostile path stays inside portal', !(hostile.headers()['location'] || '').includes('desk') , `${hostile.status()} -> ${hostile.headers()['location'] || ''}`);
  for (const m of ['get_session', 'get_submissions', 'get_submission']) {
    const r = await req.post(`${BASE}/api/method/vati_tax.portal.api.${m}`, { data: { submission_id: 'SUB-SYN-0001' } });
    const body = await r.text();
    check(`guest API ${m} rejected`, r.status() === 403 && !body.includes('SUB-SYN'), `HTTP ${r.status()}`);
  }
  // Browser view: guest lands on Frappe login with the return path.
  const page = await ctx.newPage();
  await page.goto(BASE + '/taxpayer/submissions/SUB-SYN-0002');
  check('guest browser shown login page', new URL(page.url()).pathname === '/login', page.url());
  await page.screenshot({ path: path.join(OUT, 'guest-login-redirect-1440.png') });
  await ctx.close();
}

// ---------- helpers ----------
async function login(page, target) {
  await page.goto(`${BASE}/login?redirect-to=${encodeURIComponent(target)}`);
  await page.fill('#login_email', USER);
  await page.fill('#login_password', PASS);
  await Promise.all([page.waitForURL((u) => u.pathname.startsWith('/taxpayer'), { timeout: 30000 }), page.getByRole('button', { name: 'Continue', exact: true }).click()]);
  await page.waitForLoadState('networkidle');
}

async function axe(page, label) {
  await page.addScriptTag({ content: axeSource });
  const r = await page.evaluate(async () => {
    // eslint-disable-next-line no-undef
    const res = await axe.run(document, { runOnly: ['wcag2a', 'wcag2aa', 'wcag21aa', 'best-practice'] });
    return res.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length, targets: v.nodes.slice(0, 3).map((n) => n.target.join(' ')) }));
  });
  results.axe.push({ label, violations: r });
  return r;
}

async function overflow(page, label) {
  const o = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
  results.overflow.push({ label, ...o, ok: o.sw <= o.cw });
  return o.sw <= o.cw;
}

async function shot(page, name) {
  const file = `${name}.png`;
  // Desktop content scrolls inside the shell; grow the window to show all of it.
  const vp = page.viewportSize();
  // Both shells scroll an inner area (desktop: scroll-area viewport, mobile: shell scroll).
  const inner = await page.evaluate(() => {
    const el = document.querySelector(
      '[data-slot="desktop-shell-content"] [data-slot="scroll-area-viewport"], [data-slot="mobile-shell-scroll"]',
    );
    return el ? innerHeight + Math.max(0, el.scrollHeight - el.clientHeight) : 0;
  });
  if (inner > vp.height) await page.setViewportSize({ width: vp.width, height: Math.ceil(inner) });
  await page.waitForTimeout(150);
  await page.screenshot({ path: path.join(OUT, file), fullPage: true });
  if (inner > vp.height) await page.setViewportSize(vp);
  results.screenshots.push(file);
}

const VIEWPORTS = { 1440: { width: 1440, height: 900 }, 1024: { width: 1024, height: 768 }, 390: { width: 390, height: 844 } };

// ---------- 2. Authenticated flows per viewport ----------
let storage;
{
  const ctx = await browser.newContext({ viewport: VIEWPORTS[1440] });
  const page = await ctx.newPage();
  await login(page, '/taxpayer/submissions/SUB-SYN-0002');
  check('login returns to requested deep link', new URL(page.url()).pathname === '/taxpayer/submissions/SUB-SYN-0002', page.url());
  storage = await ctx.storageState();
  // Internal fields never reach the taxpayer.
  const api = await page.evaluate(async () => {
    const r = await fetch('/api/method/vati_tax.portal.api.get_submissions', { headers: { Accept: 'application/json' } });
    return r.json();
  });
  const keys = new Set(api.message.data.flatMap((d) => Object.keys(d)));
  const forbidden = ['risk', 'risk_score', 'case_id', 'scenario', 'expected', 'supported_refund'];
  check('API marks data as synthetic fixture', api.message.mode === 'fixture' && api.message.synthetic === true);
  check('API exposes no risk/case fields', !forbidden.some((k) => keys.has(k)), [...keys].join(','));
  const text = await page.locator('body').innerText();
  check('UI shows no risk/case wording', !/risk|case id|fraud|SYN-00\d/i.test(text));
  await ctx.close();
}

for (const [w, vp] of Object.entries(VIEWPORTS)) {
  const ctx = await browser.newContext({ viewport: vp, storageState: storage });
  const page = await ctx.newPage();

  // List
  await page.goto(BASE + '/taxpayer/submissions');
  await page.getByText('Synthetic Mountain', { exact: false }).first().waitFor({ state: 'attached', timeout: 5000 }).catch(() => {});
  await page.getByRole('heading', { name: 'Your submissions' }).waitFor();
  check(`[${w}] submissions list shows 4 fixture rows`, (await page.locator('ul[aria-label="Submissions"] > li').count()) === 4);
  check(`[${w}] list has no page-wide horizontal scroll`, await overflow(page, `list-${w}`));
  await shot(page, `list-${w}`);
  await axe(page, `list-${w}`);

  // Navigation to detail by clicking
  await page.locator('ul[aria-label="Submissions"] a').filter({ hasText: 'VAT return · August 2026' }).click();
  await page.getByRole('heading', { name: 'Progress' }).waitFor();
  check(`[${w}] clicking a row opens its status page`, page.url().endsWith('/taxpayer/submissions/SUB-SYN-0002'));
  const detailText = await page.locator('body').innerText();
  check(`[${w}] status page shows long name, amount and next step`, detailText.includes('Synthetic Highland Imports and Logistics Cooperative Society') && detailText.includes('LSL 48,210.55') && detailText.includes('Evidence requested'));
  check(`[${w}] status page no horizontal scroll`, await overflow(page, `detail-${w}`));
  await shot(page, `detail-${w}`);
  await axe(page, `detail-${w}`);
  const activeNav = await page.evaluate(() =>
    [...document.querySelectorAll('[data-state="active"], [aria-current="page"]')].map((e) => e.textContent.trim()),
  );
  check(`[${w}] status page keeps Submissions nav active`, activeNav.some((t) => t.startsWith('Submissions')), activeNav.join(' | '));

  // Missing amount shown as "Not provided"
  await page.goto(BASE + '/taxpayer/submissions/SUB-SYN-0003');
  await page.getByRole('heading', { name: 'Progress' }).waitFor();
  check(`[${w}] missing amount shown as Not provided`, (await page.locator('body').innerText()).includes('Not provided'));

  // Unknown submission
  await page.goto(BASE + '/taxpayer/submissions/SUB-SYN-9999');
  await page.getByRole('heading', { name: 'Submission not found' }).waitFor();
  check(`[${w}] unknown submission shows not-found state`, true);
  await shot(page, `detail-notfound-${w}`);

  // Form: empty -> errors -> fixed -> review
  await page.goto(BASE + '/taxpayer/submissions/new');
  await page.getByRole('heading', { name: 'Your details' }).waitFor();
  await shot(page, `form-empty-${w}`);
  await axe(page, `form-empty-${w}`);
  await page.getByLabel('Tax period').fill('2026-99');
  await page.getByLabel('Amount (LSL)').fill('12.345');
  await page.getByRole('button', { name: 'Continue to review' }).click();
  await page.getByRole('alert').first().waitFor();
  const focused = await page.evaluate(() => document.activeElement?.getAttribute('aria-invalid'));
  check(`[${w}] validation focuses first invalid field`, focused === 'true');
  check(`[${w}] validation keeps entered values`, (await page.getByLabel('Tax period').inputValue()) === '2026-99' && (await page.getByLabel('Amount (LSL)').inputValue()) === '12.345');
  const formText = await page.locator('body').innerText();
  check(`[${w}] validation messages are actionable`, formText.includes('Choose the type of return or claim.') && formText.includes('for example 2026-09') && formText.includes('up to two decimal places') && formText.includes('Confirm the declaration'));
  check(`[${w}] form with errors no horizontal scroll`, await overflow(page, `form-errors-${w}`));
  await shot(page, `form-errors-${w}`);
  await axe(page, `form-errors-${w}`);

  await page.getByLabel('Type of return or claim').selectOption('VAT return').catch(async () => {
    await page.getByLabel('Type of return or claim').click();
    await page.getByRole('option', { name: 'VAT return', exact: true }).click();
  });
  await page.getByLabel('Tax period').fill('2026-09');
  await page.getByLabel('Amount (LSL)').fill('15,000.5');
  await page.getByLabel(/I declare/).check();
  await page.getByRole('button', { name: 'Continue to review' }).click();
  await page.getByRole('heading', { name: 'Check your answers' }).waitFor();
  const review = await page.locator('body').innerText();
  check(`[${w}] review step shows normalised answers`, review.includes('LSL 15,000.50') && review.includes('September 2026'));
  const send = page.getByRole('button', { name: 'Send submission' });
  check(`[${w}] send is disabled with a visible reason`, (await send.isDisabled()) && review.includes('Sending is not available yet'));
  await shot(page, `form-review-${w}`);
  await axe(page, `form-review-${w}`);
  await page.getByRole('button', { name: 'Change answers' }).click();
  check(`[${w}] change answers keeps values`, (await page.getByLabel('Amount (LSL)').inputValue()) === '15,000.5');

  // Account
  await page.goto(BASE + '/taxpayer/account');
  await page.getByRole('heading', { name: 'Account' }).waitFor();
  check(`[${w}] account shows signed-in user`, (await page.locator('body').innerText()).includes(USER));
  await shot(page, `account-${w}`);
  await axe(page, `account-${w}`);

  // Not found route
  await page.goto(BASE + '/taxpayer/does/not/exist');
  await page.getByRole('heading', { name: 'Page not found' }).waitFor();
  check(`[${w}] unknown portal route shows not-found page`, true);
  await shot(page, `notfound-${w}`);

  // States via network interception (server answers faked in the browser only).
  const listApi = '**/api/method/vati_tax.portal.api.get_submissions';
  await page.route(listApi, async (route) => { await new Promise((r) => setTimeout(r, 2500)); await route.continue().catch(() => {}); });
  await page.goto(BASE + '/taxpayer/submissions');
  await page.getByText('Loading submissions…').waitFor();
  await shot(page, `state-loading-${w}`);
  await page.unroute(listApi);

  await page.route(listApi, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ message: { mode: 'fixture', synthetic: true, data: [] } }) }));
  await page.goto(BASE + '/taxpayer/submissions');
  await page.getByRole('heading', { name: 'No submissions yet' }).waitFor();
  check(`[${w}] empty state shown`, true);
  await shot(page, `state-empty-${w}`);
  await page.unroute(listApi);

  await page.route(listApi, (route) => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ exc_type: 'Exception' }) }));
  await page.goto(BASE + '/taxpayer/submissions');
  await page.getByText('Submissions could not be loaded').waitFor();
  check(`[${w}] error state offers retry`, await page.getByRole('button', { name: 'Try again' }).isVisible());
  await shot(page, `state-error-${w}`);
  await page.unroute(listApi);

  await page.route(listApi, (route) => route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ exc_type: 'PermissionError' }) }));
  await page.goto(BASE + '/taxpayer/submissions');
  await page.getByText('Access denied').waitFor();
  check(`[${w}] denied state shown without data`, !(await page.locator('body').innerText()).includes('VAT-2026'));
  await shot(page, `state-denied-${w}`);
  await page.unroute(listApi);

  // Reflow check at 320 CSS px (≈ 1280px window at 400% zoom), mobile only.
  if (w === '390') {
    await page.setViewportSize({ width: 320, height: 720 });
    for (const p of ['/taxpayer/submissions', '/taxpayer/submissions/SUB-SYN-0002', '/taxpayer/submissions/new']) {
      await page.goto(BASE + p);
      await page.waitForLoadState('networkidle');
      check(`[320 reflow] ${p} no horizontal scroll`, await overflow(page, `reflow-320${p}`));
    }
  }
  await ctx.close();
}

// ---------- 3. Keyboard ----------
{
  const ctx = await browser.newContext({ viewport: VIEWPORTS[1440], storageState: storage });
  const page = await ctx.newPage();
  await page.goto(BASE + '/taxpayer/submissions');
  await page.getByRole('heading', { name: 'Your submissions' }).waitFor();
  const order = [];
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('Tab');
    order.push(await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const cs = getComputedStyle(el);
      const visible = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0) || cs.boxShadow !== 'none';
      return { tag: el.tagName.toLowerCase(), name: (el.getAttribute('aria-label') || el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 60), focusVisible: visible };
    }));
    if (order[order.length - 1] === null) { order.pop(); break; }
    if (i === 0) await shot(page, 'keyboard-skip-link-1440');
  }
  results.keyboard.listTabOrder = order;
  check('first Tab reaches skip link', order[0].name === 'Skip to main content');
  check('every tab stop shows a visible focus indicator', order.every((o) => o.focusVisible), order.filter((o) => !o.focusVisible).map((o) => o.name).join(' | '));
  await page.keyboard.press('Shift+Tab');
  await shot(page, 'keyboard-focus-1440');

  // Keyboard-only form flow.
  await page.goto(BASE + '/taxpayer/submissions/new');
  await page.getByRole('heading', { name: 'Your details' }).waitFor();
  await page.getByLabel('Tax period').focus();
  await page.keyboard.type('2026-09');
  await page.keyboard.press('Enter');
  await page.getByRole('alert').first().waitFor();
  check('Enter submits form from keyboard and shows errors', true);

  // ---------- 4. Logout ----------
  await page.goto(BASE + '/taxpayer/account');
  await page.getByRole('heading', { name: 'Account' }).waitFor();
  await Promise.all([page.waitForURL((u) => u.pathname === '/login', { timeout: 20000 }), page.locator('#main').getByRole('button', { name: 'Log out' }).click()]);
  check('logout lands on login page', new URL(page.url()).pathname === '/login');
  await page.goto(BASE + '/taxpayer/submissions');
  check('after logout portal requires login again', new URL(page.url()).pathname === '/login', page.url());
  const r = await page.request.post(`${BASE}/api/method/vati_tax.portal.api.get_submissions`);
  check('after logout API rejected', r.status() === 403, `HTTP ${r.status()}`);
  await ctx.close();
}

results.browser = `Microsoft Edge ${version} (Playwright Chromium driver)`;
fs.writeFileSync(path.join(OUT, 'results.json'), JSON.stringify(results, null, 2));
const failed = results.checks.filter((c) => !c.pass).length;
const axeCount = results.axe.reduce((n, a) => n + a.violations.length, 0);
console.log(`\n${results.checks.length - failed}/${results.checks.length} checks passed; axe violation groups: ${axeCount}; browser ${version}`);
await browser.close();
process.exit(failed ? 1 : 0);
