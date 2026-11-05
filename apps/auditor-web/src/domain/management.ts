import type { Ratio, SignalKind } from '../api/management';

/**
 * "3 of 6 (50%)". A zero denominator means there was nothing to measure, so it
 * reads "No data" rather than a misleading 0%.
 */
export function ratioText(value: Ratio, noun: { one: string; many: string }) {
  if (value.denominator === 0) return { text: 'No data', percent: null };
  const percent = Math.round((value.numerator / value.denominator) * 100);
  const unit = value.denominator === 1 ? noun.one : noun.many;
  return { text: `${value.numerator} of ${value.denominator} ${unit} (${percent}%)`, percent };
}

/** Hours from the API as "26.5 h", or days past two days ("4.3 days"). Null is "No data". */
export function durationText(hours: string | null): string {
  if (hours === null) return 'No data';
  const value = Number(hours);
  if (value < 48) return `${hours} h`;
  return `${(value / 24).toFixed(1)} days`;
}

export function plural(n: number, one: string, many: string) {
  return `${n} ${n === 1 ? one : many}`;
}

/** Review signals are prompts for a supervisor to look; never findings about anyone. */
export const SIGNAL_TEXT: Record<SignalKind, { label: string; help: string }> = {
  repeated_overrides: {
    label: 'Repeated overrides',
    help: 'One person recorded three or more control overrides in this window.',
  },
  repeated_separation_of_duties_attempts: {
    label: 'Repeated refused self-approvals or overrides',
    help: 'One person was refused two or more times for approving or overriding their own work.',
  },
  ledger_integrity: {
    label: 'Audit ledger check failed',
    help: 'The ledger hash chain does not verify. Some entries may have been changed or removed.',
  },
};

const DATE_TIME = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  timeZone: 'UTC',
});
const DATE = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
});

export function utcDateTime(iso: string) {
  return `${DATE_TIME.format(new Date(iso))} UTC`;
}

export function utcDate(iso: string) {
  return DATE.format(new Date(iso));
}
