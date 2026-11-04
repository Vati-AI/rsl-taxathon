"""Operator endpoints for dead letters (TAX-033).

Only site operators (``System Manager``) may change delivery state by hand. The
caller names the tenant explicitly and the event must belong to it; a foreign or
missing event is ``not_found``. Errors propagate as structured ``MappingError``s.
"""

from __future__ import annotations

from typing import Any

import frappe

from vati_tax.outbox import store


def _operator() -> str:
    frappe.only_for("System Manager")
    return str(frappe.session.user)


@frappe.whitelist(methods=["POST"])
def retry_event(tenant_id: str, event_id: str) -> dict[str, Any]:
    return store.retry_dead_letter(tenant_id=tenant_id, event_id=event_id, actor=_operator())


@frappe.whitelist(methods=["POST"])
def resolve_effect(
    tenant_id: str, event_id: str, effect_name: str, result_ref: str
) -> dict[str, Any]:
    return store.resolve_effect(
        tenant_id=tenant_id,
        event_id=event_id,
        effect_name=effect_name,
        result_ref=result_ref,
        actor=_operator(),
    )


@frappe.whitelist(methods=["POST"])
def release_effect(tenant_id: str, event_id: str, effect_name: str) -> dict[str, Any]:
    return store.release_effect(
        tenant_id=tenant_id, event_id=event_id, effect_name=effect_name, actor=_operator()
    )


@frappe.whitelist(methods=["GET"])
def delivery_state(tenant_id: str, event_id: str) -> dict[str, Any]:
    _operator()
    return store.delivery_state(tenant_id=tenant_id, event_id=event_id)
