import { describe, expect, it } from 'vitest';
import { makeCase } from '../testing/makeCase';
import {
  formatKey,
  laneLabel,
  processingState,
  queuePage,
  statusCounts,
  statusInfo,
  type QueueQuery,
} from './cases';
import { formatDate, formatTimestamp } from './dates';
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

describe('dates', () => {
  it('shows timestamps in UTC and plain dates without timezone shifts', () => {
    expect(formatTimestamp('2026-11-03T09:45:00Z')).toBe('3 Nov 2026, 09:45 UTC');
    expect(formatTimestamp(null)).toBeNull();
    expect(formatDate('2015-03-02')).toBe('2 Mar 2015');
    expect(formatDate('2015-13-02')).toBeNull();
  });
});

describe('processingState', () => {
  it('reports a failed service with a readable reason', () => {
    const item = makeCase(6, {
      status: 'FAILED_PROCESSING',
      service_links: [
        {
          service: 'docling',
          external_type: 'extraction',
          external_id: null,
          sync: { state: 'failed', last_attempt_at: null, error_code: 'upstream_timeout' },
        },
      ],
    });
    expect(processingState(item)).toMatchObject({
      kind: 'failed',
      text: 'Document extraction (Docling) timed out',
    });
  });

  it('treats a failed status without sync detail as failed', () => {
    expect(
      processingState(makeCase(1, { status: 'FAILED_PROCESSING', service_links: [] })).kind,
    ).toBe('failed');
  });

  it('reports pending services and early statuses as in progress', () => {
    const pending = makeCase(7, {
      service_links: [
        {
          service: 'jube',
          external_type: 'case',
          external_id: null,
          sync: { state: 'pending', last_attempt_at: null, error_code: null },
        },
      ],
    });
    expect(processingState(pending)).toMatchObject({
      kind: 'pending',
      text: 'Waiting for Case management (Jube)',
    });
    expect(processingState(makeCase(1, { status: 'VALIDATING', service_links: [] })).kind).toBe(
      'pending',
    );
    expect(processingState(makeCase(1)).kind).toBe('ok');
  });
});

const cases = [
  makeCase(1, { refund_claim: { amount: '15000.00', currency: 'LSL' }, refund_lane: 'fast' }),
  makeCase(2, {
    status: 'INVESTIGATING',
    refund_claim: { amount: '9000.00', currency: 'LSL' },
    taxpayer: {
      taxpayer_id: makeCase(2).taxpayer_id,
      display_name: 'Synthetic Border Retail',
      taxpayer_type: 'company',
    },
  }),
  makeCase(10, { refund_claim: null, refund_lane: null }),
  makeCase(3, { refund_claim: { amount: '100000.00', currency: 'LSL' }, refund_lane: 'targeted' }),
];

const base: QueueQuery = {
  search: '',
  status: '',
  lane: '',
  sort: 'display_id',
  direction: 'asc',
  page: 1,
  pageSize: 2,
};

const ids = (rows: { display_id: string }[]) => rows.map((x) => x.display_id);

describe('queuePage', () => {
  it('sorts case references naturally and paginates', () => {
    expect(ids(queuePage(cases, base).rows)).toEqual(['SYN-001', 'SYN-002']);
    expect(ids(queuePage(cases, { ...base, page: 2 }).rows)).toEqual(['SYN-003', 'SYN-010']);
    expect(queuePage(cases, { ...base, page: 99 }).page).toBe(2);
  });

  it('sorts amounts numerically with missing values last in both directions', () => {
    const asc = queuePage(cases, { ...base, sort: 'refund_claim', pageSize: 10 });
    expect(ids(asc.rows)).toEqual(['SYN-002', 'SYN-001', 'SYN-003', 'SYN-010']);
    const desc = queuePage(cases, {
      ...base,
      sort: 'refund_claim',
      direction: 'desc',
      pageSize: 10,
    });
    expect(ids(desc.rows)).toEqual(['SYN-003', 'SYN-001', 'SYN-002', 'SYN-010']);
  });

  it('filters by taxpayer name, case reference, status and lane', () => {
    expect(ids(queuePage(cases, { ...base, search: 'border' }).rows)).toEqual(['SYN-002']);
    expect(queuePage(cases, { ...base, search: 'syn-010' }).total).toBe(1);
    expect(queuePage(cases, { ...base, status: 'INVESTIGATING' }).total).toBe(1);
    expect(queuePage(cases, { ...base, lane: 'targeted' }).total).toBe(1);
    expect(ids(queuePage(cases, { ...base, lane: 'none' }).rows)).toEqual(['SYN-010']);
  });

  it('returns an empty first page when nothing matches', () => {
    expect(queuePage(cases, { ...base, search: 'no such taxpayer' })).toEqual({
      rows: [],
      total: 0,
      page: 1,
      pageCount: 1,
    });
  });
});

describe('labels', () => {
  it('labels statuses without verdict wording', () => {
    expect(statusInfo('AWAITING_HUMAN').label).toBe('Awaiting officer review');
    expect(statusInfo('FAILED_PROCESSING')).toEqual({ label: 'Processing failed', theme: 'red' });
    expect(laneLabel(null)).toBe('Not set');
    expect(formatKey('new_status_type')).toBe('New status type');
  });

  it('counts cases per status in workflow order', () => {
    expect(statusCounts(cases).map((c) => [c.status, c.count])).toEqual([
      ['INVESTIGATING', 1],
      ['AWAITING_HUMAN', 3],
    ]);
  });
});
