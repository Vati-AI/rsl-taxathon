import path from 'node:path';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';

// Integrity review (TAX-077) against the fixture server's getIntegrityReview.
// Figures come from integration/api/fixtures/integrity.json through the same
// Frappe-free aggregation rules the Vati service uses.
const REVIEW = /\/api\/v1\/management\/integrity/;
const nav = (page: Page) => page.getByRole('navigation', { name: 'Auditor workspace' });
const shots = path.resolve(__dirname, '../../../docs/evidence/TAX-077');
const capture = Boolean(process.env.CAPTURE_SCREENSHOTS);
// Words that would turn an aggregate into an accusation or a compliance verdict.
const ACCUSATORY =
  /\b(fraud|noncompliant|non-compliant|violation|misconduct|guilty|suspicious|offender|wrongdoing|evasion)\b/i;

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- free-form edits of a JSON test body
function edit(change: (data: Record<string, any>) => void) {
  return async (route: Route) => {
    const response = await route.fetch();
    const body = await response.json();
    change(body.data);
    await route.fulfill({ response, json: body });
  };
}

async function actAs(page: Page, role: 'auditor' | 'specialist' | 'supervisor') {
  await page.addInitScript((value) => localStorage.setItem('vati.fixtureRole', value), role);
}

async function ready(page: Page) {
  await expect(page.getByRole('heading', { level: 1, name: 'Integrity review' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: 'Policy exposure' })).toBeVisible();
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

test.describe('integrity review', () => {
  test.beforeEach(async ({ page }) => {
    await actAs(page, 'supervisor');
  });

  test('supervisor sees aggregates with small groups combined and no identities', async ({
    page,
  }) => {
    await page.goto('/auditor/');
    await nav(page).getByRole('link', { name: 'Integrity review' }).click();
    await expect(page).toHaveURL(/\/auditor\/management\/integrity$/);
    await ready(page);
    await expect(nav(page).getByRole('link', { name: 'Integrity review' })).toHaveAttribute(
      'aria-current',
      'page',
    );

    const areas = page.getByRole('region', { name: 'Confirmed policy gaps by policy area' });
    await expect(areas.getByRole('row', { name: /Related-party service charges/ })).toBeVisible();
    await expect(areas.getByRole('row', { name: /Other policy areas/ })).toContainText(
      '3 groups combined to protect privacy',
    );
    const controls = page.getByRole('region', { name: 'Overrides by control' });
    await expect(controls.getByRole('row', { name: /Case assignment/ })).toContainText('4');
    await expect(controls.getByRole('row', { name: /Other controls/ })).toBeVisible();
    await expect(page.getByTestId('withheld')).toContainText('1 signal concerns you');

    const text = (await page.locator('main').innerText()).toLowerCase();
    expect(text).not.toMatch(/@example\.test/);
    expect(text).not.toMatch(ACCUSATORY);
    await expect(page.getByText(/not a finding of noncompliance/)).toBeVisible();
    await shot(page, 'integrity-1440');
  });

  test('the dashboard links to the review', async ({ page }) => {
    // TAX-149: the link is in the Ledger assurance tab; Integrity is also a Reports tab.
    await page.goto('/auditor/management/assurance');
    await page.locator('main').getByRole('link', { name: 'integrity review', exact: true }).click();
    await ready(page);
  });

  for (const role of ['auditor', 'specialist'] as const) {
    test(`the server refuses ${role === 'auditor' ? 'an auditor' : 'a specialist'} and no figures are shown`, async ({
      page,
    }) => {
      await actAs(page, role);
      const responses: number[] = [];
      page.on('response', (r) => {
        if (REVIEW.test(r.url())) responses.push(r.status());
      });
      await page.goto('/auditor/management/integrity');
      await expect(page.getByRole('alert').getByText('Supervisors only')).toBeVisible();
      expect(responses).toEqual([403]);
      await expect(page.getByRole('heading', { level: 2, name: 'Policy exposure' })).toHaveCount(0);
      await expect(nav(page).getByRole('link', { name: 'Integrity review' })).toHaveCount(0);
      await shot(page, `denied-${role}-1440`);
    });
  }

  test('insufficient volume shows no counts at all', async ({ page }) => {
    await page.route(
      REVIEW,
      edit((d) => {
        d.policy_exposure = {
          ...d.policy_exposure,
          status: 'insufficient_volume',
          confirmed_gaps: null,
          areas: [],
          combined: null,
        };
        d.override_patterns = {
          ...d.override_patterns,
          status: 'insufficient_volume',
          total_overrides: null,
          by_control: [],
          combined: null,
        };
      }),
    );
    await page.goto('/auditor/management/integrity');
    await ready(page);
    await expect(page.getByTestId('exposure-insufficient')).toBeVisible();
    await expect(page.getByTestId('overrides-insufficient')).toBeVisible();
    await expect(page.getByRole('region', { name: 'Overrides by control' })).toHaveCount(0);
    await shot(page, 'insufficient-volume-1440');
  });

  test('unavailable sources are shown as unavailable, never as zero', async ({ page }) => {
    await page.route(
      REVIEW,
      edit((d) => {
        d.policy_exposure = { status: 'unavailable' };
        d.override_patterns = { status: 'unavailable' };
        d.review_signals = { status: 'unavailable' };
        d.ledger = { available: false };
        d.missing_data = [
          { metric: 'policy_exposure', detail: 'Reviewed policy-gap findings are not stored yet.' },
          { metric: 'integrity', detail: 'The audit ledger is not available in this environment.' },
        ];
      }),
    );
    await page.goto('/auditor/management/integrity');
    await ready(page);
    await expect(page.getByText('Policy-gap figures are not available')).toBeVisible();
    await expect(page.getByText('Override figures are not available')).toBeVisible();
    await expect(
      page.getByText('Not available in this environment', { exact: true }),
    ).toBeVisible();
    await shot(page, 'unavailable-1440');
  });

  test('a response that would reveal a person is refused rather than shown', async ({ page }) => {
    await page.route(
      REVIEW,
      edit((d) => {
        d.override_patterns.by_control[0].label = 'officer.a@example.test';
      }),
    );
    await page.goto('/auditor/management/integrity');
    await expect(page.getByText(/cannot be shown safely/)).toBeVisible();
    await expect(page.getByText('officer.a@example.test')).toHaveCount(0);
  });

  test('keyboard reaches the window control and the table regions with visible focus', async ({
    page,
  }) => {
    await page.goto('/auditor/management/integrity');
    await ready(page);
    const reached: string[] = [];
    for (let i = 0; i < 40 && reached.length < 2; i += 1) {
      await page.keyboard.press('Tab');
      const label = await page.evaluate(() => document.activeElement?.getAttribute('aria-label'));
      if (label && /by policy area|by control/.test(label) && !reached.includes(label)) {
        reached.push(label);
        const outline = await page.evaluate(() => {
          const style = getComputedStyle(document.activeElement as Element);
          return style.boxShadow !== 'none' || style.outlineStyle !== 'none';
        });
        expect(outline).toBe(true);
      }
    }
    expect(reached).toEqual(['Confirmed policy gaps by policy area', 'Overrides by control']);
  });

  for (const width of [1440, 1024, 390]) {
    test(`no serious axe violations or page-wide scroll at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/auditor/management/integrity');
      await ready(page);
      expect(await seriousViolations(page)).toEqual([]);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
      await shot(page, `integrity-${width}`);
    });
  }
});
