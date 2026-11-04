import type { Entity, Evidence, Finding } from '../api/caseRecords';

// Synthetic records for unit tests, shaped like integration/api/fixtures/case_records.json.
export const CASE_ID = '40000000-0000-4000-8000-000000000002';
const TENANT = '10000000-0000-4000-8000-000000000001';

export const evId = (n: number) => `50000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
export const entId = (n: number) => `60000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
export const finId = (n: number) => `70000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
export const sha = (pair: string) => pair.repeat(32);

export function makeEvidence(n: number, overrides: Partial<Evidence> = {}): Evidence {
  return {
    evidence_id: evId(n),
    case_id: CASE_ID,
    version: 1,
    supersedes_evidence_id: null,
    document_sha256: sha('2a'),
    media_type: 'application/pdf',
    size_bytes: 1000,
    document_type: 'invoice',
    source: 'synthetic_fixture',
    received_at: `2026-11-03T10:0${n % 10}:00Z`,
    extraction: {
      status: 'completed',
      extractor_version: 'docling-serve@locked',
      error_code: null,
    },
    service_links: [],
    synthetic: true,
    ...overrides,
  };
}

export function makeEntity(
  n: number,
  schema: string,
  properties: Record<string, string[]>,
  evidenceIds: string[] = [],
): Entity {
  return {
    entity_id: entId(n),
    case_id: CASE_ID,
    ftm_schema: schema,
    properties,
    evidence_ids: evidenceIds,
    match_candidates: [],
    service_links: [],
    synthetic: true,
  };
}

export function makeFinding(n: number, overrides: Partial<Finding> = {}): Finding {
  return {
    finding_id: finId(n),
    case_id: CASE_ID,
    kind: 'observed_fact',
    category: 'related_party',
    explanation: 'Synthetic explanation.',
    evidence_refs: [],
    unresolved_amount: null,
    confidence: 'medium',
    alternative_explanations: [],
    legal_route: { status: 'unverified', citation: null },
    proposed_action: { type: 'none' },
    producer: 'langgraph_run',
    created_at: '2026-11-03T13:00:00Z',
    ...overrides,
  };
}

/** Wire shape of a record as the API sends it (adds fields the client does not keep). */
export const wire = <T extends object>(record: T) => ({ tenant_id: TENANT, ...record });
export const wireFinding = (finding: Finding) => ({
  ...wire(finding),
  legal_route: { ...finding.legal_route, verified_by: null },
  proposed_action: { ...finding.proposed_action, requires_human_approval: true },
});
