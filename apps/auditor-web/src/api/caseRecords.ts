/**
 * Client for the case record operations of the Vati /api/v1 contract
 * (integration/api/openapi.json): listEvidence and listEntities (TAX-041). Findings
 * come from the one listFindings client in ./review (TAX-042). Evidence is metadata and
 * provenance only; the contract never returns document content. Records not marked
 * synthetic, or not belonging to the requested case, are refused rather than shown.
 */
import { ApiError } from './errors';
import { listFindings, type Finding } from './review';
import {
  UUID,
  apiRequest,
  invalid,
  isRecord,
  requireId,
  serviceLinks,
  type Fetch,
  type Json,
  type ServiceLink,
} from './vati';

export type { EvidenceLocator, EvidenceRef, Finding, FindingKind } from './review';

const SHA256 = /^[0-9a-f]{64}$/;
const MAX_PAGES = 10;

export const EXTRACTION_STATUSES = ['pending', 'running', 'completed', 'failed'] as const;
export type ExtractionStatus = (typeof EXTRACTION_STATUSES)[number];
export const REVIEW_STATUSES = ['unreviewed', 'confirmed', 'rejected'] as const;
export type ReviewStatus = (typeof REVIEW_STATUSES)[number];

export interface Evidence {
  evidence_id: string;
  case_id: string;
  version: number;
  supersedes_evidence_id: string | null;
  document_sha256: string;
  media_type: string;
  size_bytes: number;
  document_type: string;
  source: string;
  received_at: string;
  extraction: {
    status: ExtractionStatus;
    extractor_version: string | null;
    error_code: string | null;
  };
  service_links: ServiceLink[];
  synthetic: true;
}

export interface MatchCandidate {
  candidate_id: string;
  dataset: string;
  dataset_version: string;
  score: string;
  review_status: ReviewStatus;
  reviewed_by: string | null;
}

export interface Entity {
  entity_id: string;
  case_id: string;
  ftm_schema: string;
  properties: Record<string, string[]>;
  evidence_ids: string[];
  match_candidates: MatchCandidate[];
  service_links: ServiceLink[];
  synthetic: true;
}

export interface CaseRecords {
  evidence: Evidence[];
  entities: Entity[];
  findings: Finding[];
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], what: string): T {
  return allowed.includes(value as T) ? (value as T) : invalid(what);
}

function text(value: Json, key: string, what: string): string {
  const field = value[key];
  return typeof field === 'string' && field !== '' ? field : invalid(what);
}

function nullableText(value: unknown, what: string): string | null {
  if (value === null) return null;
  return typeof value === 'string' ? value : invalid(what);
}

function ownedBy(value: Json, caseId: string, what: string) {
  if (value.synthetic !== true) invalid(`${what} that is not synthetic`);
  if (value.case_id !== caseId) invalid(`${what} from another case`);
}

export function parseEvidence(value: unknown, caseId: string): Evidence {
  if (!isRecord(value)) invalid('an evidence record');
  ownedBy(value, caseId, 'evidence');
  const id = text(value, 'evidence_id', 'an evidence record');
  if (!UUID.test(id)) invalid('an evidence record');
  const sha = text(value, 'document_sha256', 'a document hash');
  if (!SHA256.test(sha)) invalid('a document hash');
  const version = value.version;
  if (typeof version !== 'number' || !Number.isInteger(version) || version < 1)
    invalid('an evidence version');
  const size = value.size_bytes;
  if (typeof size !== 'number' || !Number.isInteger(size) || size < 1) invalid('a document size');
  const supersedes = value.supersedes_evidence_id;
  if (supersedes !== null && (typeof supersedes !== 'string' || !UUID.test(supersedes)))
    invalid('an evidence version link');
  const extraction = value.extraction;
  if (!isRecord(extraction)) invalid('an extraction state');
  return {
    evidence_id: id,
    case_id: caseId,
    version,
    supersedes_evidence_id: supersedes,
    document_sha256: sha,
    media_type: text(value, 'media_type', 'a media type'),
    size_bytes: size,
    document_type: text(value, 'document_type', 'a document type'),
    source: text(value, 'source', 'an evidence source'),
    received_at: text(value, 'received_at', 'a received time'),
    extraction: {
      status: oneOf(extraction.status, EXTRACTION_STATUSES, 'an extraction state'),
      extractor_version: nullableText(extraction.extractor_version, 'an extractor version'),
      error_code: nullableText(extraction.error_code, 'an extraction error'),
    },
    service_links: serviceLinks(value.service_links),
    synthetic: true,
  };
}

function parseCandidate(value: unknown): MatchCandidate {
  if (!isRecord(value)) invalid('a match candidate');
  const score = text(value, 'score', 'a match score');
  if (!/^(0(\.[0-9]{1,4})?|1(\.0{1,4})?)$/.test(score)) invalid('a match score');
  return {
    candidate_id: text(value, 'candidate_id', 'a match candidate'),
    dataset: text(value, 'dataset', 'a match dataset'),
    dataset_version: text(value, 'dataset_version', 'a match dataset'),
    score,
    review_status: oneOf(value.review_status, REVIEW_STATUSES, 'a match review status'),
    reviewed_by: nullableText(value.reviewed_by, 'a match reviewer'),
  };
}

export function parseEntity(value: unknown, caseId: string): Entity {
  if (!isRecord(value)) invalid('an entity');
  ownedBy(value, caseId, 'an entity');
  const id = text(value, 'entity_id', 'an entity');
  if (!UUID.test(id)) invalid('an entity');
  if (!isRecord(value.properties)) invalid('entity properties');
  const properties: Record<string, string[]> = {};
  for (const [key, values] of Object.entries(value.properties)) {
    if (!Array.isArray(values) || values.some((item) => typeof item !== 'string'))
      invalid('entity properties');
    properties[key] = values as string[];
  }
  const evidenceIds = value.evidence_ids;
  if (!Array.isArray(evidenceIds) || evidenceIds.some((e) => typeof e !== 'string'))
    invalid('entity evidence links');
  if (!Array.isArray(value.match_candidates)) invalid('match candidates');
  return {
    entity_id: id,
    case_id: caseId,
    ftm_schema: text(value, 'ftm_schema', 'an entity type'),
    properties,
    evidence_ids: evidenceIds as string[],
    match_candidates: value.match_candidates.map(parseCandidate),
    service_links: serviceLinks(value.service_links),
    synthetic: true,
  };
}

async function listAll<T>(
  caseId: string,
  segment: 'evidence' | 'entities',
  parse: (value: unknown, caseId: string) => T,
  fetchImpl: Fetch,
  signal?: AbortSignal,
): Promise<T[]> {
  requireId(caseId, 'case');
  const path = `/api/v1/cases/${encodeURIComponent(caseId)}/${segment}`;
  const items: T[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < MAX_PAGES; page++) {
    const query: Record<string, string> = { limit: '100' };
    if (cursor) query.cursor = cursor;
    const body = await apiRequest(path, { query, signal }, fetchImpl);
    if (!Array.isArray(body.data) || !isRecord(body.meta)) invalid(`a ${segment} page`);
    items.push(...body.data.map((item) => parse(item, caseId)));
    if (body.meta.has_more !== true) return items;
    cursor =
      typeof body.meta.next_cursor === 'string' ? body.meta.next_cursor : invalid('a cursor');
  }
  throw new ApiError('invalid', `This case has more ${segment} than this view can show.`);
}

export const listEvidence = (caseId: string, fetchImpl: Fetch = fetch, signal?: AbortSignal) =>
  listAll(caseId, 'evidence', parseEvidence, fetchImpl, signal);

export const listEntities = (caseId: string, fetchImpl: Fetch = fetch, signal?: AbortSignal) =>
  listAll(caseId, 'entities', parseEntity, fetchImpl, signal);

/** Evidence, entities and findings of one case, loaded together. */
export async function getCaseRecords(
  caseId: string,
  fetchImpl: Fetch = fetch,
  signal?: AbortSignal,
): Promise<CaseRecords> {
  const [evidence, entities, findings] = await Promise.all([
    listEvidence(caseId, fetchImpl, signal),
    listEntities(caseId, fetchImpl, signal),
    listFindings(caseId, fetchImpl, signal),
  ]);
  return { evidence, entities, findings };
}
