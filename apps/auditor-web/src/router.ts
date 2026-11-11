import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';

declare module 'vue-router' {
  interface RouteMeta {
    section: 'overview' | 'cases' | 'management' | 'integrity' | 'showcase' | 'none';
    title: string;
  }
}

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
    path: '/cases/:caseId',
    name: 'case',
    component: () => import('./views/CaseDetailView.vue'),
    props: true,
    meta: { section: 'cases', title: 'Case' },
  },
  {
    path: '/cases/:caseId/evidence/:evidenceId',
    name: 'evidence',
    component: () => import('./views/EvidenceView.vue'),
    props: true,
    meta: { section: 'cases', title: 'Evidence' },
  },
  {
    path: '/cases/:caseId/review',
    name: 'case-review',
    component: () => import('./views/CaseReviewView.vue'),
    props: true,
    meta: { section: 'cases', title: 'Review and decide' },
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
  document.title = `${to.meta.title} · Vati AI auditor workspace`;
});
