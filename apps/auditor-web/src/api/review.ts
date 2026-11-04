/**
 * Officer review operations of the Vati /api/v1 contract (integration/api/openapi.json,
 * TAX-042, ADR-014): getCaseReview, listFindings, proposeDecision, reviewDecision and
 * requestEvidence. Decisions are recorded in Frappe and only an approved decision is
 * executed in Jube (ADR-013); this client never sends anything without an explicit
 * officer action, and every mutation carries an Idempotency-Key the caller keeps for
 * retries.
 */
import {
  CASE_STATUSES,
  UUID,
  apiRequest,
  invalid,
  isRecord,
  money,
  requireId,
  strings,
  type CaseStatus,
  type Fetch,
  type Json,
  type Money,
} from './vati';

export const OUTCOMES = [
  'close_no_further_action',
  'close_after_correction',
  'request_further_evidence',
  'continue_investigation',
  'escalate_to_supervisor',
  'refer_to_specialist',
] as const;
export type DecisionOutcome = (typeof OUTCOMES)[number];

export const PERMISSIONS = [
  'decision.propose',
  'specialist.refer',
  'evidence_request.authorise',
  'decision.approve',
] as const;
export type ReviewPermission = (typeof PERMISSIONS)[number];

export const DOCUMENT_TYPES = [
  'invoice',
  'contract',
  'payslip',
  'import_declaration',
  'bank_statement',
  'financial_statement',
  'other',
] as const;
export type DocumentType = (typeof DOCUMENT_TYPES)[number];

export type DecisionStatus = 'proposed' | 'approved' | 'rejected' | 'withdrawn';
export type SyncStateName = 'not_started' | 'pending' | 'synced' | 'failed';

export interface JubeSync {
  state: SyncStateName;
  attempts: number;
  error_code: string | null;
  retryable: boolean;
  last_attempt_at: string | null;
  synced_at: string | null;
  changed: boolean | null;
  status_after: CaseStatus | null;
}

export interface Decision {
  decision_id: string;
  case_id: string;
  tenant_id: string;
  status: DecisionStatus;
  outcome: DecisionOutcome;
  operation: 'transition' | 'escalate';
  target_status: CaseStatus;
  rationale: string;
  evidence_ids: string[];
  finding_ids: string[];
  rating: number;
  proposed_by: string;
  proposed_at: string;
  case_status_at_proposal: CaseStatus;
  case_version: number;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_reason: string | null;
  jube_sync: JubeSync;
}

export interface ReviewEvent {
  event_id: string;
  sequence: number | null;
  actor_id: string;
  actor_role: 'auditor' | 'specialist' | 'supervisor' | 'service';
  action: string;
  subject_type: 'decision' | 'evidence_request';
  subject_id: string;
  occurred_at: string;
  outcome: string;
}

export interface CaseReview {
  case_id: string;
  tenant_id: string;
  case_version: number;
  viewer: { officer_id: string; roles: string[]; allowed_actions: ReviewPermission[] };
  open_decision: Decision | null;
  audit_events: ReviewEvent[];
}

export type ProposedActionType =
  'none' | 'human_clearance' | 'request_evidence' | 'specialist_review' | 'policy_gap_review';

export interface Finding {
  finding_id: string;
  case_id: string;
  kind:
    | 'observed_fact'
    | 'inference'
    | 'missing_evidence'
    | 'unverified_evidence'
    | 'contradictory_evidence';
  category: string;
  explanation: string;
  evidence_refs: { evidence_id: string; locator: { page: number | null; record: string | null } }[];
  unresolved_amount: Money | null;
  confidence: 'low' | 'medium' | 'high';
  alternative_explanations: string[];
  legal_route: { status: 'unverified' | 'verified_by_reviewer' | 'no_verified_route' };
  proposed_action: { type: ProposedActionType };
  producer: 'jube_rule' | 'langgraph_run' | 'officer';
  created_at: string;
}

export interface RequestedItem {
  document_type: DocumentType;
  description: string;
}

export interface EvidenceRequest {
  request_id: string;
  case_id: string;
  status: 'recorded' | 'sent' | 'responded' | 'withdrawn';
  authorised_by: string;
  finding_ids: string[];
  items: RequestedItem[];
  due_date: string;
  case_version: number;
}

export interface ProposeDecisionBody {
  outcome: DecisionOutcome;
  rationale: string;
  rating: number;
  expected_case_status: CaseStatus;
  expected_version: number;
  evidence_ids: string[];
  finding_ids: string[];
}

export interface ReviewDecisionBody {
  action: 'approve' | 'reject' | 'withdraw';
  expected_version: number;
  reason?: string;
}

export interface RequestEvidenceBody {
  expected_version: number;
  finding_ids: string[];
  items: RequestedItem[];
  due_date: string;
  message_to_taxpayer: string;
}

const MAX_FINDING_PAGES = 5;

function oneOf<T extends string>(value: unknown, allowed: readonly T[], what: string): T {
  if (typeof value !== 'string' || !allowed.includes(value as T)) invalid(what);
  return value as T;
}

function uuids(value: unknown, what: string): string[] {
  if (!Array.isArray(value) || value.some((v) => typeof v !== 'string' || !UUID.test(v)))
    invalid(what);
  return value as string[];
}

function status(value: unknown): CaseStatus {
  return oneOf(value, CASE_STATUSES, 'a case status');
}

function nullableStatus(value: unknown): CaseStatus | null {
  return value === null ? null : status(value);
}

function sameCase(value: Json, caseId: string) {
  if (value.case_id !== caseId) invalid('a record for another case');
}

export function parseDecision(value: unknown, caseId: string): Decision {
  if (!isRecord(value) || !isRecord(value.jube_sync)) invalid('a decision');
  strings(value, [
    'decision_id',
    'case_id',
    'tenant_id',
    'rationale',
    'proposed_by',
    'proposed_at',
  ]);
  sameCase(value, caseId);
  const sync = value.jube_sync;
  if (typeof value.rating !== 'number' || typeof value.case_version !== 'number')
    invalid('a decision');
  return {
    ...(value as unknown as Decision),
    status: oneOf(value.status, ['proposed', 'approved', 'rejected', 'withdrawn'], 'a status'),
    outcome: oneOf(value.outcome, OUTCOMES, 'a decision outcome'),
    operation: oneOf(value.operation, ['transition', 'escalate'], 'an operation'),
    target_status: status(value.target_status),
    case_status_at_proposal: status(value.case_status_at_proposal),
    evidence_ids: uuids(value.evidence_ids, 'evidence references'),
    finding_ids: uuids(value.finding_ids, 'finding references'),
    jube_sync: {
      ...(sync as unknown as JubeSync),
      state: oneOf(sync.state, ['not_started', 'pending', 'synced', 'failed'], 'a sync state'),
      status_after: nullableStatus(sync.status_after),
    },
  };
}

export function parseCaseReview(value: unknown, caseId: string): CaseReview {
  if (!isRecord(value) || !isRecord(value.viewer)) invalid('a review state');
  strings(value, ['case_id', 'tenant_id']);
  sameCase(value, caseId);
  if (typeof value.case_version !== 'number' || value.case_version < 1) invalid('a case version');
  const viewer = value.viewer;
  if (typeof viewer.officer_id !== 'string' || !Array.isArray(viewer.roles)) invalid('a viewer');
  if (!Array.isArray(viewer.allowed_actions)) invalid('a viewer');
  // Unknown permissions are dropped rather than trusted.
  const allowed = viewer.allowed_actions.filter((a): a is ReviewPermission =>
    PERMISSIONS.includes(a as ReviewPermission),
  );
  if (!Array.isArray(value.audit_events)) invalid('a review trail');
  const events = value.audit_events.map((event) => {
    if (!isRecord(event)) invalid('a review event');
    strings(event, ['event_id', 'actor_id', 'action', 'subject_id', 'occurred_at', 'outcome']);
    return event as unknown as ReviewEvent;
  });
  return {
    case_id: value.case_id as string,
    tenant_id: value.tenant_id as string,
    case_version: value.case_version,
    viewer: {
      officer_id: viewer.officer_id,
      roles: viewer.roles.filter((r): r is string => typeof r === 'string'),
      allowed_actions: allowed,
    },
    open_decision: value.open_decision === null ? null : parseDecision(value.open_decision, caseId),
    audit_events: events,
  };
}

export function parseFinding(value: unknown, caseId: string): Finding {
  if (!isRecord(value) || !isRecord(value.proposed_action) || !isRecord(value.legal_route))
    invalid('a finding');
  strings(value, ['finding_id', 'case_id', 'kind', 'category', 'explanation', 'created_at']);
  sameCase(value, caseId);
  if (!Array.isArray(value.evidence_refs) || !Array.isArray(value.alternative_explanations))
    invalid('a finding');
  return {
    ...(value as unknown as Finding),
    unresolved_amount: money(value.unresolved_amount),
    confidence: oneOf(value.confidence, ['low', 'medium', 'high'], 'a confidence'),
    proposed_action: {
      type: oneOf(
        value.proposed_action.type,
        ['none', 'human_clearance', 'request_evidence', 'specialist_review', 'policy_gap_review'],
        'a proposed action',
      ),
    },
  };
}

export function parseEvidenceRequest(value: unknown, caseId: string): EvidenceRequest {
  if (!isRecord(value)) invalid('an evidence request');
  strings(value, ['request_id', 'case_id', 'authorised_by', 'due_date']);
  sameCase(value, caseId);
  return {
    ...(value as unknown as EvidenceRequest),
    status: oneOf(value.status, ['recorded', 'sent', 'responded', 'withdrawn'], 'a status'),
  };
}

function casePath(caseId: string, suffix: string) {
  requireId(caseId, 'case');
  return `/api/v1/cases/${encodeURIComponent(caseId)}/${suffix}`;
}

export async function getCaseReview(
  caseId: string,
  fetchImpl: Fetch = fetch,
  signal?: AbortSignal,
) {
  const body = await apiRequest(casePath(caseId, 'review'), { signal }, fetchImpl);
  return parseCaseReview(body.data, caseId);
}

export async function listFindings(caseId: string, fetchImpl: Fetch = fetch, signal?: AbortSignal) {
  const findings: Finding[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < MAX_FINDING_PAGES; page++) {
    const query: Record<string, string> = { limit: '100' };
    if (cursor) query.cursor = cursor;
    const body = await apiRequest(casePath(caseId, 'findings'), { query, signal }, fetchImpl);
    if (!Array.isArray(body.data) || !isRecord(body.meta)) invalid('a finding page');
    findings.push(...body.data.map((f) => parseFinding(f, caseId)));
    if (body.meta.has_more !== true) return findings;
    cursor =
      typeof body.meta.next_cursor === 'string' ? body.meta.next_cursor : invalid('a cursor');
  }
  return invalid('more findings than this view can show');
}

export async function proposeDecision(
  caseId: string,
  payload: ProposeDecisionBody,
  idempotencyKey: string,
  fetchImpl: Fetch = fetch,
) {
  const body = await apiRequest(
    casePath(caseId, 'decisions'),
    { body: payload, idempotencyKey },
    fetchImpl,
  );
  return parseDecision(body.data, caseId);
}

export async function reviewDecision(
  caseId: string,
  decisionId: string,
  payload: ReviewDecisionBody,
  idempotencyKey: string,
  fetchImpl: Fetch = fetch,
) {
  requireId(decisionId, 'decision');
  const path = casePath(caseId, `decisions/${encodeURIComponent(decisionId)}/review`);
  const body = await apiRequest(path, { body: payload, idempotencyKey }, fetchImpl);
  return parseDecision(body.data, caseId);
}

export async function requestEvidence(
  caseId: string,
  payload: RequestEvidenceBody,
  idempotencyKey: string,
  fetchImpl: Fetch = fetch,
) {
  const body = await apiRequest(
    casePath(caseId, 'request-evidence'),
    { body: payload, idempotencyKey },
    fetchImpl,
  );
  return parseEvidenceRequest(body.data, caseId);
}
