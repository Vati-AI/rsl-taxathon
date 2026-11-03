import { describe, expect, it, vi } from 'vitest';
import { makeCase } from '../testing/makeCase';
import { ApiError } from './errors';
import { getCase, getTaxpayer, listAssignedCases, parseCase } from './vati';

const TENANT = '10000000-0000-4000-8000-000000000001';
const REF = 'c0000000-0000-4000-8000-000000000001';

function respond(status: number, body: unknown) {
  return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
    return new Response(JSON.stringify(body), { status });
  });
}

function errorBody(code: string) {
  return { error: { code, message: 'x', retryable: false, correlation_id: REF } };
}

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error('Expected an ApiError');
}

describe('requests', () => {
  it('asks for the assigned queue with tenant and correlation headers', async () => {
    const fetchImpl = respond(200, {
      data: [makeCase(1)],
      meta: { next_cursor: null, has_more: false },
    });
    const cases = await listAssignedCases(fetchImpl);
    expect(cases.map((c) => c.display_id)).toEqual(['SYN-001']);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe('/api/v1/cases?assigned_to=me&limit=100');
    const headers = init?.headers as Record<string, string>;
    expect(headers['X-Vati-Tenant-ID']).toBe(TENANT);
    expect(headers['X-Correlation-ID']).toMatch(/^[0-9a-f-]{36}$/);
  });

  it('follows cursor pages until the queue is complete', async () => {
    const pages = [
      { data: [makeCase(1)], meta: { next_cursor: '1', has_more: true } },
      { data: [makeCase(2)], meta: { next_cursor: null, has_more: false } },
    ];
    const fetchImpl = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify(pages.shift())),
    );
    const cases = await listAssignedCases(fetchImpl);
    expect(cases.map((c) => c.display_id)).toEqual(['SYN-001', 'SYN-002']);
    expect(fetchImpl.mock.calls[1][0]).toBe('/api/v1/cases?assigned_to=me&limit=100&cursor=1');
  });

  it('treats an empty page as an empty queue, not an error', async () => {
    const fetchImpl = respond(200, { data: [], meta: { next_cursor: null, has_more: false } });
    expect(await listAssignedCases(fetchImpl)).toEqual([]);
  });

  it('treats malformed IDs in links as not found without calling the service', async () => {
    const fetchImpl = respond(200, {});
    for (const id of ['SYN-001', '../cases', '']) {
      expect((await failure(getCase(id, fetchImpl))).kind).toBe('not_found');
      expect((await failure(getTaxpayer(id, fetchImpl))).kind).toBe('not_found');
    }
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe('errors', () => {
  it.each([
    [403, 'tenant_scope_denied', 'denied'],
    [403, 'forbidden', 'denied'],
    [401, 'unauthenticated', 'denied'],
    [404, 'not_found', 'not_found'],
    [503, 'upstream_timeout', 'unavailable'],
    [503, 'upstream_unavailable', 'unavailable'],
    [429, 'rate_limited', 'unavailable'],
    [400, 'invalid_case_id', 'invalid'],
  ])('maps HTTP %i %s to %s and keeps the reference', async (status, code, kind) => {
    const error = await failure(getCase(makeCase(1).case_id, respond(status, errorBody(code))));
    expect(error.kind).toBe(kind);
    expect(error.code).toBe(code);
    expect(error.reference).toBe(REF);
    expect(error.retryable).toBe(kind === 'unavailable');
  });

  it('reports an unreachable service as retryable with its own reference', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });
    const error = await failure(listAssignedCases(fetchImpl));
    expect(error.kind).toBe('unavailable');
    expect(error.reference).toMatch(/^[0-9a-f-]{36}$/);
  });
});

describe('record validation', () => {
  it('refuses records that are not synthetic', () => {
    expect(() => parseCase({ ...makeCase(1), synthetic: false })).toThrow(ApiError);
  });

  it('refuses taxpayer context that belongs to another taxpayer', () => {
    const item = makeCase(1);
    const wrong = { ...item, taxpayer: { ...item.taxpayer, taxpayer_id: makeCase(2).taxpayer_id } };
    expect(() => parseCase(wrong)).toThrow(ApiError);
  });

  it('refuses float money and unknown statuses', () => {
    expect(() =>
      parseCase({ ...makeCase(1), refund_claim: { amount: 1000, currency: 'LSL' } }),
    ).toThrow(ApiError);
    expect(() => parseCase({ ...makeCase(1), status: 'APPROVED' })).toThrow(ApiError);
  });

  it('keeps a missing refund claim as missing', () => {
    expect(parseCase(makeCase(1, { refund_claim: null })).refund_claim).toBeNull();
  });

  it('rejects a whole page when one record is invalid', async () => {
    const page = {
      data: [makeCase(1), { ...makeCase(2), synthetic: false }],
      meta: { next_cursor: null, has_more: false },
    };
    expect((await failure(listAssignedCases(respond(200, page)))).kind).toBe('invalid');
  });
});
