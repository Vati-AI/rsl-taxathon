import { chromium, type FullConfig } from '@playwright/test';
import { TAXPAYER } from './env';
import { TAXPAYER_ROUTES } from './matrix';
import { mockTaxpayerSession } from './sessions';

/**
 * Visit every taxpayer route once before tests start. On first visits the Vite
 * dev server discovers and pre-bundles dependencies and can force a full page
 * reload; doing that here keeps the reload out of the tests themselves.
 */
export default async function warmup(config: FullConfig): Promise<void> {
  const channel = config.projects[0]?.use.channel;
  const browser = await chromium.launch({ channel });
  try {
    const page = await browser.newPage();
    await mockTaxpayerSession(page, 'taxpayer');
    for (const route of TAXPAYER_ROUTES) {
      await page.goto(TAXPAYER + route.path);
      await page.getByRole('heading', { level: 1 }).first().waitFor({ timeout: 60_000 });
      await page.waitForLoadState('networkidle');
    }
  } finally {
    await browser.close();
  }
}
