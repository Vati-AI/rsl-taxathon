/**
 * Request context for the Vati API. In fixture mode the tenant is the synthetic
 * fixture tenant (integration/api/fixtures/auditor_queue.json). The header only
 * selects a tenant; the server checks it against the signed-in principal
 * (TAX-029), so this value never grants access by itself.
 */
import { ref } from 'vue';

export const FIXTURE_TENANT_ID = '10000000-0000-4000-8000-000000000001';

export const session = {
  tenantId: (import.meta.env.VITE_VATI_TENANT_ID as string | undefined) || FIXTURE_TENANT_ID,
};

/**
 * Fixture mode only: which synthetic officer the fixture server acts as, sent in
 * the fixture-only X-Vati-Fixture-Role header (scripts/fixture_review.py). A real
 * server ignores the header and resolves the officer from authentication, so it
 * can never raise anyone's permissions.
 */
export const FIXTURE_ROLES = ['auditor', 'specialist', 'supervisor'] as const;
export type FixtureRole = (typeof FIXTURE_ROLES)[number];
const ROLE_KEY = 'vati.fixtureRole';

function storedRole(): FixtureRole {
  try {
    const value = globalThis.localStorage?.getItem(ROLE_KEY);
    return FIXTURE_ROLES.includes(value as FixtureRole) ? (value as FixtureRole) : 'auditor';
  } catch {
    return 'auditor';
  }
}

export const fixtureRole = ref<FixtureRole>(storedRole());

export function setFixtureRole(role: FixtureRole) {
  fixtureRole.value = role;
  try {
    globalThis.localStorage?.setItem(ROLE_KEY, role);
  } catch {
    // Storage can be unavailable (private windows); the choice then lasts for this page.
  }
}
