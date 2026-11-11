/**
 * Behaviour tests for the shared Vati Workspace kit (TAX-145, apps/shared/components).
 * The kit lives outside both apps; these tests run under the auditor's vitest, and the
 * taxpayer app compiles the same files in its own build and type check.
 */
import { flushPromises, mount, RouterLinkStub } from '@vue/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import {
  AppSidebar,
  CommandPalette,
  DataTable,
  EmptyState,
  InlineError,
  KeyFacts,
  NoticePanel,
  PageHeader,
  RoutedTabs,
  SideDrawer,
  StatusChip,
  TONES,
  UsageLine,
  WorkspaceHeader,
  type DataColumn,
} from '../../../shared/components';

const stubs = { RouterLink: RouterLinkStub };

const columns: DataColumn[] = [
  { key: 'ref', label: 'Case', sortable: true },
  { key: 'amount', label: 'Amount', sortable: true, numeric: true, info: 'The amount claimed.' },
];
const rows = Array.from({ length: 30 }, (_, i) => ({
  ref: `C-${String(i + 1).padStart(2, '0')}`,
  amount: i === 4 ? null : String(1000 - i),
}));

afterEach(() => {
  document.body.innerHTML = '';
});

describe('DataTable', () => {
  it('pages at 25 rows inside a labelled, focusable scroll region', async () => {
    const w = mount(DataTable, { props: { label: 'Cases', columns, rows, rowKey: 'ref' } });
    const region = w.get('[role="region"]');
    expect(region.attributes('aria-label')).toBe('Cases');
    expect(region.attributes('tabindex')).toBe('0');
    expect(w.findAll('tbody tr')).toHaveLength(25);
    expect(w.text()).toContain('Rows 1–25 of 30 · Page 1 of 2');
    const prev = w.findAll('button').find((b) => b.text() === 'Previous page')!;
    expect(prev.attributes('disabled')).toBeDefined();
    await w
      .findAll('button')
      .find((b) => b.text() === 'Next page')!
      .trigger('click');
    expect(w.emitted('update:page')?.at(-1)).toEqual([2]);
  });

  it('sorts by a column, reports aria-sort and keeps empty values last', async () => {
    const w = mount(DataTable, {
      props: {
        label: 'Cases',
        columns,
        rows,
        rowKey: 'ref',
        sort: null,
        'onUpdate:sort': (s: unknown) => w.setProps({ sort: s as never }),
      },
    });
    const amountHeader = () => w.findAll('th')[1];
    expect(amountHeader().attributes('aria-sort')).toBe('none');
    await amountHeader().get('button').trigger('click');
    expect(amountHeader().attributes('aria-sort')).toBe('ascending');
    expect(w.findAll('tbody tr')[0].text()).toContain('971');
    await amountHeader().get('button').trigger('click');
    expect(amountHeader().attributes('aria-sort')).toBe('descending');
    expect(w.findAll('tbody tr')[0].text()).toContain('1000');
    // C-05 has no amount: it sorts after the values in both directions.
    await w.setProps({ pageSize: 50 });
    expect(w.findAll('tbody tr').at(-1)!.text()).toContain('C-05');
  });

  it('shows a muted dash read as "Not available" for an empty value', () => {
    const w = mount(DataTable, { props: { label: 'Cases', columns, rows, rowKey: 'ref' } });
    const cell = w.findAll('tbody tr')[4].findAll('td')[1];
    expect(cell.text()).toContain('—');
    expect(cell.get('.vati-sr-only').text()).toBe('Not available');
  });

  it('names the row action with the row and emits the row', async () => {
    const w = mount(DataTable, {
      props: { label: 'Cases', columns, rows, rowKey: 'ref', rowAction: 'Open' },
    });
    const action = w.findAll('tbody tr')[0].get('td:last-child button');
    expect(action.text()).toBe('Open C-01');
    await action.trigger('click');
    expect(w.emitted('row-action')?.[0]).toEqual([rows[0]]);
  });

  it('has loading, error with retry and empty states instead of rows', async () => {
    const loading = mount(DataTable, {
      props: { label: 'Cases', columns, rows: [], rowKey: 'ref', loading: true },
    });
    expect(loading.get('[role="status"]').text()).toContain('Loading cases');
    expect(loading.find('table').exists()).toBe(false);

    const error = mount(DataTable, {
      props: {
        label: 'Cases',
        columns,
        rows: [],
        rowKey: 'ref',
        error: 'Cases could not be loaded',
      },
    });
    expect(error.get('[role="alert"]').text()).toContain('Cases could not be loaded');
    await error.get('button').trigger('click');
    expect(error.emitted('retry')).toHaveLength(1);

    const empty = mount(DataTable, {
      props: { label: 'Cases', columns, rows: [], rowKey: 'ref' },
      slots: { empty: '<p>No cases match</p>' },
    });
    expect(empty.text()).toBe('No cases match');
  });
});

describe('status and feedback', () => {
  it('a status chip always shows its words, whatever the tone', () => {
    for (const tone of TONES) {
      const w = mount(StatusChip, { props: { label: `Status ${tone}`, tone } });
      expect(w.text()).toBe(`Status ${tone}`);
      expect(w.attributes('data-tone')).toBe(tone);
    }
  });

  it('a notice collapses its recommendations and can be dismissed', async () => {
    const w = mount(NoticePanel, {
      props: { title: 'Two invoices unmatched', recommendations: ['Request invoices', 'Check'] },
    });
    const toggle = w.get('button[aria-expanded]');
    expect(toggle.text()).toBe('Recommendations (2)');
    expect(toggle.attributes('aria-expanded')).toBe('false');
    await toggle.trigger('click');
    expect(toggle.attributes('aria-expanded')).toBe('true');
    expect(w.get(`#${toggle.attributes('aria-controls')}`).findAll('li')).toHaveLength(2);

    const d = mount(NoticePanel, { props: { title: 'Note', dismissable: true } });
    await d.get('button[aria-label="Dismiss notice"]').trigger('click');
    expect(d.find('[data-kit="notice"]').exists()).toBe(false);
    expect(d.emitted('dismiss')).toHaveLength(1);
    // Danger interrupts; other tones are polite status.
    expect(mount(NoticePanel, { props: { title: 'x', tone: 'danger' } }).attributes('role')).toBe(
      'alert',
    );
  });

  it('an inline error says what failed and offers retry only when it helps', async () => {
    const w = mount(InlineError, {
      props: { title: 'Evidence could not be loaded', reference: 'r-1' },
    });
    expect(w.attributes('role')).toBe('alert');
    expect(w.text()).toContain('Reference: r-1');
    await w.get('button').trigger('click');
    expect(w.emitted('retry')).toHaveLength(1);
    expect(
      mount(InlineError, { props: { title: 'No access', retryable: false } })
        .find('button')
        .exists(),
    ).toBe(false);
  });

  it('usage line and key facts say the real counts and mark missing values', () => {
    const usage = mount(UsageLine, {
      props: { shown: 25, total: 143, noun: 'cases', extras: ['3 overdue'] },
    });
    expect(usage.text()).toBe('Showing 25 of 143 cases · 3 overdue');
    const facts = mount(KeyFacts, {
      props: {
        facts: [
          { label: 'Claimed', value: 'LSL 10.00' },
          { label: 'Officer', value: null },
        ],
      },
    });
    expect(facts.findAll('dd')[1].text()).toContain('Not available');
  });

  it('an empty state has a heading, one sentence and its actions', () => {
    const w = mount(EmptyState, {
      props: { title: 'No evidence yet', message: 'Ask for it.' },
      slots: { actions: '<button>Request evidence</button>' },
    });
    expect(w.get('h2').text()).toBe('No evidence yet');
    expect(w.text()).toContain('Request evidence');
  });
});

describe('page structure and navigation', () => {
  it('the page header has one heading at the requested level', () => {
    const page = mount(PageHeader, { props: { title: 'My cases', description: 'Work in hand.' } });
    expect(page.get('h1#page-title').text()).toBe('My cases');
    const example = mount(PageHeader, { props: { title: 'Example', level: 3, headingId: 'x' } });
    expect(example.find('h1').exists()).toBe(false);
    expect(example.get('h3#x').text()).toBe('Example');
  });

  it('routed tabs and sidebar mark only the current item', () => {
    const tabs = mount(RoutedTabs, {
      global: { stubs },
      props: {
        label: 'Case sections',
        current: 'case-evidence',
        tabs: [
          { label: 'Summary', to: '/s', match: 'case-summary' },
          { label: 'Evidence', to: '/e', match: ['case-evidence', 'evidence-record'], count: 7 },
        ],
      },
    });
    const links = tabs.findAllComponents(RouterLinkStub);
    expect(links.map((l) => l.attributes('aria-current'))).toEqual([undefined, 'page']);

    const nav = mount(AppSidebar, {
      global: { stubs },
      props: {
        label: 'Cases navigation',
        current: 'queue',
        sections: [
          {
            label: 'Cases',
            items: [{ label: 'My cases', to: '/q', icon: 'x', match: 'queue', count: 12 }],
          },
        ],
      },
    });
    expect(nav.get('nav').attributes('aria-label')).toBe('Cases navigation');
    expect(nav.getComponent(RouterLinkStub).attributes('aria-current')).toBe('page');
  });

  it('the sidebar collapses to an icon rail and keeps item names for screen readers', async () => {
    const w = mount(AppSidebar, {
      global: { stubs },
      props: {
        label: 'Nav',
        current: null,
        collapsed: false,
        'onUpdate:collapsed': (v: boolean) => w.setProps({ collapsed: v }),
        sections: [
          { items: [{ label: 'My cases', to: '/q', icon: 'x', match: 'queue', count: 3 }] },
        ],
      },
    });
    const toggle = w.get('button[aria-expanded]');
    await toggle.trigger('click');
    expect(w.get('nav').attributes('data-collapsed')).toBe('true');
    expect(w.get('.vati-sr-only').text()).toBe('My cases');
    expect(w.get('button[aria-expanded]').attributes('aria-label')).toBe('Expand navigation');
  });

  it('the workspace header names the product, marks the current app and shows fixture mode slimly', async () => {
    const w = mount(WorkspaceHeader, {
      global: { stubs },
      props: {
        fixture: true,
        apps: [
          { label: 'Cases', href: '/auditor/', current: true },
          { label: 'Explore', href: '/explore/' },
        ],
      },
    });
    expect(w.text()).toContain('Vati Workspace');
    expect(w.findAll('nav a').map((a) => a.attributes('aria-current'))).toEqual([
      'page',
      undefined,
    ]);
    expect(w.get('[data-kit="fixture-strip"]').text()).toBe('Synthetic data');
    await w.get('button[aria-keyshortcuts]').trigger('click');
    expect(w.emitted('search')).toHaveLength(1);
  });
});

describe('overlays', () => {
  it('the drawer is a labelled modal dialog that closes from its close button', async () => {
    const w = mount(SideDrawer, {
      attachTo: document.body,
      props: {
        open: true,
        title: 'Request evidence',
        'onUpdate:open': (v: boolean) => w.setProps({ open: v }),
      },
      slots: { default: '<p>Form</p>' },
    });
    await flushPromises();
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog).not.toBeNull();
    const titleId = dialog.getAttribute('aria-labelledby')!;
    expect(document.getElementById(titleId)?.textContent).toContain('Request evidence');
    (document.querySelector('button[aria-label="Close"]') as HTMLButtonElement).click();
    await flushPromises();
    expect(w.emitted('update:open')?.at(-1)).toEqual([false]);
    w.unmount();
  });

  it('the command palette filters by references and TINs and chooses with the keyboard', async () => {
    const w = mount(CommandPalette, {
      attachTo: document.body,
      props: {
        open: true,
        shortcut: false,
        'onUpdate:open': (v: boolean) => w.setProps({ open: v }),
        items: [
          { id: '1', label: 'DEMO-201', group: 'Cases', keywords: ['400000201'] },
          { id: '2', label: 'DEMO-202', group: 'Cases', keywords: ['400000202'] },
          { id: '3', label: 'Management', group: 'Pages' },
        ],
      },
    });
    await flushPromises();
    const input = document.querySelector('input[role="combobox"]') as HTMLInputElement;
    expect(document.querySelectorAll('[role="option"]')).toHaveLength(3);
    input.value = '400000202';
    input.dispatchEvent(new Event('input'));
    await flushPromises();
    const options = document.querySelectorAll('[role="option"]');
    expect(options).toHaveLength(1);
    expect(input.getAttribute('aria-activedescendant')).toBe(options[0].id);
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await flushPromises();
    expect(w.emitted('select')?.[0]?.[0]).toMatchObject({ id: '2' });
    expect(w.emitted('update:open')?.at(-1)).toEqual([false]);
    w.unmount();
  });
});
