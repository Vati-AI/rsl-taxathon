import { DOMWrapper, flushPromises, mount, type VueWrapper } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { makeCase } from '../testing/makeCase';
import DecisionPanel from './DecisionPanel.vue';

const ITEM = makeCase(3, { status: 'AWAITING_HUMAN' });
const CASE = ITEM.case_id;
const ME = '80000000-0000-4000-8000-000000000001';
const FINDING = {
  finding_id: '70000000-0000-4000-8000-000000000301',
  case_id: CASE,
  tenant_id: ITEM.tenant_id,
  kind: 'inference',
  category: 'worker_classification',
  explanation: 'Payslips and invoices overlap for five people.',
  evidence_refs: [],
  unresolved_amount: null,
  confidence: 'low',
  alternative_explanations: [],
  legal_route: { status: 'unverified', citation: null, verified_by: null },
  proposed_action: { type: 'specialist_review', requires_human_approval: true },
  producer: 'langgraph_run',
  producer_version: 'x@1',
  created_at: '2026-11-03T10:00:00Z',
};

function decision(proposedBy: string) {
  return {
    decision_id: 'd0000000-0000-4000-8000-000000000003',
    case_id: CASE,
    tenant_id: ITEM.tenant_id,
    status: 'proposed',
    origin: 'officer',
    outcome: 'refer_to_specialist',
    operation: 'escalate',
    target_status: 'AWAITING_HUMAN',
    rationale: 'A specialist should review the worker classification.',
    evidence_ids: [],
    finding_ids: [FINDING.finding_id],
    rating: 3,
    draft_id: null,
    proposed_by: proposedBy,
    proposed_at: '2026-11-04T10:00:00Z',
    case_status_at_proposal: 'AWAITING_HUMAN',
    case_version: 6,
    reviewed_by: null,
    reviewed_at: null,
    review_reason: null,
    jube_sync: {
      state: 'not_started',
      attempts: 0,
      error_code: null,
      retryable: false,
      last_attempt_at: null,
      synced_at: null,
      changed: null,
      status_after: null,
      status_authority: 'jube',
    },
  };
}

interface Server {
  allowed: string[];
  roles: string[];
  open: ReturnType<typeof decision> | null;
  /** Responses for successive POSTs: [status, body]. */
  posts: [number, unknown][];
}

let server: Server;
let calls: { url: string; init?: RequestInit }[];

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function errorBody(code: string, reason: string) {
  return {
    error: {
      code,
      message: 'refused',
      retryable: false,
      correlation_id: 'c0000000-0000-4000-8000-000000000001',
      field_errors: [{ field: 'expected_version', code: reason }],
    },
  };
}

beforeEach(() => {
  server = {
    allowed: ['decision.propose', 'specialist.refer', 'evidence_request.authorise'],
    roles: ['auditor'],
    open: null,
    posts: [],
  };
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (init?.method === 'POST') {
        const [status, body] = server.posts.shift() ?? [500, {}];
        return json(status, body);
      }
      if (url.includes('/findings'))
        return json(200, { data: [FINDING], meta: { next_cursor: null, has_more: false } });
      return json(200, {
        data: {
          case_id: CASE,
          tenant_id: ITEM.tenant_id,
          case_version: 6,
          viewer: { officer_id: ME, roles: server.roles, allowed_actions: server.allowed },
          open_decision: server.open,
          audit_events: [],
        },
      });
    }),
  );
});

let wrapper: VueWrapper | null = null;
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

async function mountPanel(item = ITEM) {
  wrapper = mount(DecisionPanel, { props: { item }, attachTo: document.body });
  await flushPromises();
  return wrapper;
}

const posts = () => calls.filter((c) => c.init?.method === 'POST');
/** The forms open in a drawer portalled to the body (TAX-148), so query the document. */
const find = (selector: string) => {
  const el = document.body.querySelector(selector);
  if (!el) throw new Error(`No element matches ${selector}`);
  return new DOMWrapper(el);
};
async function openDrawer(label: string) {
  button(label)!.click();
  await flushPromises();
}
const button = (label: string) =>
  [...document.body.querySelectorAll('button')].find((b) => b.textContent?.trim() === label);

async function fillReferral() {
  await openDrawer('Propose a decision…');
  await find('input[value="refer_to_specialist"]').setValue(true);
  await find('[data-kit="drawer"] textarea').setValue(
    'A specialist should review the worker classification.',
  );
  await find('[data-kit="drawer"] input[type="checkbox"]').setValue(true);
  await find('form[aria-label="Propose a decision"]').trigger('submit');
  await flushPromises();
}

describe('DecisionPanel', () => {
  it('fills the form from a suggestion without sending anything', async () => {
    await mountPanel();
    button('Use as a starting point')!.click();
    await flushPromises();
    // The suggestion opens the propose drawer with the outcome filled in.
    expect(document.body.querySelector('[data-kit="drawer"]')?.textContent).toContain(
      'Propose a decision',
    );
    expect((find('input[value="refer_to_specialist"]').element as HTMLInputElement).checked).toBe(
      true,
    );
    expect(posts()).toHaveLength(0);
  });

  it('asks for confirmation, sends once, and retries with the same Idempotency-Key', async () => {
    server.posts = [
      [503, errorBody('upstream_unavailable', 'jube_unavailable')],
      [201, { data: decision(ME) }],
    ];
    const panel = await mountPanel();
    await fillReferral();
    expect(document.body.textContent).toContain('Confirm the proposed decision');
    expect(posts()).toHaveLength(0);

    const confirm = button('Propose decision')!;
    confirm.click();
    confirm.click(); // a second click while sending is ignored
    await flushPromises();
    expect(posts()).toHaveLength(1);
    expect(document.body.textContent).toContain('The case service is unavailable');

    button('Try again')!.click();
    await flushPromises();
    const [first, second] = posts();
    const key = (c: { init?: RequestInit }) =>
      (c.init?.headers as Record<string, string>)['Idempotency-Key'];
    expect(key(first)).toBe(key(second));
    expect(JSON.parse(second.init?.body as string)).toMatchObject({
      outcome: 'refer_to_specialist',
      expected_version: 6,
      expected_case_status: 'AWAITING_HUMAN',
      finding_ids: [FINDING.finding_id],
    });
    expect(panel.text()).toContain('Decision proposed');
  });

  it('refuses a stale view, offers a reload and keeps the officer text', async () => {
    server.posts = [[409, errorBody('version_conflict', 'stale_case_version')]];
    await mountPanel();
    await fillReferral();
    button('Propose decision')!.click();
    await flushPromises();
    expect(document.body.textContent).toContain('This case changed while you were reviewing it');
    expect(button('Reload case')).toBeTruthy();
    button('Back to the form')!.click();
    await flushPromises();
    expect((find('[data-kit="drawer"] textarea').element as HTMLTextAreaElement).value).toContain(
      'specialist',
    );
  });

  it('explains missing permissions instead of offering the action', async () => {
    server.allowed = ['decision.propose'];
    server.roles = ['specialist'];
    const panel = await mountPanel();
    expect(panel.text()).toContain('Only auditors and supervisors can authorise evidence requests');
    expect(button('Request evidence…')).toBeUndefined();
    await openDrawer('Propose a decision…');
    const refer = find('input[value="refer_to_specialist"]').element as HTMLInputElement;
    expect(refer.disabled).toBe(true);
    expect(document.body.textContent).toContain('Your role cannot refer cases to a specialist.');
  });

  it('keeps its result when the parent refreshes the same case', async () => {
    server.posts = [[201, { data: decision(ME) }]];
    const panel = await mountPanel();
    await fillReferral();
    button('Propose decision')!.click();
    await flushPromises();
    expect(panel.text()).toContain('Decision proposed');
    // The drawer closes once the proposal is recorded.
    expect(document.body.querySelector('[data-kit="drawer"]')).toBeNull();
    const before = calls.length;
    await panel.setProps({ item: { ...ITEM, updated_at: '2026-11-04T12:00:00Z' } });
    await flushPromises();
    expect(panel.text()).toContain('Decision proposed');
    expect(calls).toHaveLength(before);
  });

  it('keeps proposer and approver separate', async () => {
    server.open = decision(ME);
    const panel = await mountPanel();
    expect(button('Approve…')!.disabled).toBe(true);
    expect(panel.text()).toContain('a different supervisor must approve');
    expect(button('Withdraw…')).toBeTruthy();
  });

  it('explains a blocked suggestion while another decision waits for approval', async () => {
    server.open = decision(ME);
    await mountPanel();
    const use = button('Use as a starting point')!;
    expect(use.disabled).toBe(true);
    const reason = document.getElementById(use.getAttribute('aria-describedby')!);
    expect(reason?.textContent?.trim()).toBe(
      'Another decision on this case is waiting for approval.',
    );
  });

  it('says a resolved case is resolved, not that a decision is waiting (TAX-104)', async () => {
    await mountPanel({ ...ITEM, status: 'RESOLVED' });
    const use = button('Use as a starting point')!;
    expect(use.disabled).toBe(true);
    const reason = document.getElementById(use.getAttribute('aria-describedby')!);
    expect(reason?.textContent?.trim()).toBe('This case is resolved.');
    expect(document.body.textContent).not.toContain('waiting for approval');
  });
});
