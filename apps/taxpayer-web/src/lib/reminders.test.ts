import { describe, expect, it } from 'vitest';
import { reminderDateLabel, reminderForPrompt } from './reminders';
import type { CorrectionReminder, ReviewPrompt } from './types';

const REMINDER: CorrectionReminder = {
  reminder_id: '6f0c1a52-0000-4000-8000-000000000078',
  topic: 'payment_details',
  title: 'Add what you paid for each invoice',
  explanation: 'Your March claim did not say how much you paid for two invoices.',
  prompt_codes: ['payment_evidence_missing', 'payment_total_differs_from_invoice'],
  recorded_on: '2026-11-04',
  updated_on: null,
};

function prompt(overrides: Partial<ReviewPrompt> = {}): ReviewPrompt {
  return { code: 'payment_evidence_missing', invoice: 0, message: 'Invoice 1: …', ...overrides };
}

describe('reminderForPrompt', () => {
  it('returns the reminder the server marked on the prompt', () => {
    expect(reminderForPrompt(prompt({ reminder_id: REMINDER.reminder_id }), [REMINDER])).toBe(
      REMINDER,
    );
  });

  it('does not guess from the code when the server marked nothing', () => {
    expect(reminderForPrompt(prompt(), [REMINDER])).toBeNull();
    expect(reminderForPrompt(prompt({ reminder_id: null }), [REMINDER])).toBeNull();
  });

  it('ignores a marked reminder that is not in the taxpayer list', () => {
    expect(reminderForPrompt(prompt({ reminder_id: 'unknown' }), [REMINDER])).toBeNull();
    expect(reminderForPrompt(prompt({ reminder_id: REMINDER.reminder_id }), [])).toBeNull();
  });
});

describe('reminderDateLabel', () => {
  it('shows when the reminder was noted', () => {
    expect(reminderDateLabel(REMINDER)).toBe('Noted 4 Nov 2026');
  });

  it('shows when staff last corrected it', () => {
    expect(reminderDateLabel({ ...REMINDER, updated_on: '2026-11-05' })).toBe('Updated 5 Nov 2026');
  });
});
