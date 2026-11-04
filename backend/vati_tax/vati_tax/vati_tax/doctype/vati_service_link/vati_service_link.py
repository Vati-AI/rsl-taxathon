"""Mapping from a canonical case to one upstream record (TAX-030).

The unique ``link_key``, ``slot_key`` and ``event_key`` columns enforce in the
database the rules that ``vati_tax.ids.canonical.decide_link`` applies first, so a
concurrent or direct insert cannot create a duplicate mapping. Records are
created through ``vati_tax.ids.mapping.record_link``; only sync fields may change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

from vati_tax.ids import canonical

IDENTITY = ("tenant_id", "case_id", "service", "external_type", "external_id", "source_event_id")


class VatiServiceLink(Document):
    if TYPE_CHECKING:
        tenant_id: str
        case_id: str
        service: str
        external_type: str
        external_id: str
        source_event_id: str | None
        sync_state: str
        last_attempt_at: str | None
        error_code: str | None
        link_key: str
        slot_key: str | None
        event_key: str | None

    def validate(self) -> None:
        try:
            link = canonical.Link.build(
                self.tenant_id,
                self.case_id,
                self.service,
                self.external_type,
                self.external_id,
                self.source_event_id,
            )
        except canonical.MappingError as exc:
            frappe.throw(str(exc), title=exc.code)
        case_tenant = frappe.db.get_value("Vati Case Identity", link.case_id, "tenant_id")
        if case_tenant != link.tenant_id:
            # Same message for a missing and a foreign case: no cross-tenant probing.
            frappe.throw("Case not found.", frappe.DoesNotExistError)
        self.tenant_id, self.case_id = link.tenant_id, link.case_id
        self.source_event_id = link.source_event_id
        self.link_key, self.slot_key, self.event_key = link.link_key, link.slot_key, link.event_key
        if not self.is_new():
            before = self.get_doc_before_save()
            if before is not None:
                for field in (*IDENTITY, "link_key", "slot_key", "event_key"):
                    if (before.get(field) or None) != (self.get(field) or None):
                        frappe.throw(f"{field} cannot change; record a new link instead.")

    def as_service_link(self) -> dict[str, object]:
        """Contract shape of ``ServiceLink`` in integration/api/openapi.json."""
        attempted = self.last_attempt_at
        return {
            "service": self.service,
            "external_type": self.external_type,
            "external_id": self.external_id,
            "sync": {
                "state": self.sync_state,
                "last_attempt_at": _iso(attempted) if attempted else None,
                "error_code": self.error_code or None,
            },
        }


def _iso(value: object) -> str:
    stamp = frappe.utils.get_datetime(value)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
