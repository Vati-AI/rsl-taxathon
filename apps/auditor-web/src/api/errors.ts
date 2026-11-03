export type ApiErrorKind = 'denied' | 'not_found' | 'unavailable' | 'invalid';

/** A failed API call, reduced to what the UI may show. Never carries response content. */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  /** Contract error code, e.g. tenant_scope_denied, when the server sent one. */
  readonly code: string | null;
  /** Correlation ID to quote when reporting the problem. */
  readonly reference: string | null;

  constructor(
    kind: ApiErrorKind,
    message: string,
    options: { status?: number | null; code?: string | null; reference?: string | null } = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = options.status ?? null;
    this.code = options.code ?? null;
    this.reference = options.reference ?? null;
  }

  get retryable(): boolean {
    return this.kind === 'unavailable';
  }
}
