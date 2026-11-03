import { createRouter, createWebHistory, type Router, type RouterHistory } from 'vue-router';
import { PORTAL_ROOT } from './lib/portal-path';
import { ensureSession, redirectToLogin, type Navigator } from './lib/session';

export const routes = [
  { path: '/', redirect: { name: 'submissions' } },
  {
    path: '/submissions',
    name: 'submissions',
    component: () => import('./pages/SubmissionsPage.vue'),
    meta: { title: 'Your submissions' },
  },
  {
    path: '/submissions/new',
    name: 'new-submission',
    component: () => import('./pages/NewSubmissionPage.vue'),
    meta: { title: 'New submission' },
  },
  {
    path: '/submissions/:id',
    name: 'submission',
    component: () => import('./pages/SubmissionPage.vue'),
    props: true,
    meta: { title: 'Submission status' },
  },
  {
    path: '/account',
    name: 'account',
    component: () => import('./pages/AccountPage.vue'),
    meta: { title: 'Account' },
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
    return true;
  });

  router.afterEach((to) => {
    const title = typeof to.meta.title === 'string' ? to.meta.title : 'Taxpayer portal';
    document.title = `${title} · Vati taxpayer portal`;
  });

  return router;
}
