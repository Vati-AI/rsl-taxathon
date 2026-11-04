/**
 * VAT refund claim form rules (TAX-043). They mirror the server checks in
 * `vati_tax/portal/claims.py` so the taxpayer gets guidance before sending; the
 * server re-checks everything and its answer wins.
 */

export const CLAIM_REASONS = [
  {
    value: 'excess_input_vat',
    label: 'More input VAT than output VAT',
    hint: 'VAT you paid on purchases is more than VAT you charged on sales.',
  },
  {
    value: 'zero_rated_supplies',
    label: 'Zero-rated supplies',
    hint: 'For example exports, which are taxed at 0%.',
  },
  { value: 'other', label: 'Another reason', hint: 'Explain the reason below.' },
] as const;

export type ClaimReason = (typeof CLAIM_REASONS)[number]['value'];

export const MAX_EXPLANATION = 2000;

export interface ClaimDraft {
  period: string;
  amount: string;
  reason: string;
  explanation: string;
  contactEmail: string;
  declaration: boolean;
}

export type ClaimField = keyof ClaimDraft | 'documents';
export type ClaimErrors = Partial<Record<ClaimField, string>>;

export function emptyClaim(): ClaimDraft {
  return {
    period: '',
    amount: '',
    reason: '',
    explanation: '',
    contactEmail: '',
    declaration: false,
  };
}

/** Normalise a typed amount ("12 500,00" is rejected; "12,500.5" becomes "12500.50"). */
export function normaliseAmount(raw: string): string | null {
  const compact = raw.trim().replace(/,(?=\d{3}(\D|$))/g, '');
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(compact);
  if (!match) return null;
  return `${match[1].replace(/^0+(?=\d)/, '')}.${(match[2] ?? '').padEnd(2, '0')}`;
}

export function reasonLabel(value: string): string {
  return CLAIM_REASONS.find((r) => r.value === value)?.label ?? 'Not chosen';
}

/**
 * Step 1 checks. Messages say what to do; the caller keeps the entered values
 * so the taxpayer only fixes the flagged fields.
 */
export function validateDetails(draft: ClaimDraft, today: Date = new Date()): ClaimErrors {
  const errors: ClaimErrors = {};
  const period = /^(\d{4})-(\d{2})$/.exec(draft.period.trim());
  if (draft.period.trim() === '') {
    errors.period = 'Enter the tax period, for example 2026-09.';
  } else if (!period || Number(period[2]) < 1 || Number(period[2]) > 12) {
    errors.period = 'Enter the tax period as a month, for example 2026-09.';
  } else if (Number(period[1]) < 2000) {
    errors.period = 'Enter a tax period from 2000 onwards.';
  } else {
    const current = today.getUTCFullYear() * 12 + today.getUTCMonth();
    if (Number(period[1]) * 12 + Number(period[2]) - 1 > current) {
      errors.period = 'The tax period cannot be in the future.';
    }
  }

  const amount = normaliseAmount(draft.amount);
  if (draft.amount.trim() === '') {
    errors.amount = 'Enter the amount you are claiming in maloti.';
  } else if (amount === null) {
    errors.amount = 'Enter numbers only, with up to two decimal places, for example 15000.00.';
  } else if (/^0+\.00$/.test(amount)) {
    errors.amount = 'The amount claimed must be more than 0.00.';
  } else if (amount.split('.')[0].length > 12) {
    errors.amount = 'The amount is too large. Check the figure.';
  }

  if (!CLAIM_REASONS.some((r) => r.value === draft.reason)) {
    errors.reason = 'Choose the reason for the claim.';
  }
  const explanation = draft.explanation.trim();
  if (draft.reason === 'other' && explanation === '') {
    errors.explanation = 'Explain why you are claiming a refund.';
  } else if (explanation.length > MAX_EXPLANATION) {
    errors.explanation = `Keep the explanation under ${MAX_EXPLANATION} characters.`;
  }

  const email = draft.contactEmail.trim();
  if (email !== '' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    errors.contactEmail = 'Enter an email address like name@example.com, or leave it blank.';
  }
  return errors;
}

/** Server field names (vati_tax.portal.claims) mapped to the form's fields. */
const SERVER_FIELDS: Record<string, ClaimField> = {
  period: 'period',
  claimed_amount: 'amount',
  claim_reason: 'reason',
  explanation: 'explanation',
  contact_email: 'contactEmail',
  evidence_ids: 'documents',
  declaration: 'declaration',
};

/** Place server field errors on the form; unknown fields are returned separately. */
export function mapServerErrors(fieldErrors: { field: string; message: string }[]): {
  errors: ClaimErrors;
  other: string[];
} {
  const errors: ClaimErrors = {};
  const other: string[] = [];
  for (const item of fieldErrors) {
    const field = SERVER_FIELDS[item.field];
    if (field) errors[field] ??= item.message;
    else other.push(item.message);
  }
  return { errors, other };
}

/** Which step holds a field, so a server error can send the taxpayer back to it. */
export function stepForField(field: ClaimField): 'details' | 'documents' | 'review' {
  if (field === 'documents') return 'documents';
  if (field === 'declaration') return 'review';
  return 'details';
}

/** A random idempotency key in the server's accepted alphabet (16-255 of [A-Za-z0-9:._-]). */
export function newIdempotencyKey(prefix: string): string {
  const random =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
          b.toString(16).padStart(2, '0'),
        ).join('');
  return `${prefix}-${random}`;
}
