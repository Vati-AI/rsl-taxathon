"""Operator view of the TAX-087 pipeline for one case (ADR-037).

Only site operators (``System Manager``) read it, as with the TAX-033 dead-letter
endpoints in ``vati_tax.outbox.api``: retrying a dead-lettered step is
``vati_tax.outbox.api.retry_event``. The caller names the tenant; a case of another
tenant is ``not_found``. The view holds IDs, states, counts and error codes only.
"""

from __future__ import annotations

from typing import Any

import frappe

from vati_tax.pipeline import store
from vati_tax.refund_access import store as refund_access


@frappe.whitelist(methods=["GET"])
def case_status(tenant_id: str, case_id: str) -> dict[str, Any]:
    refund_access.require_case(case_id, "case.read", tenant_id)  # TAX-142
    frappe.only_for("System Manager")
    return store.case_status(tenant_id=tenant_id, case_id=case_id)
