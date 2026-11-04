export type ApiErrorKind =
  | 'denied'
  | 'not_found'
  | 'unavailable'
  | 'invalid'
  /** 409: the case or decision changed since it was loaded (stale or competing action). */
  | 'conflict'
  /** 422: a well-formed request that a review rule refuses. */
  | 'rejected';

/** A failed API call, reduced to what the UI may show. Never carries response content. */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  /** Contract error code, e.g. tenant_scope_denied, when the server sent one. */
  readonly code: string | null;
  /** Correlation ID to quote when reporting the problem. */
  readonly reference: string | null;
  /** First field_errors code, e.g. stale_case_version or decision_pending. */
  readonly reason: string | null;
  /** current_version from a version_conflict, when the server sent it. */
  readonly currentVersion: number | null;

  constructor(
    kind: ApiErrorKind,
    message: string,
    options: {
      status?: number | null;
      code?: string | null;
      reference?: string | null;
      reason?: string | null;
      currentVersion?: number | null;
    } = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = options.status ?? null;
    this.code = options.code ?? null;
    this.reference = options.reference ?? null;
    this.reason = options.reason ?? null;
    this.currentVersion = options.currentVersion ?? null;
  }

  get retryable(): boolean {
    return this.kind === 'unavailable';
  }
}
