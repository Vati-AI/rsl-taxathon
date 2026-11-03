/**
 * Request context for the Vati API. In fixture mode the tenant is the synthetic
 * fixture tenant (integration/api/fixtures/auditor_queue.json). The header only
 * selects a tenant; the server checks it against the signed-in principal
 * (TAX-029), so this value never grants access by itself.
 */
export const FIXTURE_TENANT_ID = '10000000-0000-4000-8000-000000000001';

export const session = {
  tenantId: (import.meta.env.VITE_VATI_TENANT_ID as string | undefined) || FIXTURE_TENANT_ID,
};
