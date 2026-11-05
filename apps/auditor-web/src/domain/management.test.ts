import { describe, expect, it } from 'vitest';
import { durationText, plural, ratioText, utcDate, utcDateTime } from './management';

const CASES = { one: 'case', many: 'cases' };

describe('ratioText', () => {
  it('shows the numerator, denominator and rounded rate', () => {
    expect(ratioText({ numerator: 3, denominator: 6 }, CASES)).toEqual({
      text: '3 of 6 cases (50%)',
      percent: 50,
    });
    expect(ratioText({ numerator: 3, denominator: 7 }, CASES).text).toBe('3 of 7 cases (43%)');
    expect(ratioText({ numerator: 1, denominator: 1 }, CASES).text).toBe('1 of 1 case (100%)');
  });

  it('says "No data" for a zero denominator instead of 0%', () => {
    expect(ratioText({ numerator: 0, denominator: 0 }, CASES)).toEqual({
      text: 'No data',
      percent: null,
    });
  });
});

describe('durationText', () => {
  it('uses hours under two days and days after that', () => {
    expect(durationText('25.8')).toBe('25.8 h');
    expect(durationText('47.9')).toBe('47.9 h');
    expect(durationText('48.0')).toBe('2.0 days');
    expect(durationText('170.5')).toBe('7.1 days');
  });

  it('says "No data" when nothing was measured', () => {
    expect(durationText(null)).toBe('No data');
  });
});

describe('formatting', () => {
  it('pluralises and formats UTC dates', () => {
    expect(plural(1, 'case', 'cases')).toBe('1 case');
    expect(plural(2, 'case', 'cases')).toBe('2 cases');
    expect(utcDate('2026-10-05T12:00:00Z')).toBe('5 Oct 2026');
    expect(utcDateTime('2026-11-04T12:00:00Z')).toBe('4 Nov 2026, 12:00 UTC');
  });
});
