import type { FixtureCase } from '../api/fixtures';
import { compareDecimal } from './money';

export type BadgeTheme = 'gray' | 'blue' | 'green' | 'amber' | 'red';

const SCENARIOS: Record<string, string> = {
  legitimate_refund: 'Legitimate unusual refund',
  related_party_risk: 'Related-party charges',
  ambiguous_service: 'Employment or consultancy',
  imported_services_gap: 'Imported services evidence gap',
  unverified_legal_route: 'No verified legal route',
};

// Review lanes are routing signals for a human officer, never verdicts, so no
// lane uses the red "danger" theme.
const REVIEW_LANES: Record<string, { label: string; theme: BadgeTheme }> = {
  human_clearance: { label: 'Human clearance', theme: 'green' },
  targeted_evidence_request: { label: 'Targeted evidence request', theme: 'amber' },
  specialist_review: { label: 'Specialist review', theme: 'blue' },
  policy_gap_review: { label: 'Policy gap review', theme: 'gray' },
};

function humanize(key: string): string {
  const text = key.replaceAll('_', ' ').trim();
  return text ? text[0].toUpperCase() + text.slice(1) : 'Unknown';
}

export function scenarioLabel(key: string): string {
  return SCENARIOS[key] ?? humanize(key);
}

export function reviewLane(key: string): { label: string; theme: BadgeTheme } {
  return REVIEW_LANES[key] ?? { label: humanize(key), theme: 'gray' };
}

export type SortKey = 'id' | 'name' | 'scenario' | 'refund_claim' | 'supported_refund' | 'expected';
export type SortDirection = 'asc' | 'desc';

export interface QueueQuery {
  search: string;
  scenario: string;
  lane: string;
  sort: SortKey;
  direction: SortDirection;
  page: number;
  pageSize: number;
}

export interface QueuePage {
  rows: FixtureCase[];
  total: number;
  page: number;
  pageCount: number;
}

export function queuePage(cases: FixtureCase[], query: QueueQuery): QueuePage {
  const needle = query.search.trim().toLocaleLowerCase();
  const filtered = cases.filter(
    (item) =>
      (!needle ||
        item.id.toLocaleLowerCase().includes(needle) ||
        item.name.toLocaleLowerCase().includes(needle)) &&
      (!query.scenario || item.scenario === query.scenario) &&
      (!query.lane || item.expected === query.lane),
  );
  const factor = query.direction === 'asc' ? 1 : -1;
  const sorted = [...filtered].sort((a, b) => {
    const key = query.sort;
    if (key === 'refund_claim' || key === 'supported_refund') {
      // Missing amounts stay last whatever the direction.
      if (a[key] === null || b[key] === null) return compareDecimal(a[key], b[key]);
      return factor * compareDecimal(a[key], b[key]);
    }
    const left =
      key === 'scenario'
        ? scenarioLabel(a[key])
        : key === 'expected'
          ? reviewLane(a[key]).label
          : a[key];
    const right =
      key === 'scenario'
        ? scenarioLabel(b[key])
        : key === 'expected'
          ? reviewLane(b[key]).label
          : b[key];
    return factor * left.localeCompare(right, undefined, { numeric: true });
  });
  const pageCount = Math.max(1, Math.ceil(sorted.length / query.pageSize));
  const page = Math.min(Math.max(1, query.page), pageCount);
  const start = (page - 1) * query.pageSize;
  return {
    rows: sorted.slice(start, start + query.pageSize),
    total: sorted.length,
    page,
    pageCount,
  };
}

export function laneCounts(
  cases: FixtureCase[],
): { key: string; label: string; theme: BadgeTheme; count: number }[] {
  const counts = new Map<string, number>();
  for (const item of cases) counts.set(item.expected, (counts.get(item.expected) ?? 0) + 1);
  return [...counts.entries()]
    .map(([key, count]) => ({ key, count, ...reviewLane(key) }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}
