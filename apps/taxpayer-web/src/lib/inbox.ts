/**
 * Evidence-request inbox (TAX-152): requests grouped like messages. Requests that need a
 * response come first, by due date (overdue, due within 7 days, due later, no due date),
 * then answered requests, then requests that are no longer needed. Within a group the
 * earliest due date comes first.
 */
import type { EvidenceRequestView } from './types';

export interface InboxGroup {
  key: 'overdue' | 'this_week' | 'later' | 'no_date' | 'answered' | 'closed';
  label: string;
  items: EvidenceRequestView[];
}

const LABELS: Record<InboxGroup['key'], string> = {
  overdue: 'Past the reply-by date',
  this_week: 'Reply within 7 days',
  later: 'Reply later',
  no_date: 'No reply-by date',
  answered: 'Answered',
  closed: 'No longer needed',
};

function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}

export function groupKey(item: EvidenceRequestView, today: string): InboxGroup['key'] {
  if (item.status === 'response_sent') return 'answered';
  if (item.status === 'no_longer_needed') return 'closed';
  if (!item.due_date) return 'no_date';
  const days = daysBetween(today, item.due_date);
  if (days < 0) return 'overdue';
  return days <= 7 ? 'this_week' : 'later';
}

/** Non-empty groups in reading order. `today` is a YYYY-MM-DD date. */
export function groupRequests(items: EvidenceRequestView[], today: string): InboxGroup[] {
  const order: InboxGroup['key'][] = ['overdue', 'this_week', 'later', 'no_date', 'answered', 'closed'];
  const byDue = [...items].sort((a, b) => (a.due_date ?? '9999').localeCompare(b.due_date ?? '9999'));
  return order
    .map((key) => ({ key, label: LABELS[key], items: byDue.filter((i) => groupKey(i, today) === key) }))
    .filter((g) => g.items.length > 0);
} // prettier-ignore

export function todayIso(now: Date = new Date()): string {
  return now.toISOString().slice(0, 10);
}
