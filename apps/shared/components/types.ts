/** Types shared by the Vati Workspace kit components (TAX-145). */
import type { RouteLocationRaw } from 'vue-router';

export interface KeyFact {
  label: string;
  value: string | null | undefined;
  /** Amounts and dates: tabular numerals. */
  numeric?: boolean;
}

export interface RoutedTab {
  label: string;
  to: RouteLocationRaw;
  /** Route name(s) that make this tab current. */
  match: string | string[];
  count?: number;
}

export type SortDirection = 'asc' | 'desc';

export interface DataColumn {
  key: string;
  label: string;
  /** One sentence explaining the column, shown from an info button. */
  info?: string;
  sortable?: boolean;
  /** Right-align and use tabular numerals (amounts, counts, dates). */
  numeric?: boolean;
  /** Hide below the md breakpoint to keep the table readable on phones. */
  hideOnMobile?: boolean;
  /** Keep the value on one line (references, codes). */
  nowrap?: boolean;
}

export interface NavItem {
  label: string;
  to: RouteLocationRaw;
  /** An outline icon class, e.g. lucide-inbox. */
  icon: string;
  /** Route name(s) that make this item current. */
  match: string | string[];
  count?: number;
}

export interface NavSection {
  label?: string;
  items: NavItem[];
}

export interface AppLink {
  label: string;
  href: string;
  current?: boolean;
}

export interface CommandItem {
  id: string;
  label: string;
  /** Group heading: "Cases", "Taxpayers", "Pages". */
  group: string;
  /** Secondary text, e.g. TIN or status. */
  hint?: string;
  /** Extra words that should match the search (references, TINs). */
  keywords?: string[];
}
