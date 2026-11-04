import frappe
from frappe import _
from frappe.model.document import Document

from vati_tax.identity import policy
from vati_tax.identity.permissions import MEMBERSHIP, clear_principal_cache


class VatiTenantMembership(Document):
    def validate(self):
        taxpayer_id = (self.get("taxpayer_id") or "").strip() or None
        self.taxpayer_id = taxpayer_id
        membership = policy.Membership(self.tenant_id, self.vati_role, taxpayer_id)
        try:
            policy.validate_membership(membership)
            if self.enabled:
                # Internal read scoped to this user: the identity rules span all
                # of the user's memberships, whoever is editing them.
                others = frappe.get_all(
                    MEMBERSHIP,
                    filters={"user": self.user, "enabled": 1, "name": ["!=", self.name or ""]},
                    fields=["tenant_id", "vati_role", "taxpayer_id"],
                )
                policy.validate_user_memberships(
                    [
                        policy.Membership(o.tenant_id, o.vati_role, o.taxpayer_id or None)
                        for o in others
                    ]
                    + [membership]
                )
        except policy.InvalidMembership as exc:
            frappe.throw(_(str(exc)))
        frappe_role = policy.FRAPPE_ROLES[self.vati_role]
        if frappe_role not in frappe.get_roles(self.user):
            frappe.throw(_("Give {0} the {1} role first.").format(self.user, frappe_role))

    def on_update(self):
        clear_principal_cache()

    def on_trash(self):
        clear_principal_cache()


def on_doctype_update():
    frappe.db.add_unique(MEMBERSHIP, ["user", "tenant_id"], constraint_name="unique_user_tenant")
