/**
 * Role/session mocks for browser tests. Everything here is synthetic and runs
 * only inside the test browser; no Frappe site, account or password is used.
 *
 * Taxpayer portal: `vati_tax.portal.api.*` calls are answered in the browser
 * from the records and ownership rules the Frappe app uses
 * (vati_tax/portal/fixtures.py, called below so the two cannot drift).
 * Response shapes follow Frappe v16: success is `{ message }`, a refused call
 * is HTTP 403 with `exc_type`. Access follows TAX-029 (docs/PERMISSIONS.md):
 * only a user with a taxpayer membership is served, and only their own
 * taxpayer's submissions; another taxpayer's submission is "not found".
 *
 * Auditor workspace: the fixture API (scripts/fixture_api.py) has one signed-in
 * officer principal scoped to one synthetic tenant. Personas choose whether
 * requests stay in that tenant or select another one, which the API refuses.
 */
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import type { Page, Route } from '@playwright/test';

const root = path.resolve(__dirname, '../../..');

/**
 * The synthetic taxpayer user from docs/PERMISSIONS.md: a taxpayer membership in
 * the fixture tenant for the taxpayer that owns SUB-SYN-0001. Not a real account.
 */
export const SYNTHETIC_TAXPAYER = {
  user: 'taxpayer.a@example.test',
  full_name: 'Synthetic Taxpayer A',
  taxpayer_id: '80015243-dd34-4e97-b085-7356ffdd042f',
} as const;

/**
 * - `taxpayer`: signed in with the membership above.
 * - `guest`: not signed in; Frappe refuses every portal method with 403.
 * - `no-membership`: signed in, but not linked to a taxpayer; refused with 403.
 * - `revoked`: the portal loaded, then the membership was removed, so data calls
 *   are refused while the app still holds the earlier session.
 */
export type TaxpayerPersona = 'taxpayer' | 'guest' | 'no-membership' | 'revoked';
export type AuditorPersona = 'officer' | 'denied';

interface PortalSubmission {
  id: string;
  [key: string]: unknown;
}

interface PortalFixtures {
  /** The fixture tenant of SYNTHETIC_TAXPAYER's membership. */
  tenant_id: string;
  /** Every fixture ID, owned by anyone. */
  all_ids: string[];
  /** list_submissions(scope) for SYNTHETIC_TAXPAYER. */
  visible: PortalSubmission[];
}

function loadPortalFixtures(): PortalFixtures {
  const script = [
    'import importlib.util, json, sys',
    "spec = importlib.util.spec_from_file_location('fixtures', sys.argv[1])",
    'm = importlib.util.module_from_spec(spec)',
    'spec.loader.exec_module(m)',
    'scope = [(m.FIXTURE_TENANT_ID, sys.argv[2])]',
    "print(json.dumps({'tenant_id': m.FIXTURE_TENANT_ID,",
    "                  'all_ids': [s['id'] for s in m.SUBMISSIONS],",
    "                  'visible': m.list_submissions(scope)}))",
  ].join('\n');
  const file = path.join(root, 'backend/vati_tax/vati_tax/portal/fixtures.py');
  const out = execFileSync(
    process.env.PYTHON ?? 'python3',
    ['-c', script, file, SYNTHETIC_TAXPAYER.taxpayer_id],
    { encoding: 'utf8' },
  );
  return JSON.parse(out) as PortalFixtures;
}

const fixtures = loadPortalFixtures();

/** The tenant of the synthetic taxpayer's membership (X-Vati-Tenant-ID for uploads). */
export const SYNTHETIC_TENANT_ID = fixtures.tenant_id;

/** Submissions the synthetic taxpayer may see (their own). */
export const PORTAL_SUBMISSIONS = fixtures.visible;
/** A fixture submission owned by another taxpayer, which must stay concealed. */
export const OTHER_TAXPAYER_SUBMISSION_ID = fixtures.all_ids.find(
  (id) => !PORTAL_SUBMISSIONS.some((s) => s.id === id),
)!;

const FIXTURE = { mode: 'fixture', synthetic: true } as const;
const LIVE = { mode: 'live' } as const;

function ok(route: Route, message: unknown) {
  return route.fulfill({ status: 200, json: { message } });
}

function refused(route: Route, excType: string, status: number, text: string) {
  return route.fulfill({ status, json: { exc_type: excType, exception: `${excType}: ${text}` } });
}

/** A stand-in for Frappe's /login page, clearly labelled as a mock. */
const LOGIN_STUB = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Login (session mock)</title></head><body><main>
<h1>Login</h1><p>Session mock: Frappe's login page is not part of the browser harness.</p>
</main></body></html>`;

/** A claim the mock accepted, keyed by idempotency key (as the server's scoped key). */
interface StoredClaim {
  body: string;
  result: Record<string, unknown>;
}

export interface PortalMock {
  /**
   * TAX-092: where get_session/get_submissions/get_submission say their data comes
   * from. The server is live; 'fixture' checks the portal still labels fixture data.
   */
  dataMode: 'live' | 'fixture';
  /** Calls the portal made, in order, for assertions. */
  calls: string[];
  persona: TaxpayerPersona;
  /** Evidence IDs uploaded in this browser session. */
  evidence: string[];
  /** Claims stored by `submit_refund_claim`, by idempotency key. */
  claims: Map<string, StoredClaim>;
  /** Store the next claim, then drop the response: the client sees a lost connection. */
  dropNextSendResponse: boolean;
  /** Refuse the next claim as referencing evidence outside the caller's scope. */
  rejectEvidenceOnSend: boolean;
  /** TAX-044: how many pre-submission reviews the portal ran. */
  reviews: number;
  /** TAX-045: evidence requests by ID (taxpayer-facing views) and stored responses by key. */
  requests: Map<string, EvidenceRequestMock>;
  responses: Map<string, StoredClaim>;
  /** Store the next response, then drop the HTTP answer: the client sees a lost connection. */
  dropNextRespondResponse: boolean;
}

/** Field names the TAX-043 claim must carry (vati_tax.portal.api.submit_refund_claim). */
const CLAIM_FIELDS = [
  'period',
  'claimed_amount',
  'claim_reason',
  'explanation',
  'contact_email',
  'evidence_ids',
  // TAX-044
  'period_output_vat',
  'period_input_vat',
  'invoices',
] as const;

/** Whole cents of a decimal string (the portal sends normalised amounts). */
function toCents(value: unknown): number {
  const [whole, frac = ''] = String(value ?? '0').split('.');
  return Number(whole) * 100 + Number(frac.padEnd(2, '0').slice(0, 2));
}

function money(value: number): string {
  return `${Math.floor(value / 100)}.${String(value % 100).padStart(2, '0')}`;
}

interface MockInvoice {
  vat_amount: string;
  gross_amount: string;
  amount_paid: string | null;
  contract_evidence_id: string | null;
}

/**
 * The taxpayer view of the TAX-048 reconciliation for a draft claim, computed
 * with the same rules as vati_tax/portal/presubmission.py (whole cents). The
 * real server path is covered by vati_tax/portal/test_refund_claim_bench.py.
 */
function mockReview(body: Record<string, unknown>) {
  const rows = (body.invoices as MockInvoice[] | undefined) ?? [];
  const claimed = toCents(body.claimed_amount);
  const output = toCents(body.period_output_vat);
  const input = toCents(body.period_input_vat);
  const documented = rows.reduce((sum, r) => sum + toCents(r.vat_amount), 0);
  const explainedVat = rows
    .filter((r) => r.contract_evidence_id && r.amount_paid && toCents(r.amount_paid) === toCents(r.gross_amount))
    .reduce((sum, r) => sum + toCents(r.vat_amount), 0); // prettier-ignore
  const explained = Math.min(claimed, Math.max(0, explainedVat - output));
  const prompts: { code: string; invoice: number | null; message: string }[] = [];
  if (claimed !== Math.max(0, input - output)) {
    prompts.push({ code: 'return_arithmetic_mismatch', invoice: null,
      message: "The amount claimed should equal the period's input VAT minus its output VAT. Check these figures and the amount claimed." }); // prettier-ignore
  }
  const undocumented = claimed - Math.max(0, documented - output);
  if (undocumented > 0) {
    prompts.push({ code: 'refund_claim_exceeds_documented_input_vat', invoice: null,
      message: `Your claim is LSL ${Number(money(undocumented)).toLocaleString('en-US', { minimumFractionDigits: 2 })} more than the VAT on the invoices you listed. Add any missing invoices, or check the amount claimed.` }); // prettier-ignore
  }
  rows.forEach((r, i) => {
    if (toCents(r.vat_amount) === 0) return;
    if (r.amount_paid && toCents(r.amount_paid) !== toCents(r.gross_amount)) {
      prompts.push({ code: 'payment_total_differs_from_invoice', invoice: i,
        message: `Invoice ${i + 1}: the amount paid is different from the invoice total. Check the amount paid.` }); // prettier-ignore
    }
    if (!r.contract_evidence_id) {
      prompts.push({ code: 'contract_evidence_missing', invoice: i,
        message: `Invoice ${i + 1}: if you have a contract or purchase order for it, attach it. An officer may ask for it later.` }); // prettier-ignore
    }
    if (!r.amount_paid) {
      prompts.push({ code: 'payment_evidence_missing', invoice: i,
        message: `Invoice ${i + 1}: add the amount paid, if you have paid this invoice.` }); // prettier-ignore
    }
  });
  return {
    calculation_version: 'tax048-reconciliation-v1',
    figures: {
      claimed_refund: money(claimed),
      output_vat: money(output),
      documented_input_vat: money(documented),
      explained_amount: money(explained),
      unresolved_amount: money(claimed - explained),
    },
    prompts,
    complete: prompts.length === 0,
  };
}

let referenceCounter = 0;

/** A TAX-032 upload refusal as Frappe sends it: the `code:reason` is the message title. */
function uploadRefused(route: Route, reason: string) {
  const message = JSON.stringify({
    message: 'Upload refused (mock)',
    title: `invalid_domain_input:${reason}`,
  });
  return route.fulfill({
    status: 422,
    json: { exc_type: 'EvidenceRequestError', _server_messages: JSON.stringify([message]) },
  });
}

/** A portal API refusal in the docs/API_STANDARD.md shape. */
function claimRefused(
  route: Route,
  code: string,
  fieldErrors: { field: string; code: string; message: string }[] = [],
) {
  return route.fulfill({
    status: 422,
    json: {
      exc_type: 'PortalRequestError',
      error: {
        code,
        message: 'Check the highlighted answers.',
        retryable: false,
        correlation_id: 'mock-correlation-id',
        field_errors: fieldErrors,
      },
    },
  });
}

/**
 * Answer every portal API call for `persona`. Unhandled `/api/` requests are
 * refused in the browser, so the Vite proxy never forwards a test request to a
 * Frappe site a developer may have running on :58000.
 */
/** A taxpayer-facing evidence request view, as vati_tax.responses.rules.taxpayer_view returns. */
export interface EvidenceRequestMock {
  request_id: string;
  status: 'awaiting_response' | 'response_sent' | 'no_longer_needed';
  due_date: string;
  message_to_taxpayer: string;
  items: { index: number; document_type: string; description: string }[];
  response: unknown;
}

/** Synthetic requests owned by SYNTHETIC_TAXPAYER (TAX-045). */
export const OPEN_REQUEST_ID = '45000000-0000-4000-8000-000000000001';
export const ANSWERED_REQUEST_ID = '45000000-0000-4000-8000-000000000002';
export const WITHDRAWN_REQUEST_ID = '45000000-0000-4000-8000-000000000003';
/** Another taxpayer's request: the portal must report it as not found. */
export const OTHER_TAXPAYER_REQUEST_ID = '45000000-0000-4000-8000-0000000000ff';

function evidenceRequests(): Map<string, EvidenceRequestMock> {
  const items = [
    {
      index: 0,
      document_type: 'invoice',
      description: 'Tax invoices for the September 2026 purchases in your refund claim.',
    },
    {
      index: 1,
      document_type: 'bank_statement',
      description: 'Bank statement showing the payments for those invoices.',
    },
  ];
  const message =
    'Thank you for your VAT refund claim. Please send the documents below so we can check the input VAT.';
  return new Map([
    [OPEN_REQUEST_ID, { request_id: OPEN_REQUEST_ID, status: 'awaiting_response',
      due_date: '2026-11-30', message_to_taxpayer: message, items, response: null }],
    [ANSWERED_REQUEST_ID, { request_id: ANSWERED_REQUEST_ID, status: 'response_sent',
      due_date: '2026-10-31', message_to_taxpayer: message, items: items.slice(0, 1),
      response: { responded_at: '2026-10-20T09:30:00', message: 'Synthetic: invoices attached.',
        items: [{ index: 0, documents: [{ evidence_id: 'e-answered', name: 'invoice-0912.pdf' }],
          unavailable_reason: null }] } }],
    [WITHDRAWN_REQUEST_ID, { request_id: WITHDRAWN_REQUEST_ID, status: 'no_longer_needed',
      due_date: '2026-10-15', message_to_taxpayer: message, items: items.slice(1), response: null }],
  ] as [string, EvidenceRequestMock][]); // prettier-ignore
}

function meta(mock: PortalMock) {
  return mock.dataMode === 'fixture' ? FIXTURE : LIVE;
}

/**
 * The taxpayer's own submissions as portal.submissions.view shapes them: claims sent
 * in this test first (newest), then the synthetic records from portal/fixtures.py.
 * A sent claim is "under_review" until the TAX-039 bridge mirrors a Jube state.
 */
function ownSubmissions(mock: PortalMock): PortalSubmission[] {
  const sent = [...mock.claims.values()].reverse().map(({ result }) => {
    const d = result.data as Record<string, string>;
    return {
      id: d.submission_id,
      reference: d.reference,
      taxpayer_name: SYNTHETIC_TAXPAYER.full_name,
      tax_type: 'VAT refund claim',
      period: d.period,
      amount: d.claimed_amount,
      currency: d.currency,
      status: 'under_review',
      submitted_at: d.submitted_at,
      next_step: 'An officer is reviewing your submission. No action is needed from you yet.',
      steps: [
        { label: 'Submitted', state: 'complete', at: d.submitted_at },
        { label: 'Officer review', state: 'current', at: null },
        { label: 'Outcome issued', state: 'upcoming', at: null },
      ],
    };
  });
  return [...sent, ...PORTAL_SUBMISSIONS];
}

export async function mockTaxpayerSession(
  page: Page,
  persona: TaxpayerPersona,
  options: { dataMode?: 'live' | 'fixture' } = {},
): Promise<PortalMock> {
  const mock: PortalMock = {
    dataMode: options.dataMode ?? 'live',
    calls: [],
    persona,
    evidence: [],
    claims: new Map(),
    dropNextSendResponse: false,
    rejectEvidenceOnSend: false,
    reviews: 0,
    requests: evidenceRequests(),
    responses: new Map(),
    dropNextRespondResponse: false,
  };

  await page.route('**/login**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: LOGIN_STUB }),
  );

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const method = url.pathname.replace(/^\/api\/method\//, '');
    mock.calls.push(method);
    const p = mock.persona;

    if (method === 'logout') {
      mock.persona = 'guest';
      return ok(route, null);
    }
    if (method === 'vati_tax.portal.api.get_session') {
      if (p === 'guest') return refused(route, 'PermissionError', 403, 'Not permitted');
      if (p === 'no-membership')
        return refused(route, 'PermissionError', 403, 'This account is not linked to a taxpayer.');
      const { user, full_name, taxpayer_id } = SYNTHETIC_TAXPAYER;
      return ok(route, {
        ...meta(mock),
        user,
        full_name,
        taxpayers: [{ tenant_id: SYNTHETIC_TENANT_ID, taxpayer_id }],
      });
    }
    if (
      p !== 'taxpayer' &&
      (method.startsWith('vati_tax.portal.api.') || method.startsWith('vati_tax.evidence.api.'))
    ) {
      return refused(route, 'PermissionError', 403, 'This account is not linked to a taxpayer.');
    }
    if (method === 'vati_tax.evidence.api.upload_evidence') {
      // As upload_evidence (TAX-032): the tenant header selects, never grants.
      if (route.request().headers()['x-vati-tenant-id'] !== SYNTHETIC_TENANT_ID) {
        return refused(route, 'TenantScopeDenied', 403, 'Outside your permitted scope');
      }
      const body = route.request().postDataBuffer()?.toString('latin1') ?? '';
      const filename = /filename="([^"]*)"/.exec(body)?.[1] ?? '';
      // Content checks the browser cannot make: the mock keys them off the file name.
      if (filename.includes('renamed')) return uploadRefused(route, 'extension_mismatch');
      if (filename.includes('broken')) return uploadRefused(route, 'malformed_document');
      if (!/\.(pdf|png|jpe?g|csv|xlsx)$/i.test(filename)) {
        return uploadRefused(route, 'disallowed_type');
      }
      const evidenceId = randomUUID();
      mock.evidence.push(evidenceId);
      return ok(route, {
        evidence_id: evidenceId,
        document_type: /name="document_type"\r\n\r\n([^\r]*)/.exec(body)?.[1] ?? 'invoice',
        media_type: 'application/pdf',
        size_bytes: 2048,
        document_sha256: '0'.repeat(64),
        received_at: '2026-11-04T10:00:00Z',
        synthetic: true,
      });
    }
    if (method === 'vati_tax.portal.api.review_refund_claim') {
      const body = (route.request().postDataJSON() ?? {}) as Record<string, unknown>;
      const ids = (body.evidence_ids as string[] | undefined) ?? [];
      if (ids.some((id) => !mock.evidence.includes(id))) {
        return claimRefused(route, 'invalid_domain_input', [
          { field: 'evidence_ids', code: 'not_found',
            message: 'A document could not be found. Remove it and upload the file again.' },
        ]); // prettier-ignore
      }
      mock.reviews += 1;
      return ok(route, { mode: 'live', data: mockReview(body) });
    }
    if (method === 'vati_tax.portal.api.submit_refund_claim') {
      const body = (route.request().postDataJSON() ?? {}) as Record<string, unknown>;
      const key = String(body.idempotency_key ?? '');
      const fingerprint = JSON.stringify(CLAIM_FIELDS.map((f) => body[f] ?? null));
      const stored = mock.claims.get(key);
      if (stored) {
        // Same key: same answers replay the original result, different answers conflict.
        return stored.body === fingerprint
          ? ok(route, { ...stored.result, replayed: true })
          : claimRefused(route, 'idempotency_conflict');
      }
      const ids = (body.evidence_ids as string[] | undefined) ?? [];
      if (mock.rejectEvidenceOnSend || ids.some((id) => !mock.evidence.includes(id))) {
        mock.rejectEvidenceOnSend = false;
        return claimRefused(route, 'invalid_domain_input', [
          {
            field: 'evidence_ids',
            code: 'not_found',
            message: 'A document could not be found. Remove it and upload the file again.',
          },
        ]);
      }
      referenceCounter += 1;
      const result = {
        mode: 'live',
        replayed: false,
        data: {
          submission_id: randomUUID(),
          reference: `REF-2026-${String(referenceCounter).padStart(6, '0')}`,
          status: 'submitted',
          period: body.period,
          submitted_at: '2026-11-04T10:00:00',
          claimed_amount: body.claimed_amount,
          currency: 'LSL',
          claim_reason: body.claim_reason,
          evidence_count: ids.length,
        },
      };
      mock.claims.set(key, { body: fingerprint, result });
      if (mock.dropNextSendResponse) {
        mock.dropNextSendResponse = false;
        return route.abort('connectionreset');
      }
      return ok(route, result);
    }
    if (method === 'vati_tax.portal.api.get_evidence_requests') {
      return ok(route, { mode: 'live', data: [...mock.requests.values()] });
    }
    if (method === 'vati_tax.portal.api.get_evidence_request') {
      const body = (route.request().postDataJSON() ?? {}) as { request_id?: string };
      const item = mock.requests.get(String(body.request_id));
      // As the server: another taxpayer's request is concealed as not found.
      return item
        ? ok(route, { mode: 'live', data: item })
        : refused(route, 'DoesNotExistError', 404, 'Evidence request not found.');
    }
    if (method === 'vati_tax.portal.api.respond_to_evidence_request') {
      const body = (route.request().postDataJSON() ?? {}) as {
        request_id?: string;
        items?: { item_index: number; evidence_ids: string[]; unavailable_reason: string | null }[];
        message?: string | null;
        idempotency_key?: string;
      };
      const item = mock.requests.get(String(body.request_id));
      if (!item) return refused(route, 'DoesNotExistError', 404, 'Evidence request not found.');
      const key = String(body.idempotency_key ?? '');
      const fingerprint = JSON.stringify([body.request_id, body.items, body.message ?? null]);
      const stored = mock.responses.get(key);
      if (stored) {
        return stored.body === fingerprint
          ? ok(route, { ...stored.result, replayed: true })
          : claimRefused(route, 'idempotency_conflict');
      }
      if (item.status !== 'awaiting_response') {
        return claimRefused(route, 'invalid_domain_input', [
          { field: 'request_id', code: 'already_responded',
            message: 'You have already responded to this request.' },
        ]); // prettier-ignore
      }
      const answers = body.items ?? [];
      const missing = item.items.filter(
        (i) => !answers.some((a) => a.item_index === i.index && (a.evidence_ids.length || a.unavailable_reason)),
      ); // prettier-ignore
      if (missing.length) {
        return claimRefused(route, 'invalid_domain_input', missing.map((i) => ({
          field: `items.${i.index}`, code: 'answer_required',
          message: 'Upload a document for this item, or explain why you cannot provide it.' }))); // prettier-ignore
      }
      if (answers.some((a) => a.evidence_ids.some((e) => !mock.evidence.includes(e)))) {
        return claimRefused(route, 'invalid_domain_input', [
          { field: 'evidence_ids', code: 'not_found',
            message: 'A document could not be found. Remove it and upload the file again.' },
        ]); // prettier-ignore
      }
      const updated: EvidenceRequestMock = {
        ...item,
        status: 'response_sent',
        response: {
          responded_at: '2026-11-04T12:00:00',
          message: body.message ?? null,
          items: answers.map((a) => ({
            index: a.item_index,
            documents: a.evidence_ids.map((e, n) => ({ evidence_id: e, name: `document-${n + 1}.pdf` })),
            unavailable_reason: a.unavailable_reason,
          })),
        },
      }; // prettier-ignore
      mock.requests.set(item.request_id, updated);
      const result = { mode: 'live', replayed: false, data: updated };
      mock.responses.set(key, { body: fingerprint, result });
      if (mock.dropNextRespondResponse) {
        mock.dropNextRespondResponse = false;
        return route.abort('connectionreset');
      }
      return ok(route, result);
    }
    if (method === 'vati_tax.portal.api.get_submissions') {
      return ok(route, { ...meta(mock), data: ownSubmissions(mock) });
    }
    if (method === 'vati_tax.portal.api.get_submission') {
      const body = (route.request().postDataJSON() ?? {}) as { submission_id?: string };
      // As the server: another taxpayer's record is concealed as not found.
      const item = ownSubmissions(mock).find((x) => x.id === body.submission_id);
      return item
        ? ok(route, { ...meta(mock), data: item })
        : refused(route, 'DoesNotExistError', 404, 'Submission not found.');
    }
    return route.fulfill({
      status: 501,
      json: { error: 'not_mocked', method, ...FIXTURE },
    });
  });

  return mock;
}

/** A synthetic tenant outside the fixture principal's scope (integration/api/fixtures). */
export const OTHER_TENANT_ID = '10000000-0000-4000-8000-000000000002';

/**
 * `officer`: requests go unchanged to the fixture API as its signed-in principal.
 * `denied`: every Vati API request selects a tenant outside that principal's
 * scope, so the real fixture API refuses it (403 tenant_scope_denied). Nothing
 * is faked in the browser; the server's own access rule is exercised.
 */
export async function mockAuditorAccess(page: Page, persona: AuditorPersona): Promise<void> {
  if (persona === 'officer') return;
  await page.route(/\/api\/v1\//, (route) =>
    route.continue({
      headers: { ...route.request().headers(), 'x-vati-tenant-id': OTHER_TENANT_ID },
    }),
  );
}
