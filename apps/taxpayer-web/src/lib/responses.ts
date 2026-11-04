/**
 * Evidence request response rules for the portal (TAX-045). They mirror
 * `vati_tax/responses/rules.py` so the taxpayer gets guidance before sending;
 * the server re-checks everything and its answer wins.
 */
import type { EvidenceRequestStatus } from './types';

type BadgeTheme = 'gray' | 'blue' | 'green' | 'amber' | 'red';

const STATUS: Record<EvidenceRequestStatus, { label: string; theme: BadgeTheme }> = {
  awaiting_response: { label: 'Response needed', theme: 'amber' },
  response_sent: { label: 'Response sent', theme: 'blue' },
  no_longer_needed: { label: 'No longer needed', theme: 'gray' },
};

/** Taxpayer-facing status; anything unexpected is labelled, never guessed. */
export function requestStatusDisplay(status: string): { label: string; theme: BadgeTheme } {
  return STATUS[status as EvidenceRequestStatus] ?? { label: 'Status unavailable', theme: 'gray' };
}

const TYPE_LABEL: Record<string, string> = {
  invoice: 'Tax invoice',
  contract: 'Contract',
  payslip: 'Payslip',
  import_declaration: 'Import declaration',
  bank_statement: 'Bank statement',
  financial_statement: 'Financial statement',
  other: 'Other document',
};

export function requestedTypeLabel(value: string): string {
  return TYPE_LABEL[value] ?? 'Document';
}

export const MAX_REASON = 1000;
export const MAX_MESSAGE = 2000;

export interface ItemState {
  uploaded: number;
  pending: boolean;
  failed: boolean;
  cannotProvide: boolean;
  reason: string;
}

export type ItemErrors = Record<number, string>;

/** Per-item problems before sending; an empty object means every item is answered. */
export function validateAnswers(items: Record<number, ItemState>): ItemErrors {
  const errors: ItemErrors = {};
  for (const [key, item] of Object.entries(items)) {
    const index = Number(key);
    const reason = item.reason.trim();
    if (item.pending) {
      errors[index] = 'Wait for your documents to finish uploading.';
    } else if (item.failed) {
      errors[index] = 'Retry or remove the documents that could not be uploaded.';
    } else if (item.cannotProvide) {
      if (reason === '') errors[index] = 'Explain why you cannot provide this document.';
      else if (reason.length > MAX_REASON) {
        errors[index] = `Keep the reason under ${MAX_REASON} characters.`;
      }
    } else if (item.uploaded === 0) {
      errors[index] = 'Upload a document for this item, or tell us you cannot provide it.';
    }
  }
  return errors;
}

/** Server field errors (`items.N`, `evidence_ids`, `message`) placed on the form. */
export function mapResponseErrors(fieldErrors: { field: string; message: string }[]): {
  items: ItemErrors;
  message?: string;
  general: string[];
} {
  const items: ItemErrors = {};
  const general: string[] = [];
  let message: string | undefined;
  for (const error of fieldErrors) {
    const match = /^items\.(\d+)$/.exec(error.field);
    if (match) items[Number(match[1])] ??= error.message;
    else if (error.field === 'message') message ??= error.message;
    else general.push(error.message);
  }
  return { items, message, general };
}
