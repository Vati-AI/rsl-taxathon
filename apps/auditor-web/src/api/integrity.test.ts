import { describe, expect, it, vi } from 'vitest';
import exampleText from '../../../../integration/api/examples/http/46_get_integrity_review.json?raw';
import { ApiError } from './errors';
import { getIntegrityReview, parseIntegrityReview, type IntegrityReview } from './integrity';

// The contract example comes from the fixture server, so the client is tested
// against the same synthetic body the browser receives.
const example = JSON.parse(exampleText) as { response: { body: { data: IntegrityReview } } };

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- free-form edits of a JSON test body
function review(change?: (data: Record<string, any>) => void): unknown {
  const data = structuredClone(example.response.body.data) as unknown as Record<string, unknown>;
  change?.(data);
  return data;
}

function respond(status: number, body: unknown) {
  return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
    return new Response(JSON.stringify(body), { status });
  });
}

function refused(change: Parameters<typeof review>[0]) {
  expect(() => parseIntegrityReview(review(change))).toThrow(ApiError);
}

describe('getIntegrityReview', () => {
  it('asks for the selected window', async () => {
    const fetchImpl = respond(200, { data: review() });
    const data = await getIntegrityReview(90, fetchImpl);
    expect(data.policy_exposure.status).toBe('available');
    expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/management/integrity?window_days=90');
  });

  it('turns a 403 into a denied error without data', async () => {
    const fetchImpl = respond(403, {
      error: { code: 'forbidden', message: 'Only supervisors can view the integrity review.' },
    });
    await expect(getIntegrityReview(30, fetchImpl)).rejects.toMatchObject({ kind: 'denied' });
  });
});

describe('parseIntegrityReview privacy and wording rules', () => {
  it('accepts the contract example', () => {
    const data = parseIntegrityReview(review());
    expect(data.review_signals.status).toBe('review_only');
  });

  it('refuses non-synthetic data', () => {
    refused((d) => {
      d.synthetic = false;
    });
  });

  it('refuses anything that looks like a login', () => {
    refused((d) => {
      d.override_patterns.by_control[0].label = 'officer.a@example.test';
    });
  });

  it('refuses a signal that names its subject', () => {
    refused((d) => {
      d.review_signals.by_kind[0].subject_actor = 'someone';
    });
  });

  it('refuses totals that do not add up, so a hidden cell cannot be inferred wrongly', () => {
    refused((d) => {
      d.policy_exposure.confirmed_gaps += 1;
    });
    refused((d) => {
      d.override_patterns.combined = null;
    });
  });

  it('refuses insufficient volume that still carries counts', () => {
    refused((d) => {
      d.policy_exposure.status = 'insufficient_volume';
    });
  });

  it('refuses a combined row presented as an ordinary row', () => {
    refused((d) => {
      d.policy_exposure.areas[0].key = 'combined';
    });
  });

  it('refuses unknown signal kinds and a minimum group size below 2', () => {
    refused((d) => {
      d.review_signals.by_kind[0].kind = 'suspected_misconduct';
    });
    refused((d) => {
      d.min_group_size = 1;
    });
  });

  it('accepts unavailable sections', () => {
    const data = parseIntegrityReview(
      review((d) => {
        d.policy_exposure = { status: 'unavailable' };
        d.override_patterns = { status: 'unavailable' };
        d.review_signals = { status: 'unavailable' };
        d.ledger = { available: false };
      }),
    );
    expect(data.policy_exposure.status).toBe('unavailable');
  });
});
