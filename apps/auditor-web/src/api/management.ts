/**
 * getManagementSummary (GET /api/v1/management/summary, TAX-060): measured
 * workload and assurance figures for supervisors. The server refuses anyone
 * else with 403, which the view shows as a denied state with no figures.
 * Records not marked synthetic are refused, as for every other read.
 */
import { apiRequest, CASE_STATUSES, type CaseStatus } from './vati';
import { ApiError } from './errors';

export interface Ratio {
  numerator: number;
  denominator: number;
}
export interface DurationStats {
  count: number;
  excluded: number;
  median_hours: string | null;
  p90_hours: string | null;
  max_hours: string | null;
}
export interface OfficerWorkload {
  officer_id: string;
  display_name: string;
  open_cases: number;
  awaiting_officer_review: number;
}
export type SignalKind =
  'repeated_overrides' | 'repeated_separation_of_duties_attempts' | 'ledger_integrity';
export const SIGNAL_KINDS: readonly SignalKind[] = [
  'repeated_overrides',
  'repeated_separation_of_duties_attempts',
  'ledger_integrity',
];
export type Assurance =
  | {
      available: true;
      ledger_intact: boolean;
      entries: number;
      overrides: number;
      refusals: number;
      distinct_actors: number;
      review_signals: { kind: SignalKind; count: number }[];
      withheld_about_viewer: number;
    }
  | { available: false; reason: string };
export type MissingMetric =
  | 'workload'
  | 'open_case_age'
  | 'time_to_close'
  | 'unresolved_evidence'
  | 'closure_outcomes'
  | 'legal_route_coverage'
  | 'assurance';

export interface ManagementSummary {
  tenant_id: string;
  generated_at: string;
  window: { since: string; until: string; days: number };
  workload: {
    open_cases: number;
    unassigned_open_cases: number;
    by_status: { status: CaseStatus; count: number }[];
    by_officer: OfficerWorkload[];
  };
  elapsed: { open_case_age: DurationStats; time_to_close: DurationStats };
  unresolved_evidence: {
    outstanding_requests: number;
    overdue_requests: number;
    cases_with_outstanding_requests: number;
    open_cases_awaiting_evidence: number;
  };
  closure_outcomes: {
    closed_in_window: number;
    without_recorded_outcome: number;
    no_further_action: Ratio;
    after_correction: Ratio;
  };
  legal_route_coverage: {
    verified_by_reviewer: Ratio;
    unverified: number;
    no_verified_route: number;
  };
  assurance: Assurance;
  missing_data: { metric: MissingMetric; detail: string }[];
  synthetic: true;
}

type Json = Record<string, unknown>;
const HOURS = /^(0|[1-9][0-9]*)\.[0-9]$/;

function isRecord(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function invalid(): never {
  throw new ApiError('invalid', 'The service returned figures that cannot be shown safely.');
}

function record(value: unknown): Json {
  return isRecord(value) ? value : invalid();
}

function count(value: unknown): number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : invalid();
}

function counts(value: unknown, keys: string[]) {
  const item = record(value);
  for (const key of keys) count(item[key]);
  return item;
}

function ratio(value: unknown): Ratio {
  const item = counts(value, ['numerator', 'denominator']);
  // A numerator larger than its denominator would show an impossible rate.
  if ((item.numerator as number) > (item.denominator as number)) invalid();
  return item as unknown as Ratio;
}

function duration(value: unknown): DurationStats {
  const item = counts(value, ['count', 'excluded']);
  for (const key of ['median_hours', 'p90_hours', 'max_hours']) {
    const hours = item[key];
    const empty = item.count === 0;
    if (empty ? hours !== null : typeof hours !== 'string' || !HOURS.test(hours)) invalid();
  }
  return item as unknown as DurationStats;
}

function assurance(value: unknown): Assurance {
  const item = record(value);
  if (item.available === false) {
    if (typeof item.reason !== 'string' || item.reason === '') invalid();
    return { available: false, reason: item.reason };
  }
  if (item.available !== true || typeof item.ledger_intact !== 'boolean') invalid();
  counts(item, ['entries', 'overrides', 'refusals', 'distinct_actors', 'withheld_about_viewer']);
  if (!Array.isArray(item.review_signals)) invalid();
  for (const signal of item.review_signals) {
    const s = record(signal);
    if (!SIGNAL_KINDS.includes(s.kind as SignalKind) || count(s.count) < 1) invalid();
  }
  return item as unknown as Assurance;
}

/** Validate every figure the view shows; reject non-synthetic or inconsistent data. */
export function parseManagementSummary(value: unknown): ManagementSummary {
  const data = record(value);
  if (data.synthetic !== true) invalid();
  const window = counts(data.window, ['days']);
  if (typeof window.since !== 'string' || typeof window.until !== 'string') invalid();
  if (typeof data.generated_at !== 'string') invalid();

  const work = counts(data.workload, ['open_cases', 'unassigned_open_cases']);
  if (!Array.isArray(work.by_status) || !Array.isArray(work.by_officer)) invalid();
  let byStatus = 0;
  let awaitingEvidence = 0;
  for (const row of work.by_status) {
    const r = counts(row, ['count']);
    if (!CASE_STATUSES.includes(r.status as CaseStatus)) invalid();
    byStatus += r.count as number;
    if (r.status === 'AWAITING_EVIDENCE') awaitingEvidence += r.count as number;
  }
  let assigned = 0;
  for (const row of work.by_officer) {
    const r = counts(row, ['open_cases', 'awaiting_officer_review']);
    if (typeof r.officer_id !== 'string' || typeof r.display_name !== 'string') invalid();
    assigned += r.open_cases as number;
  }
  // Breakdowns must add up to the total they break down.
  const open = work.open_cases as number;
  if (byStatus !== open || assigned + (work.unassigned_open_cases as number) !== open) invalid();

  const elapsed = record(data.elapsed);
  duration(elapsed.open_case_age);
  duration(elapsed.time_to_close);
  const evidence = counts(data.unresolved_evidence, [
    'outstanding_requests',
    'overdue_requests',
    'cases_with_outstanding_requests',
    'open_cases_awaiting_evidence',
  ]);
  const outstanding = evidence.outstanding_requests as number;
  if (
    evidence.open_cases_awaiting_evidence !== awaitingEvidence ||
    (evidence.overdue_requests as number) > outstanding ||
    (evidence.cases_with_outstanding_requests as number) > outstanding
  )
    invalid();
  const closures = counts(data.closure_outcomes, ['closed_in_window', 'without_recorded_outcome']);
  const nfa = ratio(closures.no_further_action);
  const corrected = ratio(closures.after_correction);
  const recorded =
    (closures.closed_in_window as number) - (closures.without_recorded_outcome as number);
  if (nfa.denominator !== recorded || corrected.denominator !== recorded) invalid();
  const routes = counts(data.legal_route_coverage, ['unverified', 'no_verified_route']);
  const verified = ratio(routes.verified_by_reviewer);
  const findings =
    verified.numerator + (routes.unverified as number) + (routes.no_verified_route as number);
  if (findings !== verified.denominator) invalid();
  assurance(data.assurance);
  if (!Array.isArray(data.missing_data)) invalid();
  for (const gap of data.missing_data) {
    const g = record(gap);
    if (typeof g.metric !== 'string' || typeof g.detail !== 'string') invalid();
  }
  return data as unknown as ManagementSummary;
}

export const WINDOW_DAYS = [7, 30, 90] as const;
export type WindowDays = (typeof WINDOW_DAYS)[number];

export async function getManagementSummary(
  windowDays: WindowDays,
  fetchImpl: typeof fetch = fetch,
  signal?: AbortSignal,
) {
  const body = await apiRequest(
    '/api/v1/management/summary',
    { query: { window_days: String(windowDays) }, signal },
    fetchImpl,
  );
  return parseManagementSummary(body.data);
}
