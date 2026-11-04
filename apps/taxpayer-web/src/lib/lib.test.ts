import { describe, expect, it, vi } from 'vitest';
import { classifyError } from './api';

// Pure logic only; avoid loading the whole component library.
vi.mock('./frappe-call', () => ({ call: vi.fn() }));
import { formatAmount, formatPeriod, statusDisplay } from './format';
import { loginUrl, portalPath } from './portal-path';
import { emptyClaim, normaliseAmount, validateDetails } from './validation';

const TODAY = new Date('2026-11-03T12:00:00Z');

describe('portalPath / loginUrl', () => {
  it('keeps portal paths and drops query/fragment', () => {
    expect(portalPath('/taxpayer/submissions/SUB-SYN-0001?x=1#y')).toBe(
      '/taxpayer/submissions/SUB-SYN-0001',
    );
  });

  it.each([
    'https://evil.example/taxpayer',
    '//evil.example/taxpayer',
    '/desk',
    '/taxpayer/../desk',
    '/taxpayerX',
    '\\\\evil.example',
    'javascript:alert(1)',
    '',
  ])('falls back to the portal root for %s', (hostile) => {
    expect(portalPath(hostile)).toBe('/taxpayer');
  });

  it('builds a login URL that returns to the requested page', () => {
    const url = new URL(loginUrl('/taxpayer/account'), 'http://site.test');
    expect(url.pathname).toBe('/login');
    expect(url.searchParams.get('redirect-to')).toBe('/taxpayer/account');
  });
});

describe('formatAmount', () => {
  it('groups decimal strings without float rounding', () => {
    expect(formatAmount('48210.55', 'LSL')).toBe('LSL 48,210.55');
    expect(formatAmount('9007199254740993.10', 'LSL')).toBe('LSL 9,007,199,254,740,993.10');
  });

  it('distinguishes missing, zero and malformed', () => {
    expect(formatAmount(null, 'LSL')).toBe('Not provided');
    expect(formatAmount('0.00', 'LSL')).toBe('LSL 0.00');
    expect(formatAmount('1e5', 'LSL')).toBe('Amount unavailable');
  });
});

describe('display helpers', () => {
  it('labels unknown statuses instead of guessing', () => {
    expect(statusDisplay('approved_by_ai')).toEqual({ label: 'Status unavailable', theme: 'gray' });
    expect(statusDisplay('evidence_requested').label).toBe('Evidence requested');
  });

  it('formats periods as month and year', () => {
    expect(formatPeriod('2026-09')).toBe('September 2026');
  });
});

describe('validateDetails (VAT refund claim, TAX-043)', () => {
  const valid = {
    ...emptyClaim(),
    period: '2026-09',
    amount: '15,000.5',
    reason: 'excess_input_vat',
  };

  it('accepts complete claim details', () => {
    expect(validateDetails(valid, TODAY)).toEqual({});
    expect(normaliseAmount(valid.amount)).toBe('15000.50');
  });

  it('reports every missing field with an actionable message', () => {
    const errors = validateDetails(emptyClaim(), TODAY);
    expect(Object.keys(errors).sort()).toEqual(['amount', 'period', 'reason']);
    expect(errors.period).toMatch(/2026-09/);
  });

  it('rejects future periods, bad months, quarters and malformed amounts', () => {
    expect(validateDetails({ ...valid, period: '2026-12' }, TODAY).period).toMatch(/future/);
    expect(validateDetails({ ...valid, period: '2026-13' }, TODAY).period).toBeDefined();
    expect(validateDetails({ ...valid, period: '2026-Q3' }, TODAY).period).toBeDefined();
    expect(validateDetails({ ...valid, period: '1999-12' }, TODAY).period).toMatch(/2000/);
    expect(validateDetails({ ...valid, amount: '12.345' }, TODAY).amount).toBeDefined();
    expect(validateDetails({ ...valid, amount: '-5' }, TODAY).amount).toBeDefined();
    expect(validateDetails({ ...valid, contactEmail: 'nope' }, TODAY).contactEmail).toBeDefined();
  });

  it('requires a claimed amount above zero and within range', () => {
    expect(validateDetails({ ...valid, amount: '0.00' }, TODAY).amount).toMatch(/more than 0/);
    expect(validateDetails({ ...valid, amount: '000' }, TODAY).amount).toMatch(/more than 0/);
    expect(validateDetails({ ...valid, amount: '1000000000000' }, TODAY).amount).toMatch(/large/);
    expect(validateDetails({ ...valid, amount: '999999999999.99' }, TODAY)).toEqual({});
  });

  it('needs an explanation only for another reason', () => {
    expect(validateDetails({ ...valid, reason: 'other' }, TODAY).explanation).toBeDefined();
    expect(
      validateDetails({ ...valid, reason: 'other', explanation: 'Synthetic reason' }, TODAY),
    ).toEqual({});
    expect(validateDetails({ ...valid, reason: 'refund_please' }, TODAY).reason).toBeDefined();
  });

  it('does not modify the entered values', () => {
    const draft = { ...valid, amount: 'abc' };
    validateDetails(draft, TODAY);
    expect(draft.amount).toBe('abc');
  });
});

describe('classifyError', () => {
  it('treats a rejected session check as unauthenticated', () => {
    expect(classifyError({ exc_type: 'PermissionError', status: 403 }, true).kind).toBe(
      'unauthenticated',
    );
  });

  it('treats a rejected data call as denied, never as success', () => {
    expect(classifyError({ status: 403 }).kind).toBe('denied');
    expect(classifyError({ exc_type: 'DoesNotExistError', status: 404 }).kind).toBe('not_found');
    expect(classifyError(new TypeError('Failed to fetch')).kind).toBe('unavailable');
  });
});
