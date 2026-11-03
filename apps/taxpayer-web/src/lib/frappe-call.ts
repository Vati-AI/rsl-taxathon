/**
 * The single place the portal talks to Frappe's `call` helper. Tests mock this
 * module rather than `frappe-ui` itself, because frappe-ui's Vite barrel-import
 * plugin rewrites `frappe-ui` imports to deep paths during dev and test runs.
 */
export { call } from 'frappe-ui';
