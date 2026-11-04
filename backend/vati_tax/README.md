# Frappe custom app

This is an installable package skeleton, not a configured site or complete DocType model. TAX-019 installs it in an isolated bench; TAX-029 adds roles and tenant permissions. Do not load it into existing HR/helpdesk/production sites.

Install and verify it with the repository tooling, never by hand on a shared site:

```sh
python3 scripts/frappe_dev.py init     # pinned Frappe v16.54.0 bench, taxathon.localhost site, editable vati_tax
python3 scripts/frappe_dev.py verify   # new disposable site: install, migrate twice, import, app tests, drop
```

See [docs/FRAPPE_DEV.md](../../docs/FRAPPE_DEV.md) for running a second checkout with its own project/ports.

Package layout:

- `vati_tax/hooks.py` declares the app; `modules.txt` declares the `Vati Tax` module (package `vati_tax/vati_tax`), where DocTypes go.
- `vati_tax/patches.txt` uses Frappe's `[pre_model_sync]` / `[post_model_sync]` sections. Add a dotted path plus a test for every data migration; never edit site databases manually.
- `pyproject.toml` ships `*.txt`, JSON, HTML and `public/` assets with the package.
- `vati_tax/api.py` exposes only an authenticated scaffold health method. `vati_tax/portal` holds the TAX-021 taxpayer portal boundary and its Frappe-free unit tests.
- `vati_tax/identity` (TAX-029) holds roles, tenant memberships, service identities and permissions: `policy.py` (pure rules and the role/action matrix), `permissions.py` (Frappe hooks, `require_action`), `provision.py` (operator helpers) and the `Vati Tenant` / `Vati Tenant Membership` DocTypes. Any DocType in this app with a `tenant_id` field (plus `taxpayer_id` for taxpayer-owned records) is tenant-isolated automatically; see [docs/PERMISSIONS.md](../../docs/PERMISSIONS.md).
- `vati_tax/ids` (TAX-030) holds canonical case IDs and service links. `canonical.py` has the pure rules, `mapping.py` the internal persistence API (`register_case`, `record_link`, `apply_link_event`, `resolve_case`, `case_service_links`), and the `Vati Case Identity` / `Vati Service Link` DocTypes store them ([ADR-006](../../docs/decisions/ADR-006-canonical-ids-service-links.md)).

Tests named `test_*.py` must pass under plain `unittest` without a site. Site-backed tests skip themselves there and run on the disposable `verify` site via `bench run-tests --app vati_tax`.

TAX-031 to TAX-033 add submissions, evidence and the outbox; give their DocTypes `tenant_id`/`taxpayer_id` fields and role permissions, and add negative tests.
