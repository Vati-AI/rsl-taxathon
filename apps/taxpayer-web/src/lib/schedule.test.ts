import { describe, expect, it, vi } from 'vitest';

// Pure logic only; avoid loading the whole component library.
vi.mock('./frappe-call', () => ({ call: vi.fn() }));
import {
  cents,
  emptyInvoice,
  mapScheduleErrors,
  periodEnd,
  scheduleTotals,
  toPayload,
  validateSchedule,
  type InvoiceDraft,
} from './schedule';
import { stepForField, validateDetails, emptyClaim } from './validation';

const DOC = 'e1000000-0000-4000-8000-000000000001';
const TODAY = new Date('2026-11-05T12:00:00Z');

function row(overrides: Partial<InvoiceDraft> = {}): InvoiceDraft {
  return {
    ...emptyInvoice(DOC),
    invoiceNumber: 'INV-0912',
    invoiceDate: '2026-09-12',
    supplierName: 'Synthetic Supplies',
    net: '10,000.00',
    vat: '1500',
    gross: '11500.00',
    ...overrides,
  };
}

const check = (rows: InvoiceDraft[], uploaded = [DOC]) =>
  validateSchedule(rows, { period: '2026-09', uploaded: new Set(uploaded), today: TODAY });

describe('schedule arithmetic', () => {
  it('works in whole cents, never floats', () => {
    expect(cents('0.10')).toBe(10);
    expect(cents('12,500.5')).toBe(1250050);
    expect(cents('1e5')).toBeNull();
    expect(scheduleTotals([row(), row({ net: '0.20', vat: '0.10', gross: '0.30' })])).toEqual({
      net: '10000.20',
      vat: '1500.10',
      gross: '11500.30',
    });
  });

  it('knows the last day of a period', () => {
    expect(periodEnd('2026-09')).toBe('2026-09-30');
    expect(periodEnd('2024-02')).toBe('2024-02-29');
    expect(periodEnd('nope')).toBeNull();
  });
});

describe('validateSchedule (mirrors vati_tax/submissions/invoices.py)', () => {
  it('accepts a valid schedule', () => {
    expect(check([row()])).toEqual({ rows: {} });
  });

  it('requires net plus VAT to equal the total exactly', () => {
    expect(check([row({ gross: '11500.01' })]).rows[0].gross).toMatch(/must equal the total/);
  });

  it('checks dates, required fields, documents and duplicates', () => {
    expect(check([row({ invoiceDate: '2026-12-01' })]).rows[0].invoiceDate).toMatch(/future/);
    expect(check([row({ invoiceDate: '2026-10-01' })]).rows[0].invoiceDate).toMatch(
      /after the end/,
    );
    const blank = check([row({ invoiceNumber: '', supplierName: ' ', invoiceEvidenceId: '' })]);
    expect(Object.keys(blank.rows[0]).sort()).toEqual([
      'invoiceEvidenceId',
      'invoiceNumber',
      'supplierName',
    ]);
    expect(check([row()], []).rows[0].invoiceEvidenceId).toMatch(/uploaded for this claim/);
    const dup = check([row(), row({ invoiceNumber: ' inv-0912 ' })]);
    expect(dup.rows[1].invoiceNumber).toMatch(/already listed in row 1/);
  });

  it('needs at least one invoice', () => {
    expect(check([]).general).toMatch(/at least one/);
  });
});

describe('payload and server errors', () => {
  it('sends normalised amounts and nulls for blanks', () => {
    expect(toPayload([row()])[0]).toMatchObject({
      net_amount: '10000.00',
      vat_amount: '1500.00',
      amount_paid: null,
      supplier_tin: null,
      contract_evidence_id: null,
    });
  });

  it('places server row errors on the right field', () => {
    expect(
      mapScheduleErrors([
        { field: 'invoices.1.gross_amount', message: 'Total mismatch' },
        { field: 'invoices', message: 'Add one' },
      ]),
    ).toEqual({ rows: { 1: { gross: 'Total mismatch' } }, general: 'Add one' });
    expect(stepForField('invoices')).toBe('invoices');
  });

  it('requires the period figures but accepts zero', () => {
    const base = {
      ...emptyClaim(),
      period: '2026-09',
      amount: '1',
      reason: 'other',
      explanation: 'x',
    };
    expect(validateDetails({ ...base, outputVat: '0', inputVat: '0.00' }, TODAY)).toEqual({});
    const errors = validateDetails({ ...base, outputVat: '', inputVat: 'abc' }, TODAY);
    expect(errors.outputVat).toMatch(/0\.00 if there was none/);
    expect(errors.inputVat).toMatch(/numbers only/);
  });
});
