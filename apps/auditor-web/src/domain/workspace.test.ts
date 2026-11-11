import { describe, expect, it } from 'vitest';
import { parseWorkspaceSession, type WorkspaceSession } from '../api/session';
import { makeCase } from '../testing/makeCase';
import {
  backToCases,
  commandItems,
  commandTarget,
  openCount,
  workspaceApps,
  workspaceSections,
} from './workspace';

const AUDITOR: WorkspaceSession = {
  tenant_id: '10000000-0000-4000-8000-000000000001',
  tenant_name: 'Synthetic fixture tenant',
  officer_id: '80000000-0000-4000-8000-000000000001',
  display_name: 'Synthetic auditor',
  roles: ['auditor'],
  apps: [
    { key: 'cases', availability: 'available', reason: null },
    { key: 'investigation', availability: 'unavailable', reason: 'not_connected' },
    { key: 'explore', availability: 'unavailable', reason: 'not_connected' },
  ],
  views: ['overview', 'case_queue'],
  synthetic: true,
};
const SUPERVISOR: WorkspaceSession = {
  ...AUDITOR,
  roles: ['supervisor'],
  apps: [...AUDITOR.apps, { key: 'reports', availability: 'available', reason: null }],
  views: ['overview', 'case_queue', 'management', 'integrity'],
};

describe('workspace navigation (TAX-146)', () => {
  it('shows only the apps the session lists and marks unconnected ones', () => {
    const apps = workspaceApps(AUDITOR, 'queue', null);
    expect(apps.map((a) => a.label)).toEqual(['Cases', 'Investigation', 'Explore']);
    expect(apps.map((a) => a.current)).toEqual([true, false, false]);
    expect(apps.map((a) => a.unavailable)).toEqual([undefined, 'Not connected', 'Not connected']);
    expect(apps[1].to).toEqual({ name: 'native-app', params: { app: 'investigation' } });
    const onTool = workspaceApps(AUDITOR, 'native-app', 'explore');
    expect(onTool.map((a) => a.current)).toEqual([false, false, true]);
  });

  it('adds Reports and the supervision views only for a session that has them', () => {
    expect(workspaceSections(AUDITOR).map((s) => s.label)).toEqual(['Cases']);
    const sections = workspaceSections(SUPERVISOR, { queue: 7 });
    expect(sections.map((s) => s.label)).toEqual(['Cases', 'Reports']);
    expect(sections[0].items.find((i) => i.label === 'My case queue')?.count).toBe(7);
    expect(sections[1].items.map((i) => i.label)).toEqual([
      'Management dashboard',
      'Integrity review',
    ]);
    const reports = workspaceApps(SUPERVISOR, 'integrity', null).find((a) => a.label === 'Reports');
    expect(reports).toMatchObject({ current: true, to: { name: 'management' } });
  });

  it('offers "Back to cases" only inside a case', () => {
    expect(backToCases(AUDITOR, 'case')?.label).toBe('Back to cases');
    expect(backToCases(AUDITOR, 'taxpayer')).toBeDefined();
    expect(backToCases(AUDITOR, 'queue')).toBeUndefined();
    expect(backToCases({ ...AUDITOR, views: ['overview'] }, 'case')).toBeUndefined();
  });

  it('counts unresolved cases for the queue', () => {
    expect(openCount([makeCase(1), makeCase(2, { status: 'RESOLVED' })])).toBe(1);
  });

  it('palette offers permitted pages, the officer’s cases and masked TINs', () => {
    const c = makeCase(2);
    const items = commandItems(
      AUDITOR,
      [c],
      [
        {
          taxpayer_id: c.taxpayer_id,
          tenant_id: AUDITOR.tenant_id,
          display_name: 'Synthetic Border Retail',
          taxpayer_type: 'company',
          tin_masked: '******4102',
        } as never,
      ],
    );
    expect(items.filter((i) => i.group === 'Pages').map((i) => i.label)).toEqual([
      'Overview',
      'My case queue',
    ]);
    const taxpayer = items.find((i) => i.group === 'Taxpayers')!;
    expect(taxpayer.hint).toBe('TIN ******4102');
    expect(taxpayer.keywords).toContain('4102');
    expect(commandTarget(taxpayer)).toEqual({
      name: 'taxpayer',
      params: { taxpayerId: c.taxpayer_id },
    });
    const kase = items.find((i) => i.group === 'Cases')!;
    expect(commandTarget(kase)).toEqual({ name: 'case', params: { caseId: c.case_id } });
    expect(commandTarget({ id: 'page:queue', label: 'x', group: 'Pages' })).toEqual({
      name: 'queue',
    });
    expect(commandTarget({ id: 'weird', label: 'x', group: 'Pages' })).toBeNull();
    // A supervisor's palette includes the supervision pages; an auditor's does not.
    expect(commandItems(SUPERVISOR, [], []).map((i) => i.label)).toContain('Integrity review');
    expect(items.map((i) => i.label)).not.toContain('Management dashboard');
  });
});

describe('parseWorkspaceSession', () => {
  it('accepts the contract shape and refuses anything else', () => {
    expect(parseWorkspaceSession(SUPERVISOR).roles).toEqual(['supervisor']);
    for (const bad of [
      null,
      { ...AUDITOR, roles: ['root'] },
      { ...AUDITOR, apps: [{ key: 'desk', availability: 'available', reason: null }] },
      { ...AUDITOR, views: ['everything'] },
      { ...AUDITOR, tenant_id: 'not-a-uuid' },
      { ...AUDITOR, synthetic: 'yes' },
    ]) {
      expect(() => parseWorkspaceSession(bad)).toThrow();
    }
  });
});
