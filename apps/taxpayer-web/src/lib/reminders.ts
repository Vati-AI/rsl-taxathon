/**
 * Correction reminders (TAX-078): guidance from reviewed omissions in this
 * taxpayer's earlier claims. The server already limits them to the caller's
 * own active reminders; these helpers only present them.
 */
import { formatDate } from './format';
import type { CorrectionReminder, ReviewPrompt } from './types';

/** The reminder that covers a pre-submission prompt, if the server marked one. */
export function reminderForPrompt(
  prompt: ReviewPrompt,
  reminders: readonly CorrectionReminder[],
): CorrectionReminder | null {
  if (!prompt.reminder_id) return null;
  return reminders.find((r) => r.reminder_id === prompt.reminder_id) ?? null;
}

/** "Noted 4 Nov 2026", or "Updated …" once staff corrected the wording. */
export function reminderDateLabel(reminder: CorrectionReminder): string {
  return reminder.updated_on
    ? `Updated ${formatDate(reminder.updated_on)}`
    : `Noted ${formatDate(reminder.recorded_on)}`;
}
