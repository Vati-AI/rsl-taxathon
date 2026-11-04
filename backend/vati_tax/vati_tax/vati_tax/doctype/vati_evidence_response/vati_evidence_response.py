"""A taxpayer's structured response to one evidence request (TAX-045).

Written only through ``vati_tax.responses.store``, after it has checked that the
request belongs to the caller's own taxpayer and that every attached evidence
is that taxpayer's own. A response never changes and is never deleted; the
request it answers moves to ``responded`` in the same transaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.ids import canonical


class VatiEvidenceResponse(Document):
    if TYPE_CHECKING:
        response_id: str
        request_id: str
        tenant_id: str
        taxpayer_id: str

    def validate(self) -> None:
        try:
            for field in ("response_id", "request_id", "tenant_id", "taxpayer_id"):
                self.set(field, canonical.canonical_uuid(self.get(field), field))
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        if not self.is_new():
            frappe.throw("An evidence response cannot change once sent.", frappe.PermissionError)

    def on_trash(self) -> None:
        frappe.throw("Evidence responses are kept.", frappe.PermissionError)
