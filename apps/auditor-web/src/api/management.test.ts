import { describe, expect, it, vi } from 'vitest';
import exampleText from '../../../../integration/api/examples/http/25_get_management_summary.json?raw';
import { ApiError } from './errors';
import { getManagementSummary, parseManagementSummary, type ManagementSummary } from './management';

const TENANT = '10000000-0000-4000-8000-000000000001';
const REF = 'c0000000-0000-4000-8000-000000000001';

// The contract example is generated from the fixture server, so the client is
// tested against the same synthetic body the browser receives.
const example = JSON.parse(exampleText) as { response: { body: { data: ManagementSummary } } };

function summary(): ManagementSummary {
  return structuredClone(example.response.body.data);
}

function respond(status: number, body: unknown) {
  return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
    return new Response(JSON.stringify(body), { status });
  });
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

describe('getManagementSummary', () => {
  it('asks for the selected window with tenant and correlation headers', async () => {
    const fetchImpl = respond(200, { data: summary() });
    const data = await getManagementSummary(7, fetchImpl);
    expect(data.workload.open_cases).toBe(9);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe('/api/v1/management/summary?window_days=7');
    const headers = init?.headers as Record<string, string>;
    expect(headers['X-Vati-Tenant-ID']).toBe(TENANT);
    expect(headers['X-Correlation-ID']).toMatch(/^[0-9a-f-]{36}$/);
  });

  it('turns 403 forbidden into a denied error with no figures', async () => {
    const body = {
      error: { code: 'forbidden', message: 'x', retryable: false, correlation_id: REF },
    };
    const error = await failure(getManagementSummary(30, respond(403, body)));
    expect(error.kind).toBe('denied');
    expect(error.reference).toBe(REF);
  });

  it('reports an unavailable service as retryable', async () => {
    const body = {
      error: { code: 'upstream_unavailable', message: 'x', retryable: true, correlation_id: REF },
    };
    const error = await failure(getManagementSummary(30, respond(503, body)));
    expect(error.kind).toBe('unavailable');
    expect(error.retryable).toBe(true);
  });
});

describe('parseManagementSummary', () => {
  it('accepts the contract example', () => {
    const data = parseManagementSummary(summary());
    expect(data.closure_outcomes.no_further_action).toEqual({ numerator: 3, denominator: 6 });
    expect(data.assurance.available).toBe(true);
  });

  it('accepts zero denominators and empty durations', () => {
    const data = summary();
    data.workload = { open_cases: 0, unassigned_open_cases: 0, by_status: [], by_officer: [] };
    data.unresolved_evidence = {
      outstanding_requests: 0,
      overdue_requests: 0,
      cases_with_outstanding_requests: 0,
      open_cases_awaiting_evidence: 0,
    };
    data.elapsed.open_case_age = {
      count: 0,
      excluded: 0,
      median_hours: null,
      p90_hours: null,
      max_hours: null,
    };
    data.closure_outcomes = {
      closed_in_window: 0,
      without_recorded_outcome: 0,
      no_further_action: { numerator: 0, denominator: 0 },
      after_correction: { numerator: 0, denominator: 0 },
    };
    expect(() => parseManagementSummary(data)).not.toThrow();
  });

  it('accepts an unavailable ledger with a reason', () => {
    const data = summary();
    data.assurance = { available: false, reason: 'The audit ledger is not available.' };
    expect(parseManagementSummary(data).assurance.available).toBe(false);
  });

  const broken: [string, (d: ManagementSummary) => void][] = [
    ['not synthetic', (d) => ((d as { synthetic: unknown }).synthetic = false)],
    ['numerator above denominator', (d) => (d.closure_outcomes.after_correction.numerator = 99)],
    [
      'ratio denominator not matching closed cases with an outcome',
      (d) => (d.closure_outcomes.no_further_action.denominator = 7),
    ],
    ['status rows not adding up', (d) => (d.workload.by_status[0].count += 1)],
    ['officer rows not adding up', (d) => (d.workload.unassigned_open_cases += 1)],
    ['legal-route counts not adding up', (d) => (d.legal_route_coverage.unverified += 1)],
    ['negative count', (d) => (d.unresolved_evidence.overdue_requests = -1)],
    [
      'awaiting-evidence count not matching the status rows',
      (d) => (d.unresolved_evidence.open_cases_awaiting_evidence += 1),
    ],
    ['more overdue than outstanding requests', (d) => (d.unresolved_evidence.overdue_requests = 4)],
    ['fractional count', (d) => (d.workload.open_cases = 8.5)],
    ['median present with no cases', (d) => (d.elapsed.time_to_close.count = 0)],
    ['malformed hours', (d) => (d.elapsed.open_case_age.median_hours = '25.83')],
    [
      'unknown signal kind',
      (d) => {
        if (d.assurance.available)
          (d.assurance.review_signals[0] as { kind: string }).kind = 'officer_misconduct';
      },
    ],
  ];
  for (const [name, mutate] of broken) {
    it(`rejects ${name}`, () => {
      const data = summary();
      mutate(data);
      expect(() => parseManagementSummary(data)).toThrow(ApiError);
    });
  }
});
