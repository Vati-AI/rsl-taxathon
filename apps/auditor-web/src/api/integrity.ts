/**
 * getIntegrityReview (GET /api/v1/management/integrity, TAX-077, ADR-031):
 * restricted policy-gap and integrity aggregates for supervisors. The server
 * refuses anyone else with 403, which the view shows as a denied state.
 *
 * The parser enforces the privacy and wording contract on the client too: it
 * refuses non-synthetic data, totals that do not add up, rows that would show a
 * combined total without its combined row, and anything that looks like a
 * person's login, so a server mistake can never put an identity on screen.
 */
import { apiRequest } from './vati';
import { ApiError } from './errors';

export interface IntegrityRow {
  key: string;
  label: string;
  count: number;
  cases: number;
}
export interface CombinedRow extends IntegrityRow {
  key: 'combined';
  groups_combined: number;
}
export type IntegritySignalKind =
  | 'repeated_overrides'
  | 'repeated_separation_of_duties_attempts'
  | 'decision_overturned'
  | 'ledger_integrity';
export const INTEGRITY_SIGNAL_KINDS: readonly IntegritySignalKind[] = [
  'repeated_overrides',
  'repeated_separation_of_duties_attempts',
  'decision_overturned',
  'ledger_integrity',
];

export type PolicyExposure =
  | {
      status: 'available';
      note: string;
      confirmed_gaps: number;
      pending_review: number;
      areas: IntegrityRow[];
      combined: CombinedRow | null;
    }
  | {
      status: 'insufficient_volume';
      note: string;
      confirmed_gaps: null;
      pending_review: number;
      areas: [];
      combined: null;
    };
export type OverridePatterns =
  | {
      status: 'available';
      note: string;
      total_overrides: number;
      by_control: IntegrityRow[];
      combined: CombinedRow | null;
    }
  | {
      status: 'insufficient_volume';
      note: string;
      total_overrides: null;
      by_control: [];
      combined: null;
    }
  | { status: 'unavailable' };
export type ReviewSignals =
  | {
      status: 'review_only';
      note: string;
      by_kind: { kind: IntegritySignalKind; signals: number }[];
      withheld_about_viewer: number;
    }
  | { status: 'unavailable' };

export interface IntegrityReview {
  tenant_id: string;
  generated_at: string;
  window: { since: string; until: string; days: number };
  min_group_size: number;
  privacy_note: string;
  policy_exposure: PolicyExposure;
  override_patterns: OverridePatterns;
  review_signals: ReviewSignals;
  ledger: { available: true; intact: boolean } | { available: false };
  missing_data: { metric: 'policy_exposure' | 'integrity'; detail: string }[];
  synthetic: true;
}

type Json = Record<string, unknown>;
// An e-mail-like login anywhere in the payload means an identity leaked.
const LOGIN = /[^\s@]+@[^\s@]+\.[^\s@]+/;

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

function text(value: unknown): string {
  return typeof value === 'string' && value !== '' ? value : invalid();
}

function rows(value: unknown): IntegrityRow[] {
  if (!Array.isArray(value)) invalid();
  return value.map((item) => {
    const row = record(item);
    text(row.key);
    text(row.label);
    count(row.count);
    count(row.cases);
    if (row.key === 'combined') invalid();
    return row as unknown as IntegrityRow;
  });
}

function combined(value: unknown): CombinedRow | null {
  if (value === null) return null;
  const row = record(value);
  if (row.key !== 'combined') invalid();
  text(row.label);
  count(row.count);
  count(row.cases);
  if (count(row.groups_combined) < 1) invalid();
  return row as unknown as CombinedRow;
}

/** Shared rules for a suppressed table: totals add up; insufficient volume carries no counts. */
function table(item: Json, totalKey: string, rowsKey: string) {
  text(item.note);
  if (item.status === 'insufficient_volume') {
    if (item[totalKey] !== null || item.combined !== null) invalid();
    if (!Array.isArray(item[rowsKey]) || (item[rowsKey] as unknown[]).length) invalid();
    return;
  }
  if (item.status !== 'available') invalid();
  const total = count(item[totalKey]);
  const shown = rows(item[rowsKey]);
  const rest = combined(item.combined);
  const sum = shown.reduce((acc, row) => acc + row.count, 0) + (rest?.count ?? 0);
  if (sum !== total) invalid();
}

export function parseIntegrityReview(value: unknown): IntegrityReview {
  const data = record(value);
  if (data.synthetic !== true) invalid();
  if (LOGIN.test(JSON.stringify(data))) invalid();
  const window = record(data.window);
  if (typeof window.since !== 'string' || typeof window.until !== 'string') invalid();
  count(window.days);
  text(data.generated_at);
  text(data.privacy_note);
  if (count(data.min_group_size) < 2) invalid();

  const exposure = record(data.policy_exposure);
  count(exposure.pending_review);
  table(exposure, 'confirmed_gaps', 'areas');

  const overrides = record(data.override_patterns);
  if (overrides.status !== 'unavailable') table(overrides, 'total_overrides', 'by_control');

  const signals = record(data.review_signals);
  if (signals.status !== 'unavailable') {
    if (signals.status !== 'review_only') invalid();
    text(signals.note);
    count(signals.withheld_about_viewer);
    if (!Array.isArray(signals.by_kind)) invalid();
    for (const entry of signals.by_kind) {
      const s = record(entry);
      if (!INTEGRITY_SIGNAL_KINDS.includes(s.kind as IntegritySignalKind)) invalid();
      if (count(s.signals) < 1) invalid();
      // Subjects are never part of the aggregate.
      if ('subject_actor' in s || 'event_ids' in s) invalid();
    }
  }

  const ledger = record(data.ledger);
  if (ledger.available === true) {
    if (typeof ledger.intact !== 'boolean') invalid();
  } else if (ledger.available !== false) invalid();
  if (!Array.isArray(data.missing_data)) invalid();
  for (const gap of data.missing_data) {
    const g = record(gap);
    text(g.metric);
    text(g.detail);
  }
  return data as unknown as IntegrityReview;
}

export const INTEGRITY_WINDOW_DAYS = [7, 30, 90] as const;
export type IntegrityWindowDays = (typeof INTEGRITY_WINDOW_DAYS)[number];

export async function getIntegrityReview(
  windowDays: IntegrityWindowDays,
  fetchImpl: typeof fetch = fetch,
  signal?: AbortSignal,
) {
  const body = await apiRequest(
    '/api/v1/management/integrity',
    { query: { window_days: String(windowDays) }, signal },
    fetchImpl,
  );
  return parseIntegrityReview(body.data);
}
