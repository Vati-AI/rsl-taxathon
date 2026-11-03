export const TAX_TYPES = ['VAT return', 'VAT refund claim', 'PAYE return'] as const;

export interface SubmissionDraft {
  taxType: string;
  period: string;
  amount: string;
  contactEmail: string;
  declaration: boolean;
}

export type DraftErrors = Partial<Record<keyof SubmissionDraft, string>>;

export function emptyDraft(): SubmissionDraft {
  return { taxType: '', period: '', amount: '', contactEmail: '', declaration: false };
}

/** Normalise a typed amount ("12 500,00" is rejected; "12,500.5" becomes "12500.50"). */
export function normaliseAmount(raw: string): string | null {
  const compact = raw.trim().replace(/,(?=\d{3}(\D|$))/g, '');
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(compact);
  if (!match) return null;
  return `${match[1].replace(/^0+(?=\d)/, '')}.${(match[2] ?? '').padEnd(2, '0')}`;
}

/**
 * Validate a draft. Messages say what to do, and the caller keeps the entered
 * values so the taxpayer only fixes the flagged fields.
 */
export function validateDraft(draft: SubmissionDraft, today: Date = new Date()): DraftErrors {
  const errors: DraftErrors = {};
  if (!TAX_TYPES.includes(draft.taxType as (typeof TAX_TYPES)[number])) {
    errors.taxType = 'Choose the type of return or claim.';
  }
  const period = /^(\d{4})-(\d{2})$/.exec(draft.period);
  if (!period || Number(period[2]) < 1 || Number(period[2]) > 12) {
    errors.period = 'Enter the tax period as a month, for example 2026-09.';
  } else {
    const current = today.getUTCFullYear() * 12 + today.getUTCMonth();
    if (Number(period[1]) * 12 + Number(period[2]) - 1 > current) {
      errors.period = 'The tax period cannot be in the future.';
    }
  }
  if (draft.amount.trim() === '') {
    errors.amount = 'Enter the amount in maloti. Enter 0.00 if nothing is due.';
  } else if (normaliseAmount(draft.amount) === null) {
    errors.amount = 'Enter numbers only, with up to two decimal places, for example 15000.00.';
  }
  if (draft.contactEmail.trim() !== '' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(draft.contactEmail)) {
    errors.contactEmail = 'Enter an email address like name@example.com, or leave it blank.';
  }
  if (!draft.declaration) {
    errors.declaration = 'Confirm the declaration to continue.';
  }
  return errors;
}
