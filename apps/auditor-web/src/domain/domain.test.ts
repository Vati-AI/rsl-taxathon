import { describe, expect, it } from 'vitest';
import type { FixtureCase } from '../api/fixtures';
import { laneCounts, queuePage, reviewLane, scenarioLabel, type QueueQuery } from './cases';
import { compareDecimal, formatAmount } from './money';

describe('formatAmount', () => {
  it('formats decimal strings exactly without floating point', () => {
    expect(formatAmount('15000.00', 'LSL')).toBe('LSL 15,000.00');
    expect(formatAmount('1234567890123456789.5', 'LSL')).toBe('LSL 1,234,567,890,123,456,789.50');
    expect(formatAmount('0.1', 'LSL')).toBe('LSL 0.10');
    expect(formatAmount('-25', 'LSL')).toBe('LSL -25.00');
  });

  it('returns null for missing or malformed values', () => {
    expect(formatAmount(null, 'LSL')).toBeNull();
    expect(formatAmount('12,000', 'LSL')).toBeNull();
  });
});

describe('compareDecimal', () => {
  it('orders exactly and keeps missing values last', () => {
    expect(compareDecimal('9.99', '10.00')).toBe(-1);
    expect(compareDecimal('0.30', '0.3')).toBe(0);
    expect(compareDecimal('-5', '1')).toBe(-1);
    expect(compareDecimal(null, '1')).toBe(1);
    expect(compareDecimal('1', null)).toBe(-1);
  });
});

function item(
  id: string,
  name: string,
  claim: string | null,
  expected: string,
  scenario = 'legitimate_refund',
): FixtureCase {
  return {
    id,
    name,
    scenario,
    currency: 'LSL',
    refund_claim: claim,
    supported_refund: claim,
    expected,
    synthetic: true,
  };
}

const cases = [
  item('SYN-001', 'Mountain Wholesale', '15000.00', 'human_clearance'),
  item('SYN-002', 'Border Retail', '9000.00', 'targeted_evidence_request', 'related_party_risk'),
  item('SYN-010', 'Valley Services', null, 'specialist_review'),
  item('SYN-003', 'Highland Imports', '100000.00', 'targeted_evidence_request'),
];

const base: QueueQuery = {
  search: '',
  scenario: '',
  lane: '',
  sort: 'id',
  direction: 'asc',
  page: 1,
  pageSize: 2,
};

describe('queuePage', () => {
  it('sorts IDs naturally and paginates', () => {
    const first = queuePage(cases, base);
    expect(first.rows.map((x) => x.id)).toEqual(['SYN-001', 'SYN-002']);
    expect(first.pageCount).toBe(2);
    expect(queuePage(cases, { ...base, page: 2 }).rows.map((x) => x.id)).toEqual([
      'SYN-003',
      'SYN-010',
    ]);
  });

  it('clamps an out-of-range page', () => {
    expect(queuePage(cases, { ...base, page: 99 }).page).toBe(2);
  });

  it('sorts amounts numerically with missing values last in both directions', () => {
    const asc = queuePage(cases, { ...base, sort: 'refund_claim', pageSize: 10 });
    expect(asc.rows.map((x) => x.id)).toEqual(['SYN-002', 'SYN-001', 'SYN-003', 'SYN-010']);
    const desc = queuePage(cases, {
      ...base,
      sort: 'refund_claim',
      direction: 'desc',
      pageSize: 10,
    });
    expect(desc.rows.map((x) => x.id)).toEqual(['SYN-003', 'SYN-001', 'SYN-002', 'SYN-010']);
  });

  it('filters by search text, scenario and review lane', () => {
    expect(queuePage(cases, { ...base, search: 'border' }).rows.map((x) => x.id)).toEqual([
      'SYN-002',
    ]);
    expect(queuePage(cases, { ...base, search: 'syn-010' }).total).toBe(1);
    expect(queuePage(cases, { ...base, scenario: 'related_party_risk' }).total).toBe(1);
    expect(queuePage(cases, { ...base, lane: 'targeted_evidence_request' }).total).toBe(2);
  });

  it('returns an empty first page when nothing matches', () => {
    const page = queuePage(cases, { ...base, search: 'no such taxpayer' });
    expect(page).toEqual({ rows: [], total: 0, page: 1, pageCount: 1 });
  });
});

describe('labels', () => {
  it('labels known scenarios and lanes and never uses a verdict colour', () => {
    expect(scenarioLabel('imported_services_gap')).toBe('Imported services evidence gap');
    for (const lane of [
      'human_clearance',
      'targeted_evidence_request',
      'specialist_review',
      'policy_gap_review',
    ]) {
      expect(reviewLane(lane).theme).not.toBe('red');
    }
  });

  it('formats unknown keys as readable labels instead of failing', () => {
    expect(scenarioLabel('new_scenario_type')).toBe('New scenario type');
    expect(reviewLane('future_lane')).toEqual({ label: 'Future lane', theme: 'gray' });
  });

  it('counts cases per review lane', () => {
    expect(laneCounts(cases)[0]).toMatchObject({ key: 'targeted_evidence_request', count: 2 });
  });
});
