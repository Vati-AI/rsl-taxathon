import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';

declare module 'vue-router' {
  interface RouteMeta {
    /** Optional on a nested route: the parent's meta is merged in (TAX-148 case tabs). */
    section?: 'overview' | 'cases' | 'management' | 'integrity' | 'showcase' | 'none';
    title?: string;
    /** Case tab that shows one kind of case records (relationships or findings). */
    records?: 'relationships' | 'findings';
  }
}

/** Case tabs addressed by `?tab=` before TAX-148, and the route each one is now. */
const LEGACY_TABS: Record<string, string> = {
  overview: 'case',
  evidence: 'case-evidence',
  relationships: 'case-relationships',
  findings: 'case-findings',
};

export const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'overview',
    component: () => import('./views/OverviewView.vue'),
    meta: { section: 'overview', title: 'Overview' },
  },
  {
    path: '/cases',
    name: 'queue',
    component: () => import('./views/CaseQueueView.vue'),
    meta: { section: 'cases', title: 'My case queue' },
  },
  {
    // TAX-148: one case workspace. The parent loads the case and its records once and
    // shows the header, key facts and routed tabs; each tab is its own URL.
    path: '/cases/:caseId',
    component: () => import('./views/CaseWorkspaceView.vue'),
    props: true,
    meta: { section: 'cases' },
    children: [
      {
        path: '',
        name: 'case',
        component: () => import('./views/case/CaseSummaryTab.vue'),
        meta: { title: 'Case' },
        // Links from before TAX-148 named the tab in the query (?tab=evidence).
        beforeEnter: (to) => {
          const tab = typeof to.query.tab === 'string' ? LEGACY_TABS[to.query.tab] : undefined;
          if (!tab) return true;
          const { tab: _tab, ...query } = to.query;
          void _tab;
          return { name: tab, params: to.params, query, replace: true };
        },
      },
      {
        path: 'evidence',
        name: 'case-evidence',
        component: () => import('./views/case/CaseEvidenceTab.vue'),
        meta: { title: 'Evidence' },
      },
      {
        path: 'evidence/:evidenceId',
        name: 'evidence',
        component: () => import('./views/case/CaseEvidenceTab.vue'),
        meta: { title: 'Evidence' },
      },
      {
        path: 'relationships',
        name: 'case-relationships',
        component: () => import('./views/case/CaseRecordsTab.vue'),
        meta: { title: 'Relationships', records: 'relationships' },
      },
      {
        path: 'findings',
        name: 'case-findings',
        component: () => import('./views/case/CaseRecordsTab.vue'),
        meta: { title: 'Findings', records: 'findings' },
      },
      {
        path: 'review',
        name: 'case-review',
        component: () => import('./views/case/CaseReviewTab.vue'),
        meta: { title: 'Review and decide' },
      },
    ],
  },
  {
    path: '/taxpayers/:taxpayerId',
    name: 'taxpayer',
    component: () => import('./views/TaxpayerView.vue'),
    props: true,
    meta: { section: 'cases', title: 'Taxpayer' },
  },
  {
    path: '/management',
    name: 'management',
    component: () => import('./views/ManagementView.vue'),
    meta: { section: 'management', title: 'Management dashboard' },
  },
  {
    // TAX-077: restricted policy-gap and integrity aggregates (supervisors only).
    path: '/management/integrity',
    name: 'integrity',
    component: () => import('./views/IntegrityView.vue'),
    meta: { section: 'integrity', title: 'Integrity review' },
  },
  {
    // TAX-146: a workspace app whose native screens are not connected yet.
    path: '/apps/:app',
    name: 'native-app',
    component: () => import('./views/NativeAppView.vue'),
    props: true,
    meta: { section: 'none', title: 'Not connected' },
  },
  {
    path: '/about/notices',
    name: 'notices',
    component: () => import('./views/NoticesView.vue'),
    meta: { section: 'none', title: 'Open-source notices' },
  },
  {
    path: '/showcase',
    name: 'showcase',
    component: () => import('./views/ShowcaseView.vue'),
    meta: { section: 'showcase', title: 'Component showcase' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('./views/NotFoundView.vue'),
    meta: { section: 'none', title: 'Page not found' },
  },
];

export const router = createRouter({
  history: createWebHistory('/auditor/'),
  routes,
});

router.afterEach((to) => {
  document.title = `${to.meta.title ?? 'Vati Workspace'} · Vati Workspace · Cases`;
});
