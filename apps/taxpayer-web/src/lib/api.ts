import { call } from './frappe-call';
import type {
  CorrectionReminder,
  EvidenceRequestView,
  EvidenceView,
  DataEnvelope,
  LiveEnvelope,
  PrefilingCheck,
  PresubmissionReview,
  PortalSession,
  RefundClaimResult,
  ResponseReview,
  Submission,
} from './types';

/**
 * - `invalid`: the server refused the input (400/422); see `code`/`fieldErrors`.
 * - `unavailable`: no usable answer (offline, 5xx, unexpected shape). For a
 *   send this means the outcome is unknown, so retrying with the same key is safe.
 */
export type PortalErrorKind =
  'unauthenticated' | 'denied' | 'not_found' | 'unavailable' | 'invalid';

export interface FieldError {
  field: string;
  code: string;
  message: string;
}

export class PortalError extends Error {
  constructor(
    readonly kind: PortalErrorKind,
    message: string,
    /** API error code, e.g. `invalid_domain_input` or `idempotency_conflict`. */
    readonly code: string | null = null,
    /** Field-level reason, e.g. `too_large` for a rejected upload. */
    readonly reason: string | null = null,
    readonly fieldErrors: FieldError[] = [],
  ) {
    super(message);
    this.name = 'PortalError';
  }
}

interface FrappeLikeError {
  exc_type?: string;
  status?: number;
}

/** Map a Frappe/transport failure to what the UI should show. Never treated as success. */
export function classifyError(error: unknown, guestCheck = false): PortalError {
  const { exc_type: excType, status } = (error ?? {}) as FrappeLikeError;
  if (excType === 'DoesNotExistError' || status === 404) {
    return new PortalError('not_found', 'We could not find that record.');
  }
  if (
    excType === 'PermissionError' ||
    excType === 'AuthenticationError' ||
    status === 401 ||
    status === 403
  ) {
    return guestCheck
      ? new PortalError('unauthenticated', 'Your session has ended. Log in again to continue.')
      : new PortalError('denied', 'You do not have access to this information.');
  }
  return new PortalError('unavailable', 'The portal could not be reached. Try again in a moment.');
}

function assertMode(payload: { mode?: string; synthetic?: boolean }): void {
  // The source must be explicit: live data, or fixture data labelled synthetic.
  // Anything else means the contract changed.
  const live = payload.mode === 'live';
  const fixture = payload.mode === 'fixture' && payload.synthetic === true;
  if (!live && !fixture) {
    throw new PortalError('unavailable', 'The portal returned data in an unexpected format.');
  }
}

async function request<T extends { mode?: string; synthetic?: boolean }>(
  method: string,
  args: Record<string, unknown> = {},
  guestCheck = false,
): Promise<T> {
  let payload: T;
  try {
    payload = await call<T>(method, args);
  } catch (error) {
    throw error instanceof PortalError ? error : classifyError(error, guestCheck);
  }
  assertMode(payload);
  return payload;
}

export function fetchSession(): Promise<PortalSession> {
  return request<PortalSession>('vati_tax.portal.api.get_session', {}, true);
}

export async function fetchSubmissions(): Promise<Submission[]> {
  return (await request<DataEnvelope<Submission[]>>('vati_tax.portal.api.get_submissions')).data;
}

export async function fetchSubmission(id: string): Promise<Submission> {
  return (
    await request<DataEnvelope<Submission>>('vati_tax.portal.api.get_submission', {
      submission_id: id,
    })
  ).data;
}

// --- live calls (TAX-043) ---------------------------------------------------------

declare global {
  interface Window {
    csrf_token?: string;
  }
}

interface FrappeErrorBody {
  exc_type?: string;
  _server_messages?: string;
  error?: { code?: string; message?: string; field_errors?: FieldError[] };
}

/** `code:reason` from the title of a Frappe server message (TAX-032 upload errors). */
function serverMessageTitle(raw: string | undefined): string | null {
  if (!raw) return null;
  try {
    for (const item of JSON.parse(raw) as string[]) {
      const title = (JSON.parse(item) as { title?: unknown }).title;
      if (typeof title === 'string' && title.includes(':')) return title;
    }
  } catch {
    // Malformed messages are treated as absent.
  }
  return null;
}

/** Map a failed live call to what the UI should show. Messages never echo server text. */
export function errorFromResponse(status: number, body: FrappeErrorBody | null): PortalError {
  if (status === 401) {
    return new PortalError('unauthenticated', 'Your session has ended. Log in again to continue.');
  }
  if (status === 403) {
    return new PortalError('denied', 'You do not have access to this information.');
  }
  if (status === 404 || body?.exc_type === 'DoesNotExistError') {
    return new PortalError('not_found', 'We could not find that record.');
  }
  if (status === 400 || status === 422) {
    const portal = body?.error;
    if (portal?.code) {
      return new PortalError(
        'invalid',
        'Some answers need attention.',
        portal.code,
        null,
        Array.isArray(portal.field_errors) ? portal.field_errors : [],
      );
    }
    const title = serverMessageTitle(body?._server_messages);
    const [code, reason] = title ? title.split(':', 2) : [null, null];
    return new PortalError('invalid', 'The request was not accepted.', code, reason);
  }
  return new PortalError('unavailable', 'The portal could not be reached. Try again in a moment.');
}

/**
 * POST to a whitelisted Frappe method. Used for the calls frappe-ui's `call`
 * cannot make: a multipart upload with the tenant header, and responses whose
 * structured error body the form needs.
 */
async function post<T>(
  method: string,
  body: FormData | Record<string, unknown>,
  tenantId?: string,
): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  const token = window.csrf_token;
  // naming-allowed: HTTP header name required by the server, never shown.
  if (token && token !== '{{ csrf_token }}') headers['X-Frappe-CSRF-Token'] = token;
  if (tenantId) headers['X-Vati-Tenant-ID'] = tenantId;
  const isForm = body instanceof FormData;
  if (!isForm) headers['Content-Type'] = 'application/json';

  let response: Response;
  try {
    response = await fetch(`/api/method/${method}`, {
      method: 'POST',
      credentials: 'same-origin',
      headers,
      body: isForm ? body : JSON.stringify(body),
    });
  } catch {
    throw new PortalError('unavailable', 'The portal could not be reached. Try again in a moment.');
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) throw errorFromResponse(response.status, payload as FrappeErrorBody | null);
  const message = (payload as { message?: unknown } | null)?.message;
  if (message === undefined || message === null) {
    throw new PortalError('unavailable', 'The portal returned data in an unexpected format.');
  }
  return message as T;
}

/** Upload one supporting document as private evidence for the caller's own taxpayer. */
export async function uploadEvidence(
  file: File,
  documentType: string,
  idempotencyKey: string,
  tenantId: string,
): Promise<EvidenceView> {
  const form = new FormData();
  form.append('file', file, file.name);
  form.append('document_type', documentType);
  form.append('idempotency_key', idempotencyKey);
  const view = await post<EvidenceView>('vati_tax.evidence.api.upload_evidence', form, tenantId);
  if (typeof view?.evidence_id !== 'string') {
    throw new PortalError('unavailable', 'The portal returned data in an unexpected format.');
  }
  return view;
}

export interface RefundClaimPayload {
  idempotency_key: string;
  period: string;
  claimed_amount: string;
  claim_reason: string;
  explanation: string | null;
  contact_email: string | null;
  evidence_ids: string[];
  declaration: boolean;
  tenant_id: string;
  /** TAX-044: the period's figures and the purchase-invoice schedule. */
  period_output_vat: string;
  period_input_vat: string;
  invoices: Record<string, string | null>[];
}

/** Send the claim. Safe to repeat with the same key: the server replays, never duplicates. */
export async function submitRefundClaim(payload: RefundClaimPayload): Promise<RefundClaimResult> {
  const result = await post<RefundClaimResult>(
    'vati_tax.portal.api.submit_refund_claim',
    { ...payload },
    payload.tenant_id,
  );
  if (result?.mode !== 'live' || typeof result.data?.reference !== 'string') {
    throw new PortalError('unavailable', 'The portal returned data in an unexpected format.');
  }
  return result;
}

// --- evidence requests (TAX-045) ---------------------------------------------------

/** A live read through frappe-ui's call; anything but `mode: live` is refused. */
async function liveRequest<T>(method: string, args: Record<string, unknown> = {}): Promise<T> {
  let payload: LiveEnvelope<T>;
  try {
    payload = await call<LiveEnvelope<T>>(method, args);
  } catch (error) {
    throw error instanceof PortalError ? error : classifyError(error);
  }
  if (payload?.mode !== 'live' || payload.data === undefined) {
    throw new PortalError('unavailable', 'The portal returned data in an unexpected format.');
  }
  return payload.data;
}

export function fetchEvidenceRequests(): Promise<EvidenceRequestView[]> {
  return liveRequest<EvidenceRequestView[]>('vati_tax.portal.api.get_evidence_requests');
}

export function fetchEvidenceRequest(id: string): Promise<EvidenceRequestView> {
  return liveRequest<EvidenceRequestView>('vati_tax.portal.api.get_evidence_request', {
    request_id: id,
  });
}

export interface ResponseAnswer {
  item_index: number;
  evidence_ids: string[];
  unavailable_reason: string | null;
}

/** Send the answers. Safe to repeat with the same key: the server replays, never duplicates. */
export async function respondToEvidenceRequest(payload: {
  request_id: string;
  items: ResponseAnswer[];
  message: string | null;
  idempotency_key: string;
}): Promise<LiveEnvelope<EvidenceRequestView>> {
  const result = await post<LiveEnvelope<EvidenceRequestView>>(
    'vati_tax.portal.api.respond_to_evidence_request',
    { ...payload },
  );
  if (result?.mode !== 'live' || typeof result.data?.request_id !== 'string') {
    throw new PortalError('unavailable', 'The portal returned data in an unexpected format.');
  }
  return result;
}

/** TAX-126: guidance on a draft response before it is sent; stores nothing, never blocks. */
export async function reviewEvidenceResponse(payload: {
  request_id: string;
  items: { item_index: number; evidence_ids: string[] }[];
}): Promise<ResponseReview> {
  const result = await post<LiveEnvelope<ResponseReview>>(
    'vati_tax.portal.api.review_evidence_response',
    { ...payload },
  );
  if (result?.mode !== 'live' || !Array.isArray(result.data?.items)) {
    throw new PortalError('unavailable', 'The portal returned data in an unexpected format.');
  }
  return result.data;
}

/** Check a draft claim before sending (TAX-044); stores nothing. */
export async function reviewRefundClaim(
  payload: Omit<RefundClaimPayload, 'idempotency_key' | 'declaration'>,
): Promise<PresubmissionReview> {
  const result = await post<LiveEnvelope<PresubmissionReview>>(
    'vati_tax.portal.api.review_refund_claim',
    { ...payload },
    payload.tenant_id,
  );
  if (result?.mode !== 'live' || !Array.isArray(result.data?.prompts)) {
    throw new PortalError('unavailable', 'The portal returned data in an unexpected format.');
  }
  return result.data;
}

/** TAX-078: the caller's own reminders from reviewed omissions in earlier claims. */
export async function fetchCorrectionReminders(): Promise<CorrectionReminder[]> {
  const data = await liveRequest<CorrectionReminder[]>(
    'vati_tax.portal.api.get_correction_reminders',
  );
  if (!Array.isArray(data)) {
    throw new PortalError('unavailable', 'The portal returned data in an unexpected format.');
  }
  return data;
}

export async function logout(): Promise<void> {
  // Frappe v16: POST /api/method/logout clears the session cookie.
  await call('logout');
}

/** TAX-129: check a VAT period against what RSL holds before filing; stores nothing. */
export async function checkBeforeFiling(
  period: string,
  tenantId?: string,
): Promise<PrefilingCheck> {
  const result = await post<LiveEnvelope<PrefilingCheck>>(
    'vati_tax.portal.api.check_before_filing',
    { period },
    tenantId,
  );
  if (result?.mode !== 'live' || !Array.isArray(result.data?.prompts)) {
    throw new PortalError('unavailable', 'The portal returned data in an unexpected format.');
  }
  return result.data;
}
