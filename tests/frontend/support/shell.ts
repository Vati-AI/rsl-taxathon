import type { Locator, Page } from '@playwright/test';

/**
 * The Vati Workspace shell's navigation (TAX-146). From 1024px the sidebar is on the page;
 * below that it lives in a drawer opened from the header's Menu button. `sidebar` returns
 * the visible navigation either way, opening the drawer first when needed.
 */
export async function sidebar(page: Page, label: string): Promise<Locator> {
  const menu = page.getByRole('button', { name: 'Menu', exact: true });
  if (await menu.isVisible()) {
    if ((await menu.getAttribute('aria-expanded')) !== 'true') await menu.click();
    return page.getByRole('dialog').getByRole('navigation', { name: label });
  }
  return page.getByRole('navigation', { name: label });
}

/** The navigation locator without opening anything (for counting or absence checks). */
export function visibleSidebar(page: Page, label: string): Locator {
  return page.getByRole('navigation', { name: label }).filter({ visible: true });
}
