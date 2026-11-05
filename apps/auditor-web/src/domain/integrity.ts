import type { IntegritySignalKind } from '../api/integrity';

/**
 * Review signals are prompts for a supervisor to look; never findings about
 * anyone. The wording names a pattern in the records, not a person or a motive.
 */
export const INTEGRITY_SIGNAL_TEXT: Record<IntegritySignalKind, { label: string; help: string }> = {
  repeated_overrides: {
    label: 'Repeated overrides',
    help: 'A person recorded three or more control overrides in this window.',
  },
  repeated_separation_of_duties_attempts: {
    label: 'Repeated refused self-approvals or overrides',
    help: 'A person was refused two or more times for approving or overriding their own work.',
  },
  decision_overturned: {
    label: 'Decisions changed on objection or appeal',
    help: 'A reviewed objection or appeal changed a decision. It may reflect new evidence.',
  },
  ledger_integrity: {
    label: 'Audit ledger check failed',
    help: 'The ledger hash chain does not verify. Some entries may have been changed or removed.',
  },
};

/** "5 (in 3 groups)" for a combined row; used as its accessible description too. */
export function combinedText(groups: number) {
  return `${groups} ${groups === 1 ? 'group' : 'groups'} combined to protect privacy`;
}
