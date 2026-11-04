import AxeBuilder from '@axe-core/playwright';
import { expect, type Page } from '@playwright/test';

/** Serious or critical WCAG 2.1 A/AA violations on the current page. */
export async function seriousAxeViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  return results.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
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
