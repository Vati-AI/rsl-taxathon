import type { Page } from '@playwright/test';

/**
 * TAX-144 page-length and naming measurements (docs/DESIGN_STANDARD.md v2, ADR-044).
 *
 * Page length: the height a reader has to scroll through, in CSS pixels. It is the
 * document height or, when the app scrolls inside a page-level container (one at
 * least half the viewport tall), that container's full content height. A region
 * that is allowed to scroll on its own does not count: a labelled
 * `role="region"` (the design standard's wide tables) or anything marked
 * `data-scroll-region`.
 *
 * Naming: user-visible text and accessible names must use the Vati Workspace
 * register, never an open-source component name. Text inside an element marked
 * `data-component-names-allowed` (the About · Open-source notices page and Admin
 * diagnostics, TAX-150) is exempt.
 */

/** Operational pages may be at most this many viewport heights at 1440×900. */
export const BUDGET_VIEWPORTS = 1.5;

/** Component names that must not reach users (DESIGN_STANDARD.md register). */
export const BANNED_NAMES = [
  'Jube',
  'OpenAleph',
  'Frappe',
  'Docling',
  'yente',
  'LangGraph',
  'Groq',
] as const;

export interface PageLength {
  /** Height a reader scrolls through, in CSS pixels. */
  heightPx: number;
  viewportPx: number;
  /** heightPx / viewportPx, two decimals. */
  viewports: number;
  /** Where the height came from: "document" or the page-level scroll container. */
  source: string;
}

export function measurePageLength(page: Page): Promise<PageLength> {
  return page.evaluate(() => {
    const vh = window.innerHeight;
    const allowed = (el: Element) =>
      Boolean(
        el.closest('[data-scroll-region]') ||
        el.closest('[role="region"][aria-label], [role="region"][aria-labelledby]'),
      );
    let height = document.documentElement.scrollHeight;
    let source = 'document';
    for (const el of Array.from(document.querySelectorAll<HTMLElement>('body *'))) {
      const style = getComputedStyle(el);
      if (!/(auto|scroll)/.test(style.overflowY)) continue;
      if (el.scrollHeight <= el.clientHeight + 1) continue;
      if (el.clientHeight < vh * 0.5 || allowed(el)) continue;
      const top = el.getBoundingClientRect().top + window.scrollY;
      const full = Math.round(top + el.scrollHeight);
      if (full > height) {
        height = full;
        source = el.tagName.toLowerCase() + (el.id ? `#${el.id}` : '');
      }
    }
    return {
      heightPx: height,
      viewportPx: vh,
      viewports: Math.round((height / vh) * 100) / 100,
      source,
    };
  });
}

export interface NameHit {
  name: string;
  /** "text", or the attribute the name was found in (aria-label, title, alt, placeholder). */
  where: string;
  /** Up to 80 characters around the hit, for the report. */
  context: string;
}

export function findComponentNames(page: Page): Promise<NameHit[]> {
  return page.evaluate(
    (names) => {
      const pattern = new RegExp(`\\b(${names.join('|')})\\b`, 'gi');
      const exempt = (el: Element | null) => Boolean(el?.closest('[data-component-names-allowed]'));
      const visible = (el: Element) => {
        const box = (el as HTMLElement).getBoundingClientRect?.();
        const style = getComputedStyle(el);
        return (
          style.visibility !== 'hidden' &&
          style.display !== 'none' &&
          (!box || box.width * box.height > 0)
        );
      };
      const hits: { name: string; where: string; context: string }[] = [];
      const scan = (text: string, where: string) => {
        for (const m of text.matchAll(pattern)) {
          const at = m.index ?? 0;
          const context = text
            .slice(Math.max(0, at - 40), at + m[0].length + 40)
            .replace(/\s+/g, ' ')
            .trim();
          const canonical = names.find((n) => n.toLowerCase() === m[0].toLowerCase()) ?? m[0];
          hits.push({ name: canonical, where, context });
        }
      };
      // Rendered text, one text node at a time so exempt regions can be skipped.
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const parent = node.parentElement;
        if (!parent || exempt(parent) || !visible(parent)) continue;
        if (parent.closest('script, style, noscript, template')) continue;
        scan(node.textContent ?? '', 'text');
      }
      // Accessible names and hints that assistive technology announces.
      for (const el of Array.from(
        document.querySelectorAll('[aria-label], [title], [alt], [placeholder]'),
      )) {
        if (exempt(el)) continue;
        for (const attr of ['aria-label', 'title', 'alt', 'placeholder']) {
          const value = el.getAttribute(attr);
          if (value) scan(value, attr);
        }
      }
      scan(document.title, 'document title');
      return hits;
    },
    [...BANNED_NAMES],
  );
}
