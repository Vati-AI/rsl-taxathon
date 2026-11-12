"""Frappe binding: check before you file (TAX-129, ADR-051).

``check(tenant_id, taxpayer_id, period)`` reads the taxpayer's e-Tax VAT return, Lekuka
purchase invoices and ASYCUDA customs entries for the period from the site's RSL source
(the TAX-111 fixture until RSL supplies an interface), runs the TAX-048 reconciliation
through the portal adapter and returns guidance only (``prefiling.rules``). It stores
nothing and decides nothing.

Callers authorise first: the taxpayer portal for the caller's own taxpayer
(``portal.api.check_before_filing``), or RSL's front door through WSO2 as a service
identity (``prefiling.api.check_before_filing``).
"""

from __future__ import annotations

from typing import Any

import frappe

from vati_tax.assurance import store as assurance_store
from vati_tax.integrations import config
from vati_tax.portal import presubmission
from vati_tax.portal import rsl as portal_rsl
from vati_tax.prefiling import rules

TAXPAYER = "Vati Taxpayer"
_READS = {"etax": "vat_return", "idms": "invoice", "asycuda": "entry"}


class TaxpayerNotFound(LookupError):
    """No taxpayer with that TIN in the tenant."""


def log(outcome: str, **ids: Any) -> None:
    # Codes and IDs only: never amounts or TINs.
    frappe.logger("vati_tax.prefiling").info({"action": "check", "outcome": outcome, **ids})


def check(*, tenant_id: str, taxpayer_id: str, period: str, today: Any = None) -> dict[str, Any]:
    """Guidance for one taxpayer and period (see the module docstring)."""
    day = today or frappe.utils.getdate(frappe.utils.nowdate())
    period = rules.check_period(period, day)
    tin = frappe.db.get_value(TAXPAYER, {"name": taxpayer_id, "tenant_id": tenant_id}, "tin")
    if not tin:
        raise TaxpayerNotFound(taxpayer_id)
    statuses = {system: "unavailable" for system in _READS}
    records: dict[str, list[Any]] = {system: [] for system in _READS}
    try:
        fixture = assurance_store.sources_for(tenant_id)
    except config.ServiceNotConfigured:
        fixture = None
    if fixture is not None:
        rsl_tenant = str(fixture.params["tenant_id"])
        for system, kind in _READS.items():
            envelope = fixture.sources.read(system, kind, tenant_id=rsl_tenant, taxpayer=str(tin))
            statuses[system] = str(envelope.get("status"))
            records[system] = list(envelope.get("records") or [])
    vat_return, invoices, entries = rules.select(
        period, records["etax"], records["idms"], records["asycuda"]
    )
    reconciliation = None
    if vat_return is not None:
        try:
            reconciliation = presubmission.reconcile(
                rules.build_record(vat_return, invoices, entries)
            )
        except presubmission.ReconciliationUnavailable:
            statuses["etax"] = "unavailable"  # reported as not checked, never as clean
    view = rules.result(
        period=period,
        statuses=statuses,
        vat_return=vat_return,
        invoices=invoices,
        entries=entries,
        reconciliation=reconciliation,
        file_url=portal_rsl.online_refunds_url(frappe.conf),
    )
    log(view["status"], tenant_id=tenant_id, taxpayer_id=taxpayer_id, period=period,
        prompts=len(view["prompts"]))  # fmt: skip
    return view


def taxpayer_by_tin(tenant_id: str, tin: str) -> str:
    name = frappe.db.get_value(TAXPAYER, {"tenant_id": tenant_id, "tin": tin}, "name")
    if not name:
        raise TaxpayerNotFound(tin)
    return str(name)
