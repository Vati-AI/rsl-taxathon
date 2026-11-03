# ADR-001 — Reuse Jube and Frappe as the application backbone

Status: adopted from the supplied technical summary, superseding the initial scaffold's generic-backend authority and optional-Frappe assumption.

Jube is operational case/risk authority. Frappe is the application and taxpayer portal foundation, with custom Vati DocTypes for tax records, evidence requests and reviewed decisions. Vati is an integration/API layer, not a replacement for their mature functions.

Consequences: store cross-system mappings and source versions; do not maintain an independent case workflow in PostgreSQL. Approval records precede idempotent calls into Jube. A failed Jube transition is pending/failed synchronization, not a completed decision application. Validate actual upstream APIs before adapters are implemented.
