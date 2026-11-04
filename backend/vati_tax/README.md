# Frappe custom app

This is an installable package skeleton, not a configured site or complete DocType model. TAX-019 installs it in an isolated bench; TAX-029 adds verified roles/tenant permissions. Do not load it into existing HR/helpdesk/production sites.

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

The app declares no DocTypes yet. TAX-029 adds roles and tenant permissions; TAX-030 to TAX-033 add IDs, submissions, evidence and the outbox.
