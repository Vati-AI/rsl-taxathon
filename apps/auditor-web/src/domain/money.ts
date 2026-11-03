/**
 * Money arrives as decimal strings (docs/API_CONTRACTS.md). These helpers never
 * convert through floating point, so displayed and sorted values stay exact.
 */

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

/** Format a decimal string for display, e.g. ("15000.5", "LSL") -> "LSL 15,000.50". */
export function formatAmount(value: string | null, currency: string): string | null {
  if (value === null) return null;
  const match = DECIMAL.exec(value);
  if (!match) return null;
  const [, sign, whole, fraction = ''] = match;
  const grouped = whole.replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const cents = fraction.padEnd(2, '0');
  return `${currency} ${sign}${grouped}.${cents}`;
}

/** Compare two decimal strings exactly. Missing values sort after present ones. */
export function compareDecimal(a: string | null, b: string | null): number {
  if (a === b) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  const scale = (value: string) => {
    const match = DECIMAL.exec(value);
    if (!match) return null;
    const [, sign, whole, fraction = ''] = match;
    return BigInt(sign + whole + fraction.padEnd(12, '0').slice(0, 12));
  };
  const left = scale(a);
  const right = scale(b);
  if (left === null || right === null) return left === null ? (right === null ? 0 : 1) : -1;
  return left < right ? -1 : left > right ? 1 : 0;
}
