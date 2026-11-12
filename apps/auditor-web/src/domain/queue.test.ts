import { describe, expect, it } from 'vitest';
import type { CaseAttention } from '../api/vati';
import { makeCase } from '../testing/makeCase';
import { casesInView, inView, parseView, viewCounts } from './queue';
import { compareRows, queueCsv, queueRows } from './queueTable';

const attention = (a: Partial<CaseAttention> = {}): CaseAttention => ({
  decision_awaiting_approval: false,
  outstanding_evidence_requests: 0,
  evidence_due_date: null,
  overdue: false,
  ...a,
});

describe('queue views (TAX-147)', () => {
  const cases = [
    makeCase(1, { attention: attention({ decision_awaiting_approval: true }) }),
    makeCase(2, {
      attention: attention({
        outstanding_evidence_requests: 1,
        evidence_due_date: '2026-11-01',
        overdue: true,
      }),
    }),
    makeCase(3, { status: 'AWAITING_EVIDENCE', attention: attention() }),
    makeCase(4, { attention: attention() }),
  ];

  it('counts each view from the case list itself', () => {
    expect(viewCounts(cases)).toEqual({
      all: 4,
      awaiting_evidence: 2,
      awaiting_approval: 1,
      overdue: 1,
    });
    expect(casesInView(cases, 'overdue').map((c) => c.display_id)).toEqual([cases[1].display_id]);
  });

  it('never guesses: without attention from the server the count is unknown', () => {
    const withoutAttention = [...cases, makeCase(5)];
    const counts = viewCounts(withoutAttention);
    expect(counts.awaiting_approval).toBeUndefined();
    expect(counts.overdue).toBeUndefined();
    // Status alone still answers "awaiting evidence" and "all".
    expect(counts.all).toBe(5);
    expect(counts.awaiting_evidence).toBe(2);
    expect(inView(makeCase(6), 'overdue')).toBeNull();
    expect(casesInView([makeCase(6)], 'overdue')).toEqual([]);
  });

  it('a resolved case is not overdue', () => {
    const resolved = makeCase(7, { status: 'RESOLVED', attention: attention({ overdue: true }) });
    expect(inView(resolved, 'overdue')).toBe(false);
  });

  it('reads the view from the URL, falling back to all', () => {
    expect(parseView('overdue')).toBe('overdue');
    expect(parseView('anything')).toBe('all');
    expect(parseView(undefined)).toBe('all');
  });
});

describe('queue table rows and export', () => {
  it('sorts empty values last in both directions', () => {
    const rows = queueRows([
      makeCase(1, { refund_claim: null }),
      makeCase(2, { refund_claim: { amount: '500.00', currency: 'LSL' } }),
      makeCase(3, { refund_claim: { amount: '90.00', currency: 'LSL' } }),
    ]);
    const asc = [...rows].sort((a, b) =>
      compareRows(a, b, { key: 'refund_claim', direction: 'asc' }),
    );
    const desc = [...rows].sort((a, b) =>
      compareRows(a, b, { key: 'refund_claim', direction: 'desc' }),
    );
    expect(asc.map((r) => r.refund_claim)).toEqual(['90.00', '500.00', null]);
    expect(desc.map((r) => r.refund_claim)).toEqual(['500.00', '90.00', null]);
  });

  it('exports exactly the visible columns, quoting and neutralising formulas', () => {
    const rows = queueRows([
      makeCase(1, {
        taxpayer: { ...makeCase(1).taxpayer, display_name: '=HYPERLINK("x"), Ltd' },
        attention: attention({ evidence_due_date: '2026-11-01', overdue: true }),
      }),
    ]);
    const csv = queueCsv(rows, [
      { key: 'display_id', label: 'Case' },
      { key: 'taxpayer', label: 'Taxpayer' },
      { key: 'refund_claim', label: 'Refund claimed' },
      { key: 'due', label: 'Evidence due' },
    ]);
    const [header, line] = csv.trim().split('\r\n');
    expect(header).toBe('Case,Taxpayer,Refund claimed (amount),Evidence due,Currency');
    expect(line).toContain(`"'=HYPERLINK(""x""), Ltd"`);
    expect(line).toContain('2026-11-01 (overdue)');
    expect(csv).not.toContain('Why selected');
  });
});
