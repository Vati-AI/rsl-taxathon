import { mount, RouterLinkStub } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import type { FixtureCase } from '../api/fixtures';
import { ApiError } from '../api/fixtures';
import AmountText from './AmountText.vue';
import CaseTable from './CaseTable.vue';
import StatePanel from './StatePanel.vue';
import { errorPanel } from './errorPanel';

const row: FixtureCase = {
  id: 'SYN-002',
  name: 'Synthetic Border Retail',
  scenario: 'related_party_risk',
  currency: 'LSL',
  refund_claim: '25000.00',
  supported_refund: null,
  expected: 'targeted_evidence_request',
  synthetic: true,
};

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
    expect(mount(StatePanel, { props: { kind: 'error', title: 'x' } }).attributes('role')).toBe(
      'alert',
    );
    expect(mount(StatePanel, { props: { kind: 'denied', title: 'x' } }).attributes('role')).toBe(
      'alert',
    );
    expect(mount(StatePanel, { props: { kind: 'empty', title: 'x' } }).attributes('role')).toBe(
      'status',
    );
    expect(mount(StatePanel, { props: { kind: 'loading', title: 'x' } }).attributes('role')).toBe(
      'status',
    );
  });

  it('offers retry only when retryable', async () => {
    const plain = mount(StatePanel, { props: { kind: 'error', title: 'x' } });
    expect(plain.find('button').exists()).toBe(false);
    const retry = mount(StatePanel, { props: { kind: 'error', title: 'x', retryable: true } });
    await retry.get('button').trigger('click');
    expect(retry.emitted('retry')).toHaveLength(1);
  });
});

describe('errorPanel', () => {
  it('does not offer retry for denied access or missing cases', () => {
    expect(errorPanel(new ApiError('denied', 'x', 403))).toMatchObject({
      kind: 'denied',
      retryable: false,
    });
    expect(errorPanel(new ApiError('not_found', 'x', 404))).toMatchObject({
      kind: 'not_found',
      retryable: false,
    });
    expect(errorPanel(new ApiError('unavailable', 'x'))).toMatchObject({
      kind: 'error',
      retryable: true,
    });
  });
});

describe('CaseTable', () => {
  const mountTable = () =>
    mount(CaseTable, {
      props: { rows: [row], caption: 'Cases', sort: 'refund_claim', direction: 'desc' },
      global: { stubs: { RouterLink: RouterLinkStub } },
    });

  it('exposes sort state on column headers', () => {
    const headers = mountTable().findAll('th');
    expect(headers.map((h) => h.attributes('aria-sort'))).toEqual([
      'none',
      'none',
      'none',
      'descending',
      'none',
      'none',
    ]);
  });

  it('emits the column key when a header button is activated', async () => {
    const table = mountTable();
    await table.findAll('th button')[0].trigger('click');
    expect(table.emitted('sort')).toEqual([['id']]);
  });

  it('links each case to its detail route and shows labels, not raw keys', () => {
    const table = mountTable();
    expect(table.findComponent(RouterLinkStub).props('to')).toEqual({
      name: 'case',
      params: { id: 'SYN-002' },
    });
    expect(table.text()).toContain('Related-party charges');
    expect(table.text()).toContain('Targeted evidence request');
    expect(table.text()).toContain('Not provided');
  });
});
