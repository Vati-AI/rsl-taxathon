import { createRouter, createWebHistory, type Router, type RouterHistory } from 'vue-router';
import { PORTAL_ROOT } from './lib/portal-path';
import { ensureSession, redirectToLogin, simulatorEnabled, type Navigator } from './lib/session';

export const routes = [
  // TAX-125 (ADR-043): the portal is where taxpayers answer RSL's evidence requests.
  { path: '/', redirect: { name: 'evidence-requests' } },
  {
    path: '/submissions',
    name: 'submissions',
    component: () => import('./pages/SubmissionsPage.vue'),
    meta: { title: 'My cases' },
  },
  {
    // The fixture-only Online Refunds simulator (the former TAX-043 claim form). Absent
    // unless the site enables it; claims are filed in RSL's Online Refunds Application.
    path: '/submissions/new',
    name: 'new-submission',
    component: () => import('./pages/NewSubmissionPage.vue'),
    meta: { title: 'Online Refunds simulator', simulator: true },
  },
  {
    path: '/submissions/:id',
    name: 'submission',
    component: () => import('./pages/SubmissionPage.vue'),
    props: true,
    meta: { title: 'Case status' },
  },
  {
    path: '/requests',
    name: 'evidence-requests',
    component: () => import('./pages/EvidenceRequestsPage.vue'),
    meta: { title: 'Evidence requests' },
  },
  {
    path: '/requests/:id',
    name: 'evidence-request',
    component: () => import('./pages/EvidenceRequestPage.vue'),
    props: true,
    meta: { title: 'Evidence request' },
  },
  {
    path: '/account',
    name: 'account',
    component: () => import('./pages/AccountPage.vue'),
    meta: { title: 'Account' },
  },
  {
    path: '/about/notices',
    name: 'notices',
    component: () => import('./pages/NoticesPage.vue'),
    meta: { title: 'Open-source notices' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('./pages/NotFoundPage.vue'),
    meta: { title: 'Page not found' },
  },
];

export function createPortalRouter(
  history: RouterHistory = createWebHistory(PORTAL_ROOT),
  nav: Navigator = window.location,
): Router {
  const router = createRouter({ history, routes });

  // The server page guard already rejects guests; this catches a session that
  // expires while the app is open.
  router.beforeEach(async (to) => {
    const state = await ensureSession();
    if (state.status === 'error' && state.error.kind === 'unauthenticated') {
      redirectToLogin(PORTAL_ROOT + to.fullPath, nav);
      return false;
    }
    // TAX-125: without the simulator its route does not exist for this site.
    if (to.meta.simulator === true && !simulatorEnabled(state)) {
      return {
        name: 'not-found',
        params: { pathMatch: to.path.slice(1).split('/') },
        replace: true,
      };
    }
    return true;
  });

  router.afterEach((to) => {
    const title = typeof to.meta.title === 'string' ? to.meta.title : 'Taxpayer portal';
    document.title = `${title} · Vati Taxpayer Portal`;
  });

  return router;
}
