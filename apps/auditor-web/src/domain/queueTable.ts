/**
 * Queue table rows, sorting and CSV export (TAX-147). A row holds plain sortable values
 * taken from one case; cells render the case itself. Empty values stay empty (shown as a
 * muted dash, exported as an empty field) and sort last in both directions.
 */
import type { DataColumn, SortDirection } from '../../../shared/components';
import type { Case } from '../api/vati';
import { laneLabel, statusInfo } from './cases';

export interface QueueRow extends Record<string, unknown> {
  case_id: string;
  display_id: string;
  taxpayer: string;
  why: string;
  status: string;
  refund_lane: string | null;
  refund_claim: string | null;
  currency: string | null;
  due: string | null;
  overdue: boolean;
  processing: string;
  updated_at: string;
}

/** Columns an officer can show or hide (Display options), and which show by default. */
export const OPTIONAL_COLUMNS = {
  why: { label: 'Why selected', default: true },
  refund_lane: { label: 'Refund lane', default: true },
  due: { label: 'Evidence due', default: true },
  processing: { label: 'Processing', default: true },
  updated_at: { label: 'Last updated', default: false },
} as const;
export type OptionalColumn = keyof typeof OPTIONAL_COLUMNS;

export function queueRows(cases: Case[]): QueueRow[] {
  return cases.map((c) => {
    const failed = c.service_links.filter((l) => l.sync.state === 'failed').length;
    const pending = c.service_links.filter((l) => l.sync.state === 'pending').length;
    return {
      case_id: c.case_id,
      display_id: c.display_id,
      taxpayer: c.taxpayer.display_name,
      why: c.selection_reasons.map((r) => r.summary).join('; '),
      status: statusInfo(c.status).label,
      refund_lane: c.refund_lane ? laneLabel(c.refund_lane) : null,
      refund_claim: c.refund_claim?.amount ?? null,
      currency: c.refund_claim?.currency ?? null,
      due: c.attention?.evidence_due_date ?? null,
      overdue: Boolean(c.attention?.overdue),
      processing: failed ? 'Failed' : pending ? 'Waiting' : 'Up to date',
      updated_at: c.updated_at,
    };
  });
}

function empty(value: unknown): boolean {
  return value === null || value === undefined || value === '';
}

/** Same order as the kit DataTable: numbers numerically, text naturally, empty last. */
export function compareRows(
  a: QueueRow,
  b: QueueRow,
  sort: { key: string; direction: SortDirection },
): number {
  const x = a[sort.key];
  const y = b[sort.key];
  if (empty(x) && empty(y)) return 0;
  if (empty(x)) return 1;
  if (empty(y)) return -1;
  const factor = sort.direction === 'asc' ? 1 : -1;
  const nx = Number(x);
  const ny = Number(y);
  if (!Number.isNaN(nx) && !Number.isNaN(ny)) return factor * (nx - ny);
  return factor * String(x).localeCompare(String(y), undefined, { numeric: true });
}

function csvField(value: unknown): string {
  if (empty(value)) return '';
  const text = String(value);
  // Quote when needed; neutralise a leading formula character for spreadsheet safety.
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return /[",\n\r]/.test(safe) ? `"${safe.replaceAll('"', '""')}"` : safe;
}

/** CSV of the given rows with exactly the visible columns, as plain values. */
export function queueCsv(rows: QueueRow[], columns: DataColumn[]): string {
  const cols = columns.map((c) => c.key);
  const header = columns.map((c) => csvField(c.key === 'refund_claim' ? `${c.label} (amount)` : c.label));
  const extra = cols.includes('refund_claim') ? ['Currency'] : [];
  const lines = [[...header, ...extra].join(',')];
  for (const row of rows) {
    const values = cols.map((k) => csvField(k === 'due' && row.overdue ? `${row.due} (overdue)` : row[k]));
    if (extra.length) values.push(csvField(row.currency));
    lines.push(values.join(','));
  }
  return lines.join('\r\n') + '\r\n';
} // prettier-ignore
