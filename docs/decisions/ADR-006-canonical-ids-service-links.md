# ADR-006: Canonical case IDs and service-link persistence

Status: proposed (TAX-030, pending human review)
Owner and task: Edga (owner), TAX-030
Date: 2026-11-04

Context and smallest prototype need: Every component must follow one shared `case_id`, and Vati must map it to the Jube case, Frappe records and OpenAleph collections/entities without creating a second case lifecycle (ADR-001). DATA_MODEL.md asks for a `service_links` table unique on (tenant_id, service, external_id) with tenant-scoped constraints. ADR-003 fixed the `ServiceLink` contract shape. No Vati API server or database exists yet, while the Frappe app has a verified install and migration path (TAX-019).

Decision:

- Store the mapping in two `vati_tax` DocTypes, created only through the internal Python API `vati_tax.ids.mapping`. Pure rules live in `vati_tax.ids.canonical` and are unit-tested without a bench. When the Vati API server lands it calls this API or takes over the same tables in a migration. It does not get a second copy.
- `Vati Case Identity` holds `case_id` (UUIDv4, the document name, immutable), `tenant_id`, `display_id` (unique per tenant; generated from the `CASE-.######` naming series if omitted) and optional taxpayer/submission UUIDs. It has no status field, because Jube is the status authority.
- `Vati Service Link` maps a case to one upstream record (`service`, `external_type`, `external_id`, plus the contract's sync fields). Identity fields are immutable; only sync fields change.
- Uniqueness is enforced by unique DB columns holding SHA-256 keys, because Frappe has no declarative composite unique constraint:
  - `link_key` = (tenant, service, namespace, external_id). The namespace is the service itself, except for Frappe, where document names are only unique per DocType, so the namespace is the DocType. This keeps the DATA_MODEL rule for Jube/OpenAleph and stops `SUB-1` in two DocTypes from colliding.
  - `slot_key` = (tenant, case, service, external_type), only for one-per-case types: a Jube `case` and an OpenAleph `collection`. OpenAleph entities/documents, Frappe records and the other service types may repeat.
  - `event_key` = (tenant, source event_id). Replaying an event is a no-op. Reusing its ID for a different mapping is `idempotency_conflict`.
  - Case `display_key` (tenant, display_id) and `registration_key` (tenant, idempotency key) give per-tenant display uniqueness and idempotent registration.
- Error codes reuse the ADR-003 `ErrorCode` set; the HTTP contract is unchanged. An external ID already mapped elsewhere, or a taken one-per-case slot, is `invalid_domain_input` (422) with a `field_errors` reason (`already_mapped`, `slot_taken`). Reusing an event or registration key is `idempotency_conflict` (422). A case in another tenant is reported as `not_found` (404), the same as a missing case, so callers cannot probe other tenants. Internally it is a distinct `TenantBoundaryError`.
- Callers pass the tenant they have already authorised (TAX-029). Every lookup and write is scoped to it. The DocTypes are read-only in desk (System Manager read/report/export, no create/write). Service inserts use `ignore_permissions`, and only inside `vati_tax.ids.mapping`.

Alternatives and tradeoff:

- Raw SQL composite unique index in a patch: this is the exact constraint, but it lives outside DocType metadata, and `bench migrate` would not recreate it on a fresh site without extra patch logic. Hash columns are declarative and portable. The cost is three opaque 64-character columns.
- Vati-side PostgreSQL table: matches "Vati stores cross-service links", but no Vati server or migration tooling exists yet. It would add a second persistence stack for the prototype.
- Global (cross-tenant) uniqueness of upstream IDs: stricter if Jube/OpenAleph are shared instances, but it contradicts DATA_MODEL.md. This ADR keeps per-tenant uniqueness. Cross-tenant references to cases are still rejected.
- External ID reservations (`external_id: null` while an upstream create is pending, which the contract allows): deferred to TAX-033/TAX-034, which own delivery state.

Official sources and version/date checked (2026-11-04): Frappe Framework v16.54.0 (pinned in `integration/frappe-framework.lock.json`). The behaviour relied on was observed on that version in the tests below, not only read from documentation. A `unique` Data field creates a DB unique index. A violating `Document.insert` raises `frappe.UniqueValidationError`. A Link field rejects a missing target before `validate`. Frappe DocType docs: https://docs.frappe.io/framework/user/en/basics/doctypes.

Verification performed: `python3 scripts/frappe_dev.py verify --project rsl-taxathon-frappe-tax019 --http-port 58010 --socketio-port 59010` on a disposable site installed the app and migrated twice. It ran 13 site-backed tests (`vati_tax.ids.test_mapping_bench`): persistence of Jube, Frappe and OpenAleph links; event replay creating one row; event-ID reuse conflict; conflicting mapping and slot rejection; DB unique enforcement on a direct insert; immutability; tenant-boundary rejection on write, read and resolve; desk permissions. It also ran 36 Frappe-free tests. The site was dropped afterwards.

Consequences and revisit trigger: TAX-031/032 link their records through `record_link` (`service: frappe`, `external_type` = DocType). TAX-033/034/036 use `apply_link_event`/`resolve_case`. Revisit when the Vati API server is built (move or front these tables), when an upstream is shared across tenants (consider global uniqueness), or when pending reservations are needed.
