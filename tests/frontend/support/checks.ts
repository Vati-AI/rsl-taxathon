import AxeBuilder from '@axe-core/playwright';
import { expect, type Page } from '@playwright/test';

/**
 * Recorded defects owned by another task. Each entry excludes exactly one rule on
 * one element so the rest of the page stays checked. Remove an entry once fixed;
 * the matrix test then fails if the defect comes back.
 */
export const KNOWN_A11Y_ISSUES = [
  {
    path: '/auditor/showcase',
    rule: 'label-content-name-mismatch',
    target: 'button[aria-label="Saving"]',
    note:
      'WCAG 2.5.3 Label in Name: the showcase loading demo shows "Show loading" but is ' +
      'named "Saving" (apps/auditor-web/src/views/ShowcaseView.vue, TAX-020/TAX-040).',
  },
  {
    // Found by the TAX-055 SYN-005 route; the only auditor field error it exercises.
    path: '/auditor/cases/40000000-0000-4000-8000-000000000005/review',
    rule: 'color-contrast',
    target: '#reject-reason-error',
    note:
      "WCAG 1.4.3 Contrast: frappe-ui's field error text (text-ink-red-5, #e03434 on white) " +
      'measures 4.45:1, below 4.5:1. The taxpayer portal already overrides it with ' +
      '--ink-red-8 (apps/taxpayer-web/src/styles.css, TAX-021); the auditor app has no such rule ' +
      '(apps/auditor-web/src/style.css). Owner: auditor frontend.',
  },
] as const;

/** Serious or critical WCAG 2.1 A/AA violations on the current page, minus recorded known issues. */
export async function seriousAxeViolations(page: Page): Promise<string[]> {
  // Let open/close transitions finish first: axe measures the colours it sees, so a
  // dialog caught mid fade-in reports false colour-contrast failures (see #183).
  // Looping animations (spinners, skeletons) never finish, so they are ignored.
  await page.waitForFunction(() =>
    document
      .getAnimations()
      .every(
        (animation) =>
          animation.playState !== 'running' ||
          animation.effect?.getTiming().iterations === Infinity,
      ),
  );
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  const path = new URL(page.url()).pathname;
  const known = KNOWN_A11Y_ISSUES.filter((k) => k.path === path);
  return results.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .flatMap((v) => {
      const nodes = v.nodes
        .map((n) => n.target.join(' '))
        .filter((t) => !known.some((k) => k.rule === v.id && k.target === t));
      return nodes.length ? [`${v.id}: ${nodes.join(', ')}`] : [];
    });
}

/** Pixels of page-wide horizontal scroll (0 means the page reflows). */
export function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

/** Wait for a page's single level-1 heading and for its data requests to settle. */
export async function pageReady(page: Page, heading: string | RegExp): Promise<void> {
  await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
  await page.waitForLoadState('networkidle');
}

/** Press Tab until `locator`'s element has focus (bounded), then assert it. */
export async function tabUntilFocused(
  page: Page,
  locator: ReturnType<Page['locator']>,
  limit = 30,
): Promise<void> {
  for (let i = 0; i < limit; i++) {
    if (await locator.evaluate((el) => el === document.activeElement).catch(() => false)) break;
    await page.keyboard.press('Tab');
  }
  await expect(locator).toBeFocused();
}

/** The focused element draws a visible focus indicator (outline or ring shadow). */
export async function hasVisibleFocus(locator: ReturnType<Page['locator']>): Promise<boolean> {
  return locator.evaluate((el) => {
    const style = getComputedStyle(el);
    const outline = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0;
    return outline || style.boxShadow !== 'none';
  });
}
