import { mount, RouterLinkStub } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/errors';
import { makeCase } from '../testing/makeCase';
import AmountText from './AmountText.vue';
import CaseTable from './CaseTable.vue';
import ProcessingIndicator from './ProcessingIndicator.vue';
import SelectionReasons from './SelectionReasons.vue';
import StatePanel from './StatePanel.vue';
import { errorPanel } from './errorPanel';

const row = makeCase(2, {
  status: 'INVESTIGATING',
  refund_claim: null,
  refund_lane: 'targeted',
  selection_reasons: [
    { code: 'related_party_charges', summary: 'Fees paid to a related company.', source: 'jube' },
    { code: 'persistent_reported_losses', summary: 'Losses for several periods.', source: 'jube' },
  ],
});

describe('AmountText', () => {
  it('shows exact amounts and labels missing values instead of zero', () => {
    expect(mount(AmountText, { props: { value: '25000.00', currency: 'LSL' } }).text()).toBe(
      'LSL 25,000.00',
    );
    expect(mount(AmountText, { props: { value: null, currency: 'LSL' } }).text()).toBe(
      'Not provided',
    );
  });
});

describe('StatePanel', () => {
  it('announces errors and denials as alerts and other states as status', () => {
    for (const [kind, role] of [
      ['error', 'alert'],
      ['denied', 'alert'],
      ['empty', 'status'],
      ['loading', 'status'],
    ] as const) {
      expect(mount(StatePanel, { props: { kind, title: 'x' } }).attributes('role')).toBe(role);
    }
  });

  it('offers retry only when retryable and shows the reference to quote', async () => {
    const plain = mount(StatePanel, { props: { kind: 'error', title: 'x' } });
    expect(plain.find('button').exists()).toBe(false);
    const retry = mount(StatePanel, {
      props: { kind: 'error', title: 'x', retryable: true, reference: 'ref-123' },
    });
    expect(retry.text()).toContain('Reference: ref-123');
    await retry.get('button').trigger('click');
    expect(retry.emitted('retry')).toHaveLength(1);
  });
});

describe('errorPanel', () => {
  it('does not offer retry for denied access or missing records', () => {
    const denied = new ApiError('denied', 'x', { status: 403, reference: 'r' });
    expect(errorPanel(denied)).toMatchObject({ kind: 'denied', retryable: false, reference: 'r' });
    const missing = new ApiError('not_found', 'x', { status: 404 });
    expect(errorPanel(missing, 'taxpayer')).toMatchObject({
      kind: 'not_found',
      title: 'Taxpayer not found',
      retryable: false,
    });
    expect(errorPanel(new ApiError('unavailable', 'x'))).toMatchObject({
      kind: 'error',
      retryable: true,
    });
  });
});

describe('SelectionReasons', () => {
  it('summarises in tables and lists every reason with its source in full', () => {
    const compact = mount(SelectionReasons, {
      props: { reasons: row.selection_reasons, compact: true },
    });
    expect(compact.text()).toContain('Fees paid to a related company.');
    expect(compact.text()).toContain('+1 more reason');
    const full = mount(SelectionReasons, { props: { reasons: row.selection_reasons } });
    expect(full.findAll('li')).toHaveLength(2);
    expect(full.text()).toContain('Signal from Investigation');
  });

  it('says when risk evaluation has not run instead of showing nothing', () => {
    expect(mount(SelectionReasons, { props: { reasons: [] } }).text()).toBe('Not evaluated yet');
  });
});

describe('ProcessingIndicator', () => {
  it('names the failed service in text, not only colour', () => {
    const failed = makeCase(6, {
      status: 'FAILED_PROCESSING',
      service_links: [
        {
          service: 'docling',
          external_type: 'extraction',
          external_id: null,
          sync: { state: 'failed', last_attempt_at: null, error_code: 'upstream_timeout' },
        },
      ],
    });
    expect(mount(ProcessingIndicator, { props: { item: failed } }).text()).toBe(
      'Document reading timed out',
    );
    expect(mount(ProcessingIndicator, { props: { item: makeCase(1) } }).text()).toBe('Up to date');
  });
});

describe('CaseTable', () => {
  const mountTable = () =>
    mount(CaseTable, {
      props: { rows: [row], caption: 'Cases', sort: 'refund_claim', direction: 'desc' },
      global: { stubs: { RouterLink: RouterLinkStub } },
    });

  it('exposes sort state only on sortable headers', () => {
    const headers = mountTable().findAll('th');
    expect(headers.map((h) => h.attributes('aria-sort'))).toEqual([
      'none',
      'none',
      undefined,
      'none',
      'none',
      'descending',
      undefined,
    ]);
  });

  it('emits the column key when a header button is activated', async () => {
    const table = mountTable();
    await table.findAll('th button')[0].trigger('click');
    expect(table.emitted('sort')).toEqual([['display_id']]);
  });

  it('links each case by its internal ID and shows readable labels', () => {
    const table = mountTable();
    expect(table.findComponent(RouterLinkStub).props('to')).toEqual({
      name: 'case',
      params: { caseId: row.case_id },
    });
    expect(table.text()).toContain('Investigating');
    expect(table.text()).toContain('Targeted');
    expect(table.text()).toContain('Not provided');
    expect(table.text()).toContain('Fees paid to a related company.');
  });
});
