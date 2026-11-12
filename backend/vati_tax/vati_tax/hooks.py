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
has_permission = {
    "*": "vati_tax.identity.permissions.tenant_has_permission",
    # TAX-032: evidence originals cannot be edited or deleted through File.
    "File": "vati_tax.evidence.store.file_has_permission",
}
doc_events = {
    "DocShare": {"validate": "vati_tax.identity.permissions.validate_share"},
    # TAX-032: an evidence file stays private and in place.
    "File": {"before_validate": "vati_tax.evidence.store.guard_evidence_file"},
    # TAX-121 (ADR-042): a sent refund claim first enqueues assurance.requested, so the
    # assurance checks never wait for Jube. TAX-039: the send registers the case and
    # enqueues case.registered. TAX-087: then the claim's evidence is linked to the case.
    "Vati Submission": {
        "on_update": [
            "vati_tax.assurance.store.on_submission_sent",
            "vati_tax.bridge.store.on_submission_update",
            "vati_tax.pipeline.store.on_submission_sent",
        ]
    },
    # TAX-087: evidence linked to a case enqueues evidence.received (Docling extraction).
    "Vati Service Link": {"after_insert": "vati_tax.pipeline.store.on_service_link_insert"},
    # TAX-124 (ADR-048): officer-approved results queue outbound messages to RSL.
    "Vati Decision": {"on_update": "vati_tax.rsl_outbound.store.on_decision_update"},
    "Vati Evidence Request": {
        "after_insert": "vati_tax.rsl_outbound.store.on_evidence_request_insert"
    },
}

# TAX-033: deliver committed outbox events every minute. Handlers are registered
# per event type with `vati_outbox_handlers = {"<type>": "dotted.path"}`.
# TAX-039: Jube sends no webhooks, so open cases are polled every five minutes.
scheduler_events = {
    "cron": {
        "* * * * *": ["vati_tax.outbox.worker.dispatch_scheduled"],
        "*/5 * * * *": ["vati_tax.bridge.store.poll_scheduled"],
    }
}
vati_outbox_handlers = {
    "case.registered": "vati_tax.bridge.store.handle_case_registered",
    # TAX-087 (ADR-037): Docling extraction, then OpenAleph entities and yente screening.
    "evidence.received": "vati_tax.pipeline.store.handle_evidence_received",
    "extraction.completed": "vati_tax.pipeline.store.handle_extraction_settled",
    "extraction.failed": "vati_tax.pipeline.store.handle_extraction_settled",
    # TAX-121 (ADR-042): TAX-112-116 checks over the RSL source fixture and Frappe records.
    "assurance.requested": "vati_tax.assurance.store.handle_assurance_requested",
    # TAX-124 (ADR-048): findings pack, lane recommendation and evidence notice to RSL.
    "rsl_outbound.requested": "vati_tax.rsl_outbound.store.handle_rsl_outbound",
}

# TAX-021: the Vue taxpayer portal owns client routes under /taxpayer; every
# deep link renders the guarded www/taxpayer page.
website_route_rules = [
    {"from_route": "/taxpayer/<path:app_path>", "to_route": "taxpayer"},
]
