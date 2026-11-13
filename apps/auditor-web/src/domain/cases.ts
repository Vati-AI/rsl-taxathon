import type { Case, CaseStatus, RefundLane, ServiceLink, TaxpayerType } from '../api/vati';
import { compareDecimal } from './money';

export type BadgeTheme = 'gray' | 'blue' | 'green' | 'amber' | 'red' | 'violet';

// Status is a projection of the Jube workflow. Colours mark where a case is in
// processing; none of them is a verdict about the taxpayer.
const STATUSES: Record<CaseStatus, { label: string; theme: BadgeTheme }> = {
  RECEIVED: { label: 'Received', theme: 'gray' },
  VALIDATING: { label: 'Validating', theme: 'gray' },
  INVESTIGATING: { label: 'Investigating', theme: 'blue' },
  AWAITING_EVIDENCE: { label: 'Awaiting evidence', theme: 'amber' },
  AWAITING_HUMAN: { label: 'Awaiting officer review', theme: 'violet' },
  RESOLVED: { label: 'Resolved', theme: 'green' },
  FAILED_PROCESSING: { label: 'Processing failed', theme: 'red' },
};

const LANES: Record<RefundLane, string> = {
  fast: 'Fast',
  standard: 'Standard',
  targeted: 'Targeted',
};

const TAXPAYER_TYPES: Record<TaxpayerType, string> = {
  individual: 'Individual',
  company: 'Company',
  partnership: 'Partnership',
  trust: 'Trust',
  other: 'Other entity',
};

const SERVICES: Record<string, string> = {
  frappe: 'Vati records',
  jube: 'Investigation',
  docling: 'Document reading',
  openaleph: 'Explore',
  yente: 'Name screening',
  langgraph: 'Vati assistant',
  // RSL systems in the reference register (TAX-133).
  online_refunds: 'RSL Online Refunds',
  audit_system: 'RSL audit system',
  psrm: 'RSL PSRM',
  etax: 'RSL e-Tax',
  idms: 'RSL Lekuka (IDMS)',
  asycuda: 'RSL ASYCUDA',
};

const SYNC_ERRORS: Record<string, string> = {
  upstream_timeout: 'timed out',
  upstream_unavailable: 'was unavailable',
};

export function formatKey(key: string): string {
  const text = key.replaceAll('_', ' ').trim();
  return text ? text[0].toUpperCase() + text.slice(1) : 'Unknown';
}

export function statusInfo(status: CaseStatus) {
  return STATUSES[status] ?? { label: formatKey(status), theme: 'gray' as BadgeTheme };
}

// RSL systems (RslServiceName, TAX-133). A reason from one of them is RSL's own (TAX-131).
const RSL_SERVICES = new Set(['online_refunds', 'audit_system', 'psrm', 'etax', 'idms', 'asycuda']);

export function isRslService(service: string): boolean {
  return RSL_SERVICES.has(service);
}

/** Who a selection reason belongs to: RSL's own input, or a Vati corroborating signal. */
export function reasonSourceLabel(source: string): string {
  return isRslService(source)
    ? `RSL's input · ${serviceLabel(source)}`
    : `Vati signal from ${serviceLabel(source)}`;
}

export function laneLabel(lane: RefundLane | null): string {
  return lane ? LANES[lane] : 'Not set';
}

export function taxpayerTypeLabel(type: TaxpayerType): string {
  return TAXPAYER_TYPES[type] ?? formatKey(type);
}

export function serviceLabel(service: string): string {
  return SERVICES[service] ?? formatKey(service);
}

export interface ProcessingState {
  kind: 'ok' | 'pending' | 'failed';
  /** Short, user-facing explanation; empty when processing is complete. */
  text: string;
  failures: ServiceLink[];
}

/** Summarise processing from the case status and its service sync states. */
export function processingState(item: Case): ProcessingState {
  const failures = item.service_links.filter((link) => link.sync.state === 'failed');
  const pending = item.service_links.filter((link) => link.sync.state === 'pending');
  if (failures.length || item.status === 'FAILED_PROCESSING') {
    const text = failures.length
      ? failures
          .map((link) => {
            const what = SYNC_ERRORS[link.sync.error_code ?? ''] ?? 'failed';
            return `${serviceLabel(link.service)} ${what}`;
          })
          .join('; ')
      : 'Processing failed';
    return { kind: 'failed', text, failures };
  }
  if (pending.length || item.status === 'RECEIVED' || item.status === 'VALIDATING') {
    const text = pending.length
      ? `Waiting for ${pending.map((link) => serviceLabel(link.service)).join(', ')}`
      : 'Processing';
    return { kind: 'pending', text, failures: [] };
  }
  return { kind: 'ok', text: '', failures: [] };
}

export type SortKey =
  'display_id' | 'taxpayer' | 'status' | 'refund_lane' | 'refund_claim' | 'updated_at';
export type SortDirection = 'asc' | 'desc';

export interface QueueQuery {
  search: string;
  status: string;
  lane: string;
  sort: SortKey;
  direction: SortDirection;
  page: number;
  pageSize: number;
}

export interface QueuePage {
  rows: Case[];
  total: number;
  page: number;
  pageCount: number;
}

function sortValue(item: Case, key: SortKey): string {
  switch (key) {
    case 'taxpayer':
      return item.taxpayer.display_name;
    case 'status':
      return statusInfo(item.status).label;
    case 'refund_lane':
      return item.refund_lane ?? '';
    case 'updated_at':
      return item.updated_at;
    default:
      return item.display_id;
  }
}

export function queuePage(cases: Case[], query: QueueQuery): QueuePage {
  const needle = query.search.trim().toLocaleLowerCase();
  const filtered = cases.filter(
    (item) =>
      (!needle ||
        item.display_id.toLocaleLowerCase().includes(needle) ||
        item.taxpayer.display_name.toLocaleLowerCase().includes(needle)) &&
      (!query.status || item.status === query.status) &&
      (!query.lane ||
        (query.lane === 'none' ? item.refund_lane === null : item.refund_lane === query.lane)),
  );
  const factor = query.direction === 'asc' ? 1 : -1;
  const sorted = [...filtered].sort((a, b) => {
    if (query.sort === 'refund_claim') {
      const left = a.refund_claim?.amount ?? null;
      const right = b.refund_claim?.amount ?? null;
      // Missing amounts stay last whatever the direction.
      if (left === null || right === null) return compareDecimal(left, right);
      return factor * compareDecimal(left, right);
    }
    const left = sortValue(a, query.sort);
    const right = sortValue(b, query.sort);
    if (query.sort === 'refund_lane' && (!left || !right))
      return left === right ? 0 : left ? -1 : 1;
    return factor * left.localeCompare(right, undefined, { numeric: true });
  });
  const pageCount = Math.max(1, Math.ceil(sorted.length / query.pageSize));
  const page = Math.min(Math.max(1, query.page), pageCount);
  const start = (page - 1) * query.pageSize;
  return {
    rows: sorted.slice(start, start + query.pageSize),
    total: sorted.length,
    page,
    pageCount,
  };
}

/** Counts per status in display order, omitting statuses with no cases. */
export function statusCounts(cases: Case[]) {
  return (Object.keys(STATUSES) as CaseStatus[])
    .map((status) => ({
      status,
      ...statusInfo(status),
      count: cases.filter((item) => item.status === status).length,
    }))
    .filter((entry) => entry.count > 0);
}
