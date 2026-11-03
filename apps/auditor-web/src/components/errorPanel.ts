import type { ApiError } from '../api/fixtures';
import type { PanelKind } from './StatePanel.vue';

/** Map a client error to user-facing state text without leaking response content. */
export function errorPanel(error: ApiError): {
  kind: PanelKind;
  title: string;
  message: string;
  retryable: boolean;
} {
  switch (error.kind) {
    case 'denied':
      return {
        kind: 'denied',
        title: 'You do not have access',
        message:
          'Your role or tenant does not permit viewing this record. Ask a supervisor if you need access.',
        retryable: false,
      };
    case 'not_found':
      return {
        kind: 'not_found',
        title: 'Case not found',
        message: 'Check the case reference, or return to the case queue.',
        retryable: false,
      };
    case 'invalid':
      return {
        kind: 'error',
        title: 'Data could not be shown safely',
        message: `${error.message} Nothing has been displayed from this response.`,
        retryable: true,
      };
    default:
      return {
        kind: 'error',
        title: 'Case data is temporarily unavailable',
        message: `${error.message} Start the fixture server with "python3 scripts/dev.py serve", then try again.`,
        retryable: true,
      };
  }
}
