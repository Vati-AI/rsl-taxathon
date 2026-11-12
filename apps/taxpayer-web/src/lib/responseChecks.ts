/**
 * Guidance on an evidence response before it is sent (TAX-126). The server
 * (`vati_tax.responses.checks`) reads each uploaded document's extraction and
 * words the guidance; these helpers only choose what to show. Nothing here can
 * stop a response being sent.
 */
import type { CorrectionReminder, RequestedItem, ResponseCheck, ResponseReview } from './types';

/**
 * TAX-078 reminder topics that bear on each kind of requested document. A reminder
 * the server marked on a check is shown as well, whatever the document type.
 */
const TOPICS_BY_DOCUMENT: Record<string, readonly string[]> = {
  invoice: ['missing_invoices', 'invoice_totals'],
  bank_statement: ['payment_details'],
  contract: ['contract_documents'],
};

/** The reminders relevant to this request, in the order the server listed them. */
export function relevantReminders(
  items: readonly RequestedItem[],
  reminders: readonly CorrectionReminder[],
  review: ResponseReview | null = null,
): CorrectionReminder[] {
  const topics = new Set(items.flatMap((i) => TOPICS_BY_DOCUMENT[i.document_type] ?? []));
  const marked = new Set(
    (review?.items ?? []).flatMap((i) => i.checks.map((c) => c.reminder_id).filter(Boolean)),
  );
  return reminders.filter((r) => topics.has(r.topic) || marked.has(r.reminder_id));
}

/** Checks for one item, things to look at first. */
export function checksFor(review: ResponseReview | null, index: number): ResponseCheck[] {
  const item = review?.items.find((i) => i.index === index);
  if (!item) return [];
  return [...item.checks].sort(
    (a, b) => Number(b.level === 'attention') - Number(a.level === 'attention'),
  );
}

/** Whether any document is still being read, so checking again later can say more. */
export function stillReading(review: ResponseReview | null): boolean {
  return (review?.items ?? []).some((i) =>
    i.checks.some((c) => c.code === 'document_still_being_read'),
  );
}

export function attentionCount(review: ResponseReview | null): number {
  return (review?.items ?? []).reduce((n, i) => n + i.attention, 0);
}

/** A stable key for the documents being checked: a new upload means a new review. */
export function draftKey(answers: { item_index: number; evidence_ids: string[] }[]): string {
  return JSON.stringify(answers.map((a) => [a.item_index, [...a.evidence_ids].sort()]));
}
