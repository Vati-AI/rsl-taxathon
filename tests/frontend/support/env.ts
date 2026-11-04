// Dedicated loopback ports so the harness never reuses a developer's running
// fixture server (:58080), auditor dev server (:58081), taxpayer dev server
// (:58082) or Frappe site (:58000).
export const FIXTURE_API_PORT = Number(process.env.FRONTEND_E2E_FIXTURE_PORT ?? 58092);
export const AUDITOR_PORT = Number(process.env.FRONTEND_E2E_AUDITOR_PORT ?? 58093);
export const TAXPAYER_PORT = Number(process.env.FRONTEND_E2E_TAXPAYER_PORT ?? 58094);

export const AUDITOR = `http://127.0.0.1:${AUDITOR_PORT}`;
export const TAXPAYER = `http://127.0.0.1:${TAXPAYER_PORT}`;
