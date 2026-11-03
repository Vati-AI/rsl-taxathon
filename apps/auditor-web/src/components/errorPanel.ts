import type { ApiError } from '../api/errors';
import type { PanelKind } from './StatePanel.vue';

/** Map a client error to user-facing state text without leaking response content. */
export function errorPanel(
  error: ApiError,
  subject: 'case' | 'taxpayer' = 'case',
): {
  kind: PanelKind;
  title: string;
  message: string;
  retryable: boolean;
  reference: string | null;
} {
  const reference = error.reference;
  switch (error.kind) {
    case 'denied':
      return {
        kind: 'denied',
        title: 'You do not have access',
        message:
          'Your role or tenant does not permit viewing this record. Ask a supervisor if you need access.',
        retryable: false,
        reference,
      };
    case 'not_found':
      return {
        kind: 'not_found',
        title: subject === 'case' ? 'Case not found' : 'Taxpayer not found',
        message:
          subject === 'case'
            ? 'Check the case reference, or return to the case queue.'
            : 'The taxpayer has no case you can view. Return to the case queue.',
        retryable: false,
        reference,
      };
    case 'invalid':
      return {
        kind: 'error',
        title: 'Data could not be shown safely',
        message: `${error.message} Nothing has been displayed from this response.`,
        retryable: true,
        reference,
      };
    default:
      return {
        kind: 'error',
        title: 'Case data is temporarily unavailable',
        message: `${error.message} If you are developing locally, start the fixture server with "python3 scripts/dev.py serve", then try again.`,
        retryable: true,
        reference,
      };
  }
}
