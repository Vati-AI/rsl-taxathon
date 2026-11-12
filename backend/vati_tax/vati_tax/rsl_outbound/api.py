"""Whitelisted entry points for Vati's outbound results to RSL (TAX-124, ADR-048).

``get_case_outbound`` shows officers what Vati has queued for RSL on a case and whether
each item was delivered (``X-Vati-Tenant-ID`` selects the tenant, TAX-029). Emission
itself has no endpoint: approving a decision or authorising an evidence request queues
it, and the TAX-033 outbox worker delivers it. A dead-lettered item is retried by a site
operator with ``vati_tax.outbox.api.retry_event``.
"""

from __future__ import annotations

from typing import Any

import frappe

from vati_tax.identity import permissions
from vati_tax.ids.canonical import MappingError
from vati_tax.refund_access import store as refund_access
from vati_tax.rsl_outbound import store


@frappe.whitelist(methods=["GET"])
def get_case_outbound(case_id: str) -> list[dict[str, Any]]:
    refund_access.require_case(case_id, "case.read")  # TAX-142
    try:
        return store.case_outbound(
            tenant_id=permissions.request_tenant_header() or "", case_id=case_id
        )
    except MappingError as exc:
        if exc.http_status == 404:
            frappe.throw(str(exc), frappe.DoesNotExistError, title=exc.code)
        frappe.throw(str(exc), title=exc.code)
        raise AssertionError("unreachable") from exc
