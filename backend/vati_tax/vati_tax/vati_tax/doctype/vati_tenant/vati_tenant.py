import frappe
from frappe import _
from frappe.model.document import Document

from vati_tax.identity.permissions import clear_principal_cache
from vati_tax.identity.policy import is_stored_uuid


class VatiTenant(Document):
    def validate(self):
        if not is_stored_uuid(self.tenant_id):
            frappe.throw(_("Tenant ID must be a lower-case UUID."))

    def on_update(self):
        clear_principal_cache()

    def on_trash(self):
        # Frappe's link check refuses deletion while memberships reference it.
        clear_principal_cache()
