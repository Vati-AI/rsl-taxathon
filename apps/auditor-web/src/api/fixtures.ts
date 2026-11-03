/**
 * Client for the read-only fixture contract served by `scripts/dev.py serve`
 * (docs/API_CONTRACTS.md). Every response must declare `mode: fixture` and
 * `synthetic: true`; anything else is rejected rather than displayed, so live
 * or malformed data can never be mistaken for synthetic fixtures.
 */

export interface FixtureCase {
  id: string;
  name: string;
  scenario: string;
  currency: string;
  refund_claim: string | null;
  supported_refund: string | null;
  expected: string;
  synthetic: true;
}

export type ApiErrorKind = 'denied' | 'not_found' | 'unavailable' | 'invalid';

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;

  constructor(kind: ApiErrorKind, message: string, status: number | null = null) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
  }

  get retryable(): boolean {
    return this.kind === 'unavailable';
  }
}

type Fetch = typeof fetch;

const DECIMAL = /^-?\d+(\.\d+)?$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function optionalDecimal(value: unknown): string | null | undefined {
  if (value === null || value === undefined || value === '') return null;
  return typeof value === 'string' && DECIMAL.test(value) ? value : undefined;
}

function parseCase(value: unknown): FixtureCase {
  if (!isRecord(value) || value.synthetic !== true) {
    throw new ApiError('invalid', 'A case record is not marked as synthetic fixture data.');
  }
  const text = ['id', 'name', 'scenario', 'currency', 'expected'] as const;
  for (const key of text) {
    if (typeof value[key] !== 'string' || value[key] === '') {
      throw new ApiError('invalid', `A fixture case is missing its ${key}.`);
    }
  }
  const refundClaim = optionalDecimal(value.refund_claim);
  const supportedRefund = optionalDecimal(value.supported_refund);
  if (refundClaim === undefined || supportedRefund === undefined) {
    throw new ApiError('invalid', 'A fixture amount is not a decimal string.');
  }
  return {
    id: value.id as string,
    name: value.name as string,
    scenario: value.scenario as string,
    currency: value.currency as string,
    expected: value.expected as string,
    refund_claim: refundClaim,
    supported_refund: supportedRefund,
    synthetic: true,
  };
}

async function request(path: string, fetchImpl: Fetch, signal?: AbortSignal) {
  let response: Response;
  try {
    response = await fetchImpl(path, { headers: { Accept: 'application/json' }, signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError('unavailable', 'The fixture service could not be reached.');
  }
  if (response.status === 401 || response.status === 403) {
    throw new ApiError('denied', 'You do not have access to this record.', response.status);
  }
  if (response.status === 404) {
    throw new ApiError('not_found', 'No case exists with this reference.', 404);
  }
  if (!response.ok) {
    throw new ApiError(
      'unavailable',
      `The fixture service returned an error (${response.status}).`,
      response.status,
    );
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ApiError('invalid', 'The fixture service returned a response that is not JSON.');
  }
  if (!isRecord(body) || body.mode !== 'fixture' || body.synthetic !== true) {
    throw new ApiError('invalid', 'The response is not explicitly marked as synthetic fixtures.');
  }
  return body;
}

export async function listCases(fetchImpl: Fetch = fetch, signal?: AbortSignal) {
  const body = await request('/api/cases', fetchImpl, signal);
  if (!Array.isArray(body.items)) {
    throw new ApiError('invalid', 'The case list response has no items.');
  }
  return body.items.map(parseCase);
}

export async function getCase(id: string, fetchImpl: Fetch = fetch, signal?: AbortSignal) {
  const body = await request('/api/cases/' + encodeURIComponent(id), fetchImpl, signal);
  return parseCase(body.item);
}
