# Frappe custom app

This is an installable package skeleton, not a configured site or complete DocType model. TAX-019 installs it in an isolated bench; TAX-029 adds verified roles/tenant permissions. Do not load it into existing HR/helpdesk/production sites.

Inside the pinned Frappe development container after creating a compatible bench:

```sh
# Mount this directory at /workspace/vati_tax first.
bench get-app /workspace/vati_tax
bench --site taxathon.localhost install-app vati_tax
bench --site taxathon.localhost migrate
```

Use the official development guide under `external/frappe/docs` to initialize the bench and site, with generated local credentials. Keep the chosen Frappe framework revision in the deployment record; the Docker tooling lock is not a framework version lock. The starter app currently declares no DocTypes and has no guest-access API.
