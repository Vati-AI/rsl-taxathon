"""Seal audit events recorded before TAX-059 into each tenant's hash chain.

TAX-046 events have no ``category`` or chain fields. They are sealed in the
order they were created. A tenant that already has sealed entries is left as
it is and reported: placing old rows behind newer ones would rewrite history,
and ``verify_chain`` then shows the gap for a supervisor to review.
"""

from __future__ import annotations

import datetime
from typing import Any

import frappe

from vati_tax.audit import ledger

AUDIT = "Vati Audit Event"


def _unsealed(row: Any) -> ledger.Unsealed:
    occurred = frappe.utils.get_datetime(row.occurred_at).replace(tzinfo=datetime.UTC)
    return ledger.Unsealed(
        event_id=row.name,
        tenant_id=row.tenant_id,
        action=row.action,
        outcome=row.outcome,
        actor=row.actor,
        actor_role=row.actor_role,
        occurred_at=occurred,
        correlation_id=row.correlation_id,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        payload_hash=row.payload_hash,
        case_id=row.case_id or None,
        taxpayer_id=row.taxpayer_id or None,
        sequence=int(row.sequence or 0) or None,
    )


def execute() -> None:
    rows = frappe.get_all(
        AUDIT,
        filters={"chain_seq": ["in", [0, None]]},
        fields=[
            "name",
            "tenant_id",
            "action",
            "outcome",
            "actor",
            "actor_role",
            "occurred_at",
            "correlation_id",
            "subject_type",
            "subject_id",
            "payload_hash",
            "case_id",
            "taxpayer_id",
            "sequence",
        ],
        order_by="creation asc",
    )
    by_tenant: dict[str, list[Any]] = {}
    for row in rows:
        by_tenant.setdefault(row.tenant_id, []).append(row)
    for tenant_id, tenant_rows in by_tenant.items():
        if frappe.db.count(AUDIT, {"tenant_id": tenant_id, "chain_seq": [">", 0]}):
            frappe.log_error(
                title="vati_tax: audit ledger not sealed",
                message=f"Tenant {tenant_id} already has sealed entries; "
                f"{len(tenant_rows)} older events left unsealed for review.",
            )
            continue
        head: ledger.LedgerEntry | None = None
        for row in tenant_rows:
            entry = ledger.seal(_unsealed(row), head)
            frappe.db.set_value(
                AUDIT,
                row.name,
                {
                    "category": entry.category,
                    "chain_seq": entry.chain_seq,
                    "prev_hash": entry.prev_hash,
                    "entry_hash": entry.entry_hash,
                    "chain_key": ledger.chain_key(tenant_id, entry.chain_seq),
                },
                update_modified=False,
            )
            head = entry
