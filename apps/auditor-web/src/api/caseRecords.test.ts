import { describe, expect, it, vi } from 'vitest';
import {
  CASE_ID,
  evId,
  makeEntity,
  makeEvidence,
  makeFinding,
  sha,
  wire,
  wireFinding,
} from '../testing/makeRecords';
import { getCaseRecords, listFindings, parseEvidence, parseFinding } from './caseRecords';
import { ApiError } from './errors';

const page = (data: unknown[], meta = { next_cursor: null, has_more: false }) => ({ data, meta });

function serve(bodies: Record<string, unknown>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const key = Object.keys(bodies).find((segment) => url.includes(`/${segment}?`));
    return new Response(JSON.stringify(key ? bodies[key] : {}), { status: key ? 200 : 404 });
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

describe('case record requests', () => {
  it('loads evidence, entities and findings for the case', async () => {
    const citation = {
      evidence_id: evId(1),
      document_sha256: sha('2a'),
      locator: { page: 3, record: null },
    };
    const fetchImpl = serve({
      evidence: page([wire(makeEvidence(1))]),
      entities: page([wire(makeEntity(1, 'Company', { name: ['Synthetic Co'] }, [evId(1)]))]),
      findings: page([wireFinding(makeFinding(1, { evidence_refs: [citation] }))]),
    });
    const records = await getCaseRecords(CASE_ID, fetchImpl);
    expect(records.evidence).toHaveLength(1);
    expect(records.entities[0].properties.name).toEqual(['Synthetic Co']);
    expect(records.findings[0].evidence_refs[0].locator.page).toBe(3);
    const urls = fetchImpl.mock.calls.map(([url]) => String(url)).sort();
    expect(urls).toEqual([
      `/api/v1/cases/${CASE_ID}/entities?limit=100`,
      `/api/v1/cases/${CASE_ID}/evidence?limit=100`,
      `/api/v1/cases/${CASE_ID}/findings?limit=100`,
    ]);
  });

  it('follows cursor pages', async () => {
    const pages = [
      page([wireFinding(makeFinding(1))], { next_cursor: '1', has_more: true } as never),
      page([wireFinding(makeFinding(2))]),
    ];
    const fetchImpl = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify(pages.shift())),
    );
    const findings = await listFindings(CASE_ID, fetchImpl);
    expect(findings).toHaveLength(2);
    expect(String(fetchImpl.mock.calls[1][0])).toContain('cursor=1');
  });

  it('never requests a malformed case ID', async () => {
    const fetchImpl = vi.fn();
    const error = await failure(listFindings('SYN-002', fetchImpl));
    expect(error.kind).toBe('not_found');
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('maps a denied tenant to an access error without data', async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            error: { code: 'tenant_scope_denied', message: 'x', retryable: false },
          }),
          { status: 403 },
        ),
    );
    expect((await failure(listFindings(CASE_ID, fetchImpl))).kind).toBe('denied');
  });
});

describe('case record parsing', () => {
  it('refuses records that are not synthetic or belong to another case', () => {
    expect(() => parseEvidence({ ...wire(makeEvidence(1)), synthetic: false }, CASE_ID)).toThrow(
      ApiError,
    );
    const other = { ...wire(makeEvidence(1)), case_id: '40000000-0000-4000-8000-000000000009' };
    expect(() => parseEvidence(other, CASE_ID)).toThrow(/another case/);
  });

  it('refuses malformed hashes and page references', () => {
    expect(() =>
      parseEvidence({ ...wire(makeEvidence(1)), document_sha256: 'abc' }, CASE_ID),
    ).toThrow(/document hash/);
    const bad = wireFinding(
      makeFinding(1, {
        evidence_refs: [
          { evidence_id: evId(1), document_sha256: sha('2a'), locator: { page: 0, record: null } },
        ],
      }),
    );
    expect(() => parseFinding(bad, CASE_ID)).toThrow(/page reference/);
  });

  it('refuses a proposed action that does not require approval', () => {
    const finding = wireFinding(makeFinding(1));
    finding.proposed_action = { type: 'none', requires_human_approval: false as never };
    expect(() => parseFinding(finding, CASE_ID)).toThrow(/without approval/);
  });

  it('refuses unknown finding kinds rather than guessing a label', () => {
    expect(() => parseFinding({ ...wireFinding(makeFinding(1)), kind: 'fraud' }, CASE_ID)).toThrow(
      /finding kind/,
    );
  });
});
