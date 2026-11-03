import type { StepState, SubmissionStatus } from './types';

type BadgeTheme = 'gray' | 'blue' | 'green' | 'amber' | 'red';

const STATUS: Record<SubmissionStatus, { label: string; theme: BadgeTheme }> = {
  draft: { label: 'Draft', theme: 'gray' },
  under_review: { label: 'Under review', theme: 'blue' },
  evidence_requested: { label: 'Evidence requested', theme: 'amber' },
  correction_needed: { label: 'Correction needed', theme: 'amber' },
  completed: { label: 'Completed', theme: 'green' },
};

export function statusDisplay(status: string): { label: string; theme: BadgeTheme } {
  return STATUS[status as SubmissionStatus] ?? { label: 'Status unavailable', theme: 'gray' };
}

const STEP_LABEL: Record<StepState, string> = {
  complete: 'Completed',
  current: 'In progress',
  upcoming: 'Not started',
};

export function stepStateLabel(state: StepState): string {
  return STEP_LABEL[state];
}

/**
 * Format a decimal-string amount without floating-point conversion.
 * null means "not provided"; malformed values are reported, never guessed.
 */
export function formatAmount(amount: string | null, currency: string): string {
  if (amount === null) return 'Not provided';
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(amount);
  if (!match) return 'Amount unavailable';
  const [, sign, whole, cents = ''] = match;
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${sign}${currency} ${grouped}.${cents.padEnd(2, '0')}`;
}

const DATE = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
});

export function formatDate(iso: string | null): string {
  if (!iso) return '—';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '—' : DATE.format(date);
}

const MONTH = new Intl.DateTimeFormat('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' });

export function formatPeriod(period: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(period);
  if (!match) return period;
  return MONTH.format(new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1)));
}
