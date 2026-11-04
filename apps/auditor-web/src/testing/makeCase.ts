import type { Case } from '../api/vati';

/** Build a valid synthetic contract Case for tests; override only what a test cares about. */
export function makeCase(n: number, overrides: Partial<Case> = {}): Case {
  const id = `40000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
  const taxpayerId = `20000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
  return {
    case_id: id,
    tenant_id: '10000000-0000-4000-8000-000000000001',
    display_id: `SYN-${String(n).padStart(3, '0')}`,
    taxpayer_id: taxpayerId,
    taxpayer: {
      taxpayer_id: taxpayerId,
      display_name: `Synthetic Taxpayer ${n}`,
      taxpayer_type: 'company',
    },
    status: 'AWAITING_HUMAN',
    version: 1,
    refund_claim: { amount: '1000.00', currency: 'LSL' },
    refund_lane: 'standard',
    assigned_officer_id: '80000000-0000-4000-8000-000000000001',
    selection_reasons: [
      { code: 'refund_above_usual_pattern', summary: 'Refund larger than usual.', source: 'jube' },
    ],
    service_links: [
      {
        service: 'jube',
        external_type: 'case',
        external_id: `syn-jube-${n}`,
        sync: { state: 'synced', last_attempt_at: '2026-11-03T10:00:00Z', error_code: null },
      },
    ],
    created_at: '2026-11-03T10:00:00Z',
    updated_at: '2026-11-03T10:00:00Z',
    synthetic: true,
    ...overrides,
  };
}
