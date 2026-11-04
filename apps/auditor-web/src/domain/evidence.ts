import type {
  CaseRecords,
  Entity,
  Evidence,
  EvidenceLocator,
  EvidenceRef,
  Finding,
  FindingKind,
  MatchCandidate,
} from '../api/caseRecords';
import type { BadgeTheme } from './cases';
import { formatKey } from './cases';

const DOCUMENT_TYPES: Record<string, string> = {
  invoice: 'Invoice',
  contract: 'Contract',
  payslip: 'Payslip',
  import_declaration: 'Import declaration',
  bank_statement: 'Bank statement',
  financial_statement: 'Financial statement',
  other: 'Other document',
};

const SOURCES: Record<string, string> = {
  taxpayer_portal: 'Taxpayer portal upload',
  officer_upload: 'Officer upload',
  synthetic_fixture: 'Synthetic fixture',
};

const CATEGORIES: Record<string, string> = {
  refund_reconciliation: 'Refund reconciliation',
  related_party: 'Related party',
  worker_classification: 'Worker classification',
  imported_services: 'Imported services',
  policy_gap: 'Policy gap',
  other: 'Other',
};

const ACTIONS: Record<string, string> = {
  none: 'No action proposed',
  human_clearance: 'Officer clearance',
  request_evidence: 'Request evidence',
  specialist_review: 'Specialist review',
  policy_gap_review: 'Policy gap review',
};

export interface KindInfo {
  label: string;
  theme: BadgeTheme;
  /** What the kind means, in neutral words: none of these is a conclusion about the taxpayer. */
  description: string;
}

// Discrepancies (records disagree) and gaps (records absent) are deliberately distinct.
const KINDS: Record<FindingKind, KindInfo> = {
  contradictory_evidence: {
    label: 'Records disagree',
    theme: 'amber',
    description: 'Two or more records conflict. The conflict is unresolved, not a conclusion.',
  },
  missing_evidence: {
    label: 'Evidence missing',
    theme: 'gray',
    description: 'A record needed to check this point has not been supplied.',
  },
  unverified_evidence: {
    label: 'Not yet verified',
    theme: 'gray',
    description: 'A record exists but has not been checked against its source.',
  },
  observed_fact: {
    label: 'Observed in records',
    theme: 'blue',
    description: 'Stated directly in the cited records.',
  },
  inference: {
    label: 'Inference',
    theme: 'violet',
    description: 'A possible reading of the records that needs officer judgement.',
  },
};

export const KIND_ORDER: FindingKind[] = [
  'contradictory_evidence',
  'missing_evidence',
  'unverified_evidence',
  'observed_fact',
  'inference',
];

const UNCERTAIN_KINDS = new Set<FindingKind>(['contradictory_evidence', 'unverified_evidence']);

export const documentTypeLabel = (type: string) => DOCUMENT_TYPES[type] ?? formatKey(type);
export const sourceLabel = (source: string) => SOURCES[source] ?? formatKey(source);
export const categoryLabel = (category: string) => CATEGORIES[category] ?? formatKey(category);
export const actionLabel = (action: string) => ACTIONS[action] ?? formatKey(action);
export const kindInfo = (kind: FindingKind): KindInfo => KINDS[kind];

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/** First and last characters, for tables; the full hash is always available alongside. */
export const shortHash = (sha: string) => `${sha.slice(0, 8)}…${sha.slice(-4)}`;

export function locatorLabel(locator: EvidenceLocator): string {
  const parts: string[] = [];
  if (locator.page !== null) parts.push(`Page ${locator.page}`);
  if (locator.record !== null) parts.push(`Record ${locator.record}`);
  return parts.length ? parts.join(', ') : 'Whole document';
}

export function extractionLabel(item: Evidence): { label: string; theme: BadgeTheme } {
  switch (item.extraction.status) {
    case 'completed':
      return item.extraction.error_code
        ? { label: 'Partly extracted', theme: 'amber' }
        : { label: 'Extracted', theme: 'green' };
    case 'failed':
      return { label: 'Extraction failed', theme: 'red' };
    case 'running':
      return { label: 'Extracting', theme: 'blue' };
    default:
      return { label: 'Waiting for extraction', theme: 'gray' };
  }
}

export type CitationStatus = 'ok' | 'not_on_file' | 'hash_mismatch';

/**
 * A citation is only trusted when it names evidence on file with the same stored hash.
 * A different hash means the citation points at other bytes than the stored original.
 */
export function citationStatus(ref: EvidenceRef, byId: Map<string, Evidence>): CitationStatus {
  const item = byId.get(ref.evidence_id);
  if (!item) return 'not_on_file';
  return item.document_sha256 === ref.document_sha256 ? 'ok' : 'hash_mismatch';
}

export const evidenceIndex = (evidence: Evidence[]) =>
  new Map(evidence.map((item) => [item.evidence_id, item]));

/** All versions of the document that `evidenceId` belongs to, oldest first. */
export function versionHistory(evidence: Evidence[], evidenceId: string): Evidence[] {
  const byId = evidenceIndex(evidence);
  const successor = new Map<string, Evidence>();
  for (const item of evidence)
    if (item.supersedes_evidence_id) successor.set(item.supersedes_evidence_id, item);
  let first = byId.get(evidenceId);
  if (!first) return [];
  const seen = new Set<string>([first.evidence_id]);
  while (first.supersedes_evidence_id) {
    const previous = byId.get(first.supersedes_evidence_id);
    if (!previous || seen.has(previous.evidence_id)) break;
    seen.add(previous.evidence_id);
    first = previous;
  }
  const chain = [first];
  let next = successor.get(first.evidence_id);
  while (next && !chain.includes(next)) {
    chain.push(next);
    next = successor.get(next.evidence_id);
  }
  return chain;
}

/** Evidence the officer is likely to want first: latest versions, then superseded ones. */
export function sortedEvidence(evidence: Evidence[]): Evidence[] {
  const superseded = new Set(evidence.map((e) => e.supersedes_evidence_id).filter(Boolean));
  return [...evidence].sort((a, b) => {
    const left = superseded.has(a.evidence_id) ? 1 : 0;
    const right = superseded.has(b.evidence_id) ? 1 : 0;
    return left - right || a.received_at.localeCompare(b.received_at);
  });
}

export function isSuperseded(evidence: Evidence[], evidenceId: string): Evidence | null {
  return evidence.find((item) => item.supersedes_evidence_id === evidenceId) ?? null;
}

export interface Citation {
  finding: Finding;
  locators: EvidenceLocator[];
}

/** Findings that cite this evidence, with each cited location. */
export function citedBy(findings: Finding[], evidenceId: string): Citation[] {
  return findings
    .map((finding) => ({
      finding,
      locators: finding.evidence_refs
        .filter((ref) => ref.evidence_id === evidenceId)
        .map((ref) => ref.locator),
    }))
    .filter((entry) => entry.locators.length > 0);
}

export function groupFindings(findings: Finding[]) {
  return KIND_ORDER.map((kind) => ({
    kind,
    ...KINDS[kind],
    findings: findings.filter((finding) => finding.kind === kind),
  })).filter((group) => group.findings.length > 0);
}

const PARTY_SCHEMAS = new Set(['Company', 'Person', 'Organization', 'LegalEntity']);

export interface PartyRow {
  entity: Entity;
  name: string;
  schema: string;
  details: string[];
  candidates: MatchCandidate[];
  missingEvidence: string[];
}

export interface OwnershipRow {
  entity: Entity;
  owner: string;
  asset: string;
  percentage: string | null;
  role: string | null;
  since: string | null;
  /** Unresolved findings that cite this ownership's records. Non-empty means "uncertain". */
  disputes: Finding[];
  missingEvidence: string[];
}

export interface PaymentRow {
  entity: Entity;
  payer: string;
  beneficiary: string;
  amount: string | null;
  currency: string;
  date: string | null;
  purpose: string | null;
  missingEvidence: string[];
}

export interface RelationshipView {
  parties: PartyRow[];
  ownership: OwnershipRow[];
  payments: PaymentRow[];
  other: Entity[];
}

const first = (entity: Entity, key: string) => entity.properties[key]?.[0] ?? null;

export function entityName(entity: Entity): string {
  return first(entity, 'name') ?? `Unnamed ${entity.ftm_schema.toLowerCase()}`;
}

/**
 * Arrange FtM entities into parties, ownership and payments. Ownership is marked
 * uncertain when an unresolved finding (records disagree, not yet verified) cites
 * one of its records; a percentage is never adjusted or picked by this view.
 */
export function relationshipView(records: CaseRecords): RelationshipView {
  const byId = new Map(records.entities.map((entity) => [entity.entity_id, entity]));
  const onFile = new Set(records.evidence.map((item) => item.evidence_id));
  const missing = (entity: Entity) => entity.evidence_ids.filter((id) => !onFile.has(id));
  const name = (entity: Entity, key: string) => {
    const ref = first(entity, key);
    const target = ref ? byId.get(ref) : undefined;
    return target ? entityName(target) : 'Party not in this case';
  };
  const unresolved = records.findings.filter((finding) => UNCERTAIN_KINDS.has(finding.kind));
  const view: RelationshipView = { parties: [], ownership: [], payments: [], other: [] };
  for (const entity of records.entities) {
    if (PARTY_SCHEMAS.has(entity.ftm_schema)) {
      const details = [first(entity, 'jurisdiction'), first(entity, 'nationality')]
        .filter((value): value is string => Boolean(value))
        .map((code) => `Jurisdiction ${code.toUpperCase()}`);
      const registration = first(entity, 'registrationNumber');
      if (registration) details.push(`Registration ${registration}`);
      view.parties.push({
        entity,
        name: entityName(entity),
        schema: entity.ftm_schema,
        details,
        candidates: entity.match_candidates,
        missingEvidence: missing(entity),
      });
    } else if (entity.ftm_schema === 'Ownership') {
      view.ownership.push({
        entity,
        owner: name(entity, 'owner'),
        asset: name(entity, 'asset'),
        percentage: first(entity, 'percentage'),
        role: first(entity, 'role'),
        since: first(entity, 'startDate'),
        disputes: unresolved.filter((finding) =>
          finding.evidence_refs.some((ref) => entity.evidence_ids.includes(ref.evidence_id)),
        ),
        missingEvidence: missing(entity),
      });
    } else if (entity.ftm_schema === 'Payment') {
      view.payments.push({
        entity,
        payer: name(entity, 'payer'),
        beneficiary: name(entity, 'beneficiary'),
        amount: first(entity, 'amount'),
        currency: first(entity, 'currency') ?? '',
        date: first(entity, 'date'),
        purpose: first(entity, 'purpose'),
        missingEvidence: missing(entity),
      });
    } else {
      view.other.push(entity);
    }
  }
  return view;
}

export const REVIEW_LABELS: Record<string, { label: string; theme: BadgeTheme }> = {
  unreviewed: { label: 'Unreviewed match', theme: 'amber' },
  confirmed: { label: 'Confirmed by reviewer', theme: 'green' },
  rejected: { label: 'Rejected by reviewer', theme: 'gray' },
};
