/**
 * Saved queue views (TAX-147): My cases, Awaiting evidence, Awaiting approval and Overdue.
 * Membership and counts come only from the case list the table shows (GET /api/v1/cases)
 * and its server-computed `attention` fields. A view that needs a field the server did
 * not send has an unknown count (undefined), never a guessed one.
 */
import type { Case } from '../api/vati';

export const QUEUE_VIEWS = ['all', 'awaiting_evidence', 'awaiting_approval', 'overdue'] as const;
export type QueueView = (typeof QUEUE_VIEWS)[number];

export const VIEW_INFO: Record<QueueView, { label: string; icon: string; description: string }> = {
  all: {
    label: 'My cases',
    icon: 'lucide-list-checks',
    description: 'Every case assigned to you.',
  },
  awaiting_evidence: {
    label: 'Awaiting evidence',
    icon: 'lucide-hourglass',
    description: 'Cases waiting for the taxpayer: an evidence request is outstanding or the case is in "Awaiting evidence".',
  },
  awaiting_approval: {
    label: 'Awaiting approval',
    icon: 'lucide-user-check',
    description: 'A proposed decision is waiting for a supervisor who did not propose it.',
  },
  overdue: {
    label: 'Overdue',
    icon: 'lucide-alarm-clock',
    description: 'An outstanding evidence request is past its due date.',
  },
}; // prettier-ignore

export function parseView(value: unknown): QueueView {
  return QUEUE_VIEWS.includes(value as QueueView) ? (value as QueueView) : 'all';
}

/** Whether `item` belongs in `view`, or null when the server did not say. */
export function inView(item: Case, view: QueueView): boolean | null {
  const a = item.attention;
  switch (view) {
    case 'all':
      return true;
    case 'awaiting_evidence':
      return item.status === 'AWAITING_EVIDENCE' || (a ? a.outstanding_evidence_requests > 0 : false);
    case 'awaiting_approval':
      return a ? a.decision_awaiting_approval : null;
    case 'overdue':
      return a ? a.overdue && item.status !== 'RESOLVED' : null;
  }
} // prettier-ignore

/** Count per view; undefined where any case lacks what the view needs. */
export function viewCounts(cases: Case[]): Record<QueueView, number | undefined> {
  const out = {} as Record<QueueView, number | undefined>;
  for (const view of QUEUE_VIEWS) {
    const members = cases.map((c) => inView(c, view));
    out[view] = members.includes(null) ? undefined : members.filter(Boolean).length;
  }
  return out;
}

/** The cases in `view`; an unknown membership leaves the case out. */
export function casesInView(cases: Case[], view: QueueView): Case[] {
  return cases.filter((c) => inView(c, view) === true);
}
