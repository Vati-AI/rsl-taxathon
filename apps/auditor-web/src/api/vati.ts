/**
 * Client for the Vati /api/v1 contract (integration/api/openapi.json): listCases,
 * getCase and getTaxpayer. Every request selects the tenant and carries a
 * correlation ID. Records not marked synthetic are refused: this prototype only
 * ever displays synthetic data.
 */
import { session } from '../session';
import { ApiError } from './errors';

export const CASE_STATUSES = [
  'RECEIVED',
  'VALIDATING',
  'INVESTIGATING',
  'AWAITING_EVIDENCE',
  'AWAITING_HUMAN',
  'RESOLVED',
  'FAILED_PROCESSING',
] as const;
export type CaseStatus = (typeof CASE_STATUSES)[number];
export type RefundLane = 'fast' | 'standard' | 'targeted';
export type TaxpayerType = 'individual' | 'company' | 'partnership' | 'trust' | 'other';

export interface Money {
  amount: string;
  currency: string;
}
export interface SyncState {
  state: 'pending' | 'synced' | 'failed';
  last_attempt_at: string | null;
  error_code: string | null;
}
export interface ServiceLink {
  service: string;
  external_type: string;
  external_id: string | null;
  sync: SyncState;
}
export interface SelectionReason {
  code: string;
  summary: string;
  source: string;
}
export interface TaxpayerSummary {
  taxpayer_id: string;
  display_name: string;
  taxpayer_type: TaxpayerType;
}
export interface Case {
  case_id: string;
  tenant_id: string;
  display_id: string;
  taxpayer_id: string;
  taxpayer: TaxpayerSummary;
  status: CaseStatus;
  version: number;
  refund_claim: Money | null;
  refund_lane: RefundLane | null;
  assigned_officer_id: string | null;
  selection_reasons: SelectionReason[];
  service_links: ServiceLink[];
  created_at: string;
  updated_at: string;
  synthetic: true;
}
export interface OpenCaseRef {
  case_id: string;
  display_id: string;
  status: CaseStatus;
}
export interface TaxpayerProfile {
  taxpayer_id: string;
  tenant_id: string;
  display_name: string;
  taxpayer_type: TaxpayerType;
  tin_masked: string | null;
  registered_on: string | null;
  sector: string | null;
  open_cases: OpenCaseRef[];
  service_links: ServiceLink[];
  synthetic: true;
}

type Fetch = typeof fetch;
type Json = Record<string, unknown>;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const DECIMAL = /^-?(0|[1-9][0-9]*)(\.[0-9]{1,4})?$/;
const MAX_QUEUE_PAGES = 10;

const DENIED = new Set(['unauthenticated', 'forbidden', 'tenant_scope_denied']);
const UNAVAILABLE = new Set([
  'upstream_unavailable',
  'upstream_timeout',
  'rate_limited',
  'idempotency_in_progress',
]);

export function isRecord(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function invalid(what: string): never {
  throw new ApiError('invalid', `The service returned ${what} that cannot be shown safely.`);
}

function newCorrelationId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  // RFC 4122 v4 fallback for environments without crypto.randomUUID.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = Math.floor(Math.random() * 16);
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

function errorFrom(status: number, body: unknown, fallbackReference: string): ApiError {
  const error = isRecord(body) && isRecord(body.error) ? body.error : null;
  const code = error && typeof error.code === 'string' ? error.code : null;
  const reference =
    error && typeof error.correlation_id === 'string' ? error.correlation_id : fallbackReference;
  const options = { status, code, reference };
  if ((code && DENIED.has(code)) || status === 401 || status === 403) {
    return new ApiError('denied', 'You do not have access to this record.', options);
  }
  if (code === 'not_found' || status === 404) {
    return new ApiError('not_found', 'The requested record was not found.', options);
  }
  if ((code && UNAVAILABLE.has(code)) || status === 429 || status >= 500) {
    return new ApiError('unavailable', `The case service is unavailable (${status}).`, options);
  }
  return new ApiError('invalid', 'The request was not accepted by the case service.', options);
}

export async function request(
  path: string,
  query: Record<string, string>,
  fetchImpl: Fetch,
  signal?: AbortSignal,
): Promise<Json> {
  const correlation = newCorrelationId();
  const params = new URLSearchParams(query).toString();
  let response: Response;
  try {
    response = await fetchImpl(params ? `${path}?${params}` : path, {
      headers: {
        Accept: 'application/json',
        'X-Vati-Tenant-ID': session.tenantId,
        'X-Correlation-ID': correlation,
      },
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError('unavailable', 'The case service could not be reached.', {
      reference: correlation,
    });
  }
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    if (response.ok) invalid('a response');
  }
  if (!response.ok) throw errorFrom(response.status, body, correlation);
  if (!isRecord(body)) invalid('a response');
  return body;
}

export function money(value: unknown): Money | null {
  if (value === null) return null;
  if (isRecord(value) && typeof value.amount === 'string' && DECIMAL.test(value.amount)) {
    if (typeof value.currency === 'string' && /^[A-Z]{3}$/.test(value.currency)) {
      return { amount: value.amount, currency: value.currency };
    }
  }
  return invalid('an amount');
}

function strings(value: Json, keys: string[]) {
  for (const key of keys)
    if (typeof value[key] !== 'string' || value[key] === '') invalid('a record');
}

export function serviceLinks(value: unknown): ServiceLink[] {
  if (!Array.isArray(value)) invalid('service links');
  return value.map((link) => {
    if (!isRecord(link) || !isRecord(link.sync)) invalid('a service link');
    strings(link, ['service', 'external_type']);
    const state = link.sync.state;
    if (state !== 'pending' && state !== 'synced' && state !== 'failed') invalid('a sync state');
    return link as unknown as ServiceLink;
  });
}

/** Validate the fields the UI relies on; reject non-synthetic or malformed records. */
export function parseCase(value: unknown): Case {
  if (!isRecord(value) || value.synthetic !== true) invalid('a case that is not synthetic');
  strings(value, ['case_id', 'tenant_id', 'display_id', 'taxpayer_id', 'created_at', 'updated_at']);
  if (!UUID.test(value.case_id as string)) invalid('a case');
  if (!CASE_STATUSES.includes(value.status as CaseStatus)) invalid('a case status');
  const taxpayer = value.taxpayer;
  if (!isRecord(taxpayer) || taxpayer.taxpayer_id !== value.taxpayer_id)
    invalid('taxpayer context');
  strings(taxpayer, ['display_name', 'taxpayer_type']);
  if (!Array.isArray(value.selection_reasons)) invalid('selection reasons');
  for (const reason of value.selection_reasons) {
    if (!isRecord(reason)) invalid('a selection reason');
    strings(reason, ['code', 'summary', 'source']);
  }
  return {
    ...(value as unknown as Case),
    refund_claim: money(value.refund_claim),
    service_links: serviceLinks(value.service_links),
  };
}

export function parseTaxpayer(value: unknown): TaxpayerProfile {
  if (!isRecord(value) || value.synthetic !== true) invalid('a taxpayer that is not synthetic');
  strings(value, ['taxpayer_id', 'tenant_id', 'display_name', 'taxpayer_type']);
  if (!Array.isArray(value.open_cases)) invalid('open cases');
  for (const ref of value.open_cases) {
    if (!isRecord(ref) || !CASE_STATUSES.includes(ref.status as CaseStatus))
      invalid('a case reference');
    strings(ref, ['case_id', 'display_id']);
  }
  return {
    ...(value as unknown as TaxpayerProfile),
    service_links: serviceLinks(value.service_links),
  };
}

/** All cases assigned to the signed-in officer, following cursor pages. */
export async function listAssignedCases(fetchImpl: Fetch = fetch, signal?: AbortSignal) {
  const cases: Case[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < MAX_QUEUE_PAGES; page++) {
    const query: Record<string, string> = { assigned_to: 'me', limit: '100' };
    if (cursor) query.cursor = cursor;
    const body = await request('/api/v1/cases', query, fetchImpl, signal);
    if (!Array.isArray(body.data) || !isRecord(body.meta)) invalid('a case page');
    cases.push(...body.data.map(parseCase));
    if (body.meta.has_more !== true) return cases;
    cursor =
      typeof body.meta.next_cursor === 'string' ? body.meta.next_cursor : invalid('a cursor');
  }
  throw new ApiError('invalid', 'The case queue is larger than this view can show.');
}

/** A malformed ID in a link can never match a record; say "not found" without a request. */
export function requireId(id: string, what: string) {
  if (!UUID.test(id)) throw new ApiError('not_found', `No ${what} exists with this reference.`);
}

export async function getCase(caseId: string, fetchImpl: Fetch = fetch, signal?: AbortSignal) {
  requireId(caseId, 'case');
  const body = await request(`/api/v1/cases/${encodeURIComponent(caseId)}`, {}, fetchImpl, signal);
  return parseCase(body.data);
}

export async function getTaxpayer(
  taxpayerId: string,
  fetchImpl: Fetch = fetch,
  signal?: AbortSignal,
) {
  requireId(taxpayerId, 'taxpayer');
  const path = `/api/v1/taxpayers/${encodeURIComponent(taxpayerId)}`;
  const body = await request(path, {}, fetchImpl, signal);
  return parseTaxpayer(body.data);
}
