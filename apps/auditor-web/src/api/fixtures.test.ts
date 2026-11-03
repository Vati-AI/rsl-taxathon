import { describe, expect, it, vi } from 'vitest';
import { ApiError, getCase, listCases } from './fixtures';

const sample = {
  id: 'SYN-001',
  name: 'Synthetic Mountain Wholesale',
  scenario: 'legitimate_refund',
  currency: 'LSL',
  refund_claim: '15000.00',
  supported_refund: '15000.00',
  expected: 'human_clearance',
  synthetic: true,
};

function respond(status: number, body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status }));
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

describe('fixture client', () => {
  it('returns validated synthetic cases', async () => {
    const fetchImpl = respond(200, { mode: 'fixture', synthetic: true, items: [sample] });
    const cases = await listCases(fetchImpl);
    expect(cases).toEqual([sample]);
    expect(fetchImpl).toHaveBeenCalledWith('/api/cases', expect.anything());
  });

  it('rejects responses not marked as synthetic fixtures', async () => {
    const error = await failure(listCases(respond(200, { mode: 'live', items: [sample] })));
    expect(error.kind).toBe('invalid');
    const record = await failure(
      listCases(
        respond(200, {
          mode: 'fixture',
          synthetic: true,
          items: [{ ...sample, synthetic: false }],
        }),
      ),
    );
    expect(record.kind).toBe('invalid');
  });

  it('rejects amounts that are not decimal strings', async () => {
    const body = { mode: 'fixture', synthetic: true, items: [{ ...sample, refund_claim: 15000 }] };
    expect((await failure(listCases(respond(200, body)))).kind).toBe('invalid');
  });

  it('keeps missing amounts as missing rather than zero', async () => {
    const body = { mode: 'fixture', synthetic: true, item: { ...sample, supported_refund: null } };
    expect((await getCase('SYN-001', respond(200, body))).supported_refund).toBeNull();
  });

  it.each([
    [401, 'denied'],
    [403, 'denied'],
    [404, 'not_found'],
    [503, 'unavailable'],
  ])('maps HTTP %i to %s', async (status, kind) => {
    const error = await failure(getCase('SYN-001', respond(status, { error: 'x' })));
    expect(error.kind).toBe(kind);
    expect(error.retryable).toBe(kind === 'unavailable');
  });

  it('reports an unreachable service as retryable', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });
    const error = await failure(listCases(fetchImpl));
    expect(error.kind).toBe('unavailable');
    expect(error.retryable).toBe(true);
  });

  it('encodes case identifiers in the request path', async () => {
    const fetchImpl = respond(404, { error: 'not_found' });
    await failure(getCase('../health', fetchImpl));
    expect(fetchImpl).toHaveBeenCalledWith('/api/cases/..%2Fhealth', expect.anything());
  });
});
