import { expect, test } from '@playwright/test';

// At 390px the review page keeps every control named and keyboard reachable: no
// collapsed breadcrumb menu (an unnamed button in frappe-ui) is rendered.
test('the review page has no unnamed buttons at 390px and its header links work by keyboard', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/auditor/cases/40000000-0000-4000-8000-000000000002/review');
  await expect(page.getByText(/Reviewing as/)).toBeVisible();
  await expect(page.locator('[id^="reka-dropdown-menu-trigger"]')).toHaveCount(0);
  const unnamed = await page.locator('button').evaluateAll((buttons) =>
    buttons
      .filter((b) => (b as HTMLElement).offsetParent !== null)
      .filter((b) => !(b.getAttribute('aria-label') || b.textContent?.trim()))
      .map((b) => b.outerHTML.slice(0, 80)),
  );
  expect(unnamed).toEqual([]);
  const back = page.getByRole('link', { name: 'Back to case' });
  await back.focus();
  await expect(back).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/auditor\/cases\/40000000-0000-4000-8000-000000000002$/);
});
