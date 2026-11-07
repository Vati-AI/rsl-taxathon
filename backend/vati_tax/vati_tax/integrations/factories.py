"""Site factories for the integration/ adapters (TAX-085, ADR-035).

Registry (``config.SITE_FACTORIES`` are named in site config; the rest are
called directly by Frappe handlers):

- ``case_lifecycle(tenant_id)``: site key ``vati_case_lifecycle``, the TAX-034
  ``JubeCaseAdapter`` using the ``Vati Service Link`` store (``ids.mapping``).
  Decisions (TAX-046) and the submission bridge (TAX-039) call it.
- ``risk_evaluator(tenant_id)``: site key ``vati_risk_evaluator``, the TAX-039
  ``DeclaredRiskEvaluator`` for the TAX-035 Jube model.
- ``docling_extraction(tenant_id, jobs=..., sink=...)``: TAX-038 Docling adapter.
- ``openaleph_ingest(tenant_id)``: TAX-036 entity ingest into the site's collection.
- ``yente_matcher(tenant_id)``: TAX-037 yente candidate lookup for the site's dataset.
- ``rsl_sources(tenant_id)``: TAX-111 RSL source fixture recorded for the site (site key
  ``vati_rsl_sources``, TAX-121). There is no live RSL connection; the fixture says so.

Each reads ``frappe.conf["vati_services"]`` (written by ``scripts/frappe_dev.py
init`` from ignored local config) on every call and raises
``config.ServiceNotConfigured`` (503 ``upstream_unavailable``) when it is
missing. The adapters enforce tenant scope themselves (the Jube adapter serves
exactly one Vati tenant and conceals others as not found).
"""

from __future__ import annotations

from typing import Any

import frappe

from vati_tax.ids import mapping
from vati_tax.integrations import config


def case_lifecycle(tenant_id: str) -> Any:
    return config.case_lifecycle(
        frappe.conf,
        record_link=mapping.record_link,
        case_service_links=mapping.case_service_links,
    )


def risk_evaluator(tenant_id: str) -> Any:
    return config.risk_evaluator(frappe.conf)


def docling_extraction(tenant_id: str, *, jobs: Any, sink: Any) -> Any:
    return config.docling_extraction(frappe.conf, jobs=jobs, sink=sink)


def openaleph_ingest(tenant_id: str) -> config.OpenAlephIngest:
    return config.openaleph_ingest(frappe.conf)


def yente_matcher(tenant_id: str) -> config.YenteMatcher:
    return config.yente_matcher(frappe.conf)


def rsl_sources(tenant_id: str) -> config.RslSourceFixture:
    return config.rsl_sources(frappe.conf, vati_tenant_id=tenant_id)
