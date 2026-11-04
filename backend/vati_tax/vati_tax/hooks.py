app_name = "vati_tax"
app_title = "Vati Tax"
app_publisher = "Vati AI"
app_description = "RSL Taxathon application and taxpayer portal"
app_email = "keletso@vatiai.com"
app_license = "Proprietary"

# TAX-029: Vati roles, tenant/taxpayer isolation and document-share guard.
# Any vati_tax DocType with a tenant_id field (plus taxpayer_id for taxpayer-
# owned records) is narrowed by these wildcard hooks; see identity/permissions.py.
before_install = "vati_tax.identity.install.before_install"
before_migrate = "vati_tax.identity.install.before_migrate"
permission_query_conditions = {"*": "vati_tax.identity.permissions.tenant_query_conditions"}
has_permission = {"*": "vati_tax.identity.permissions.tenant_has_permission"}
doc_events = {"DocShare": {"validate": "vati_tax.identity.permissions.validate_share"}}

# TAX-033: deliver committed outbox events every minute. Handlers are registered
# per event type with `vati_outbox_handlers = {"<type>": "dotted.path"}`.
scheduler_events = {"cron": {"* * * * *": ["vati_tax.outbox.worker.dispatch_scheduled"]}}

# TAX-021: the Vue taxpayer portal owns client routes under /taxpayer; every
# deep link renders the guarded www/taxpayer page.
website_route_rules = [
    {"from_route": "/taxpayer/<path:app_path>", "to_route": "taxpayer"},
]
