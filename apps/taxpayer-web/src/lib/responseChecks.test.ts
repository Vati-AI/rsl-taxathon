import { describe, expect, it } from 'vitest';
import {
  attentionCount,
  checksFor,
  draftKey,
  relevantReminders,
  stillReading,
} from './responseChecks';
import type { CorrectionReminder, RequestedItem, ResponseCheck, ResponseReview } from './types';

function reminder(topic: string, id = topic): CorrectionReminder {
  return { reminder_id: id, topic, title: topic, explanation: 'Synthetic reminder text.',
    prompt_codes: [], recorded_on: '2026-08-14', updated_on: null }; // prettier-ignore
}

function check(code: ResponseCheck['code'], level: ResponseCheck['level'] = 'info',
  reminder_id: string | null = null): ResponseCheck {
  return { code, level, message: code, reminder_id };
} // prettier-ignore

const ITEMS: RequestedItem[] = [
  { index: 0, document_type: 'invoice', description: 'Synthetic invoice' },
  { index: 1, document_type: 'bank_statement', description: 'Synthetic statement' },
];

function review(...items: ResponseReview['items']): ResponseReview {
  return { checks_version: 'tax126-response-checks-v1', items, blocks_sending: false };
}

describe('relevantReminders', () => {
  const all = [
    reminder('missing_invoices'),
    reminder('contract_documents'),
    reminder('payment_details'),
    reminder('period_figures'),
  ];

  it('keeps reminders about the kinds of documents requested', () => {
    expect(relevantReminders(ITEMS, all).map((r) => r.topic)).toEqual([
      'missing_invoices',
      'payment_details',
    ]);
    expect(relevantReminders([ITEMS[1]], all).map((r) => r.topic)).toEqual(['payment_details']);
  });

  it('adds a reminder the server marked on a check', () => {
    const marked = review({ index: 1, attention: 1,
      checks: [check('document_period_differs', 'attention', 'period_figures')] }); // prettier-ignore
    expect(relevantReminders([ITEMS[1]], all, marked).map((r) => r.topic)).toEqual([
      'payment_details',
      'period_figures',
    ]);
  });

  it('shows nothing for a first-time claimant', () => {
    expect(relevantReminders(ITEMS, [])).toEqual([]);
  });
});

describe('checks', () => {
  const r = review(
    { index: 0, attention: 1, checks: [check('document_partly_read'),
      check('document_amount_not_found', 'attention')] }, // prettier-ignore
    { index: 1, attention: 0, checks: [check('document_still_being_read')] },
  );

  it('lists things to look at first', () => {
    expect(checksFor(r, 0).map((c) => c.code)).toEqual([
      'document_amount_not_found',
      'document_partly_read',
    ]);
    expect(checksFor(r, 5)).toEqual([]);
    expect(checksFor(null, 0)).toEqual([]);
  });

  it('counts attention and notices documents still being read', () => {
    expect(attentionCount(r)).toBe(1);
    expect(attentionCount(null)).toBe(0);
    expect(stillReading(r)).toBe(true);
    expect(stillReading(review({ index: 0, attention: 0, checks: [] }))).toBe(false);
  });

  it('keys a draft by its documents, in any order', () => {
    expect(draftKey([{ item_index: 0, evidence_ids: ['b', 'a'] }])).toBe(
      draftKey([{ item_index: 0, evidence_ids: ['a', 'b'] }]),
    );
    expect(draftKey([{ item_index: 0, evidence_ids: ['a'] }])).not.toBe(
      draftKey([{ item_index: 1, evidence_ids: ['a'] }]),
    );
  });
});
