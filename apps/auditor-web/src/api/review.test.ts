import { afterEach, describe, expect, it, vi } from 'vitest';
import { fixtureRole } from '../session';
import { outcomeAvailability, refusalFor } from '../domain/review';
import { ApiError } from './errors';
import {
  getCaseReview,
  listFindings,
  parseCaseReview,
  proposeDecision,
  reviewDecision,
  type ProposeDecisionBody,
} from './review';

const CASE = '40000000-0000-4000-8000-000000000003';
const OTHER = '40000000-0000-4000-8000-000000000002';
const TENANT = '10000000-0000-4000-8000-000000000001';

function decision(overrides: Record<string, unknown> = {}) {
  return {
    decision_id: 'd0000000-0000-4000-8000-000000000003',
    case_id: CASE,
    tenant_id: TENANT,
    status: 'proposed',
    origin: 'officer',
    outcome: 'refer_to_specialist',
    operation: 'escalate',
    target_status: 'AWAITING_HUMAN',
    rationale: 'Synthetic rationale long enough for the minimum.',
    evidence_ids: [],
    finding_ids: ['70000000-0000-4000-8000-000000000301'],
    rating: 3,
    draft_id: null,
    proposed_by: '80000000-0000-4000-8000-000000000001',
    proposed_at: '2026-11-04T10:00:00Z',
    case_status_at_proposal: 'AWAITING_HUMAN',
    case_version: 6,
    reviewed_by: null,
    reviewed_at: null,
    review_reason: null,
    jube_sync: {
      state: 'not_started',
      attempts: 0,
      error_code: null,
      retryable: false,
      last_attempt_at: null,
      synced_at: null,
      changed: null,
      status_after: null,
      status_authority: 'jube',
    },
    ...overrides,
  };
}

function review(overrides: Record<string, unknown> = {}) {
  return {
    case_id: CASE,
    tenant_id: TENANT,
    case_version: 7,
    viewer: {
      officer_id: '80000000-0000-4000-8000-000000000003',
      roles: ['supervisor'],
      allowed_actions: ['decision.propose', 'decision.approve', 'make.coffee'],
    },
    open_decision: decision(),
    audit_events: [],
    ...overrides,
  };
}

function respond(status: number, body: unknown) {
  return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
    return new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  });
}

const PROPOSAL: ProposeDecisionBody = {
  outcome: 'refer_to_specialist',
  rationale: 'Synthetic rationale long enough for the minimum.',
  rating: 3,
  expected_case_status: 'AWAITING_HUMAN',
  expected_version: 6,
  evidence_ids: [],
  finding_ids: [],
};

afterEach(() => {
  fixtureRole.value = 'auditor';
});

describe('review parsing', () => {
  it('drops unknown permissions and rejects records for another case', () => {
    const parsed = parseCaseReview(review(), CASE);
    expect(parsed.viewer.allowed_actions).toEqual(['decision.propose', 'decision.approve']);
    expect(() => parseCaseReview(review(), OTHER)).toThrow(ApiError);
    expect(() =>
      parseCaseReview(review({ open_decision: decision({ case_id: OTHER }) }), CASE),
    ).toThrow(ApiError);
  });

  it('refuses outcomes outside the fixed list, such as a refund decision', () => {
    expect(() =>
      parseCaseReview(review({ open_decision: decision({ outcome: 'approve_refund' }) }), CASE),
    ).toThrow(/cannot be shown safely/);
  });
});

describe('review requests', () => {
  it('sends a mutation as POST with the Idempotency-Key and tenant', async () => {
    const fetchImpl = respond(201, { data: decision() });
    await proposeDecision(CASE, PROPOSAL, 'key-0000000000000001', fetchImpl);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(`/api/v1/cases/${CASE}/decisions`);
    expect(init?.method).toBe('POST');
    const headers = init?.headers as Record<string, string>;
    expect(headers['Idempotency-Key']).toBe('key-0000000000000001');
    expect(headers['X-Vati-Tenant-ID']).toBe(TENANT);
    expect(headers['X-Vati-Fixture-Role']).toBeUndefined();
    expect(JSON.parse(init?.body as string)).toEqual(PROPOSAL);
  });

  it('sends the fixture role only when another fixture officer is chosen', async () => {
    fixtureRole.value = 'supervisor';
    const fetchImpl = respond(200, { data: review() });
    await getCaseReview(CASE, fetchImpl);
    const headers = fetchImpl.mock.calls[0][1]?.headers as Record<string, string>;
    expect(headers['X-Vati-Fixture-Role']).toBe('supervisor');
    expect(fetchImpl.mock.calls[0][1]?.method).toBeUndefined();
  });

  it('maps a stale version to a conflict with its reason and current version', async () => {
    const body = {
      error: {
        code: 'version_conflict',
        message: 'stale',
        retryable: false,
        correlation_id: 'c0000000-0000-4000-8000-000000000001',
        field_errors: [{ field: 'expected_version', code: 'stale_case_version' }],
        current_version: 9,
      },
    };
    const error = await reviewDecision(
      CASE,
      decision().decision_id,
      { action: 'approve', expected_version: 7 },
      'key-0000000000000002',
      respond(409, body),
    ).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      kind: 'conflict',
      reason: 'stale_case_version',
      currentVersion: 9,
    });
    expect(refusalFor(error as ApiError)).toMatchObject({ next: 'reload' });
  });

  it('maps permission denials and outages to explanations', () => {
    expect(refusalFor(new ApiError('denied', 'x', { status: 403 })).title).toMatch(/role/);
    expect(refusalFor(new ApiError('unavailable', 'x'))).toMatchObject({ next: 'retry' });
    const self = new ApiError('denied', 'x', { reason: 'self_approval' });
    expect(refusalFor(self).title).toMatch(/your own proposal/);
  });

  it('follows finding pages and rejects findings for another case', async () => {
    const finding = {
      finding_id: '70000000-0000-4000-8000-000000000301',
      case_id: CASE,
      tenant_id: TENANT,
      kind: 'inference',
      category: 'worker_classification',
      explanation: 'Overlap.',
      evidence_refs: [],
      unresolved_amount: null,
      confidence: 'low',
      alternative_explanations: [],
      legal_route: { status: 'unverified', citation: null, verified_by: null },
      proposed_action: { type: 'specialist_review', requires_human_approval: true },
      producer: 'langgraph_run',
      producer_version: 'x@1',
      created_at: '2026-11-03T10:00:00Z',
    };
    const pages = [
      { data: [finding], meta: { next_cursor: '1', has_more: true } },
      { data: [finding], meta: { next_cursor: null, has_more: false } },
    ];
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify(pages.shift())));
    expect(await listFindings(CASE, fetchImpl)).toHaveLength(2);
    const wrong = respond(200, {
      data: [{ ...finding, case_id: OTHER }],
      meta: { has_more: false },
    });
    await expect(listFindings(CASE, wrong)).rejects.toThrow(ApiError);
  });
});

describe('outcome availability', () => {
  it('needs the referral permission and refuses a no-change transition', () => {
    expect(
      outcomeAvailability('refer_to_specialist', 'AWAITING_HUMAN', ['decision.propose']),
    ).toMatchObject({ available: false });
    expect(
      outcomeAvailability('refer_to_specialist', 'AWAITING_HUMAN', [
        'decision.propose',
        'specialist.refer',
      ]),
    ).toMatchObject({ available: true });
    expect(
      outcomeAvailability('continue_investigation', 'INVESTIGATING', ['decision.propose']),
    ).toMatchObject({ available: false });
  });
});
