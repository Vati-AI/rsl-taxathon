"""Send the synthetic SYN-001 and SYN-002 claims through the whole pipeline (TAX-087).

``bench --site verify-... execute vati_tax.pipeline.demo.run`` on a disposable verify
site (refused anywhere else). With no manual step it:

1. creates a tenant, the pipeline service identity and one taxpayer per scenario;
2. as each taxpayer, uploads one CSV document per invoice and contract and sends the
   refund claim through the portal method (``portal.api.submit_refund_claim``), exactly
   as the taxpayer portal does;
3. runs the outbox worker until nothing is due: Jube risk and case (TAX-039), Docling
   extraction, OpenAleph entities and yente screening (this task);
4. reports, per case, the Jube link, extraction records, indexed entities, screenings
   and any failing step, and which services were ``live`` or ``fake``.

A service is ``live`` only when ``live=True`` and the site's ``vati_services`` has its
section (``frappe_dev.py init`` writes it from ignored local config). Otherwise its
``pipeline.fakes`` stand-in is used and the report says so.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

import frappe

from vati_tax.bridge import store as bridge_store
from vati_tax.evidence import store as evidence_store
from vati_tax.identity import policy
from vati_tax.identity.permissions import clear_principal_cache
from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
from vati_tax.ids import mapping
from vati_tax.integrations import config
from vati_tax.outbox import worker
from vati_tax.pipeline import fakes, scenarios, store
from vati_tax.portal import rsl as portal_rsl

VERIFY_SITE = re.compile(r"^verify-[0-9]{8}t[0-9]{6}\.localhost$")
SERVICES = ("jube", "docling", "openaleph", "yente")


class Services:
    """Which service each step uses for this run, and the fakes' counters."""

    def __init__(self, live: bool = False) -> None:
        configured = config.configured(frappe.conf) if live else {}
        self.mode = {name: "live" if configured.get(name) else "fake" for name in SERVICES}
        self.jube = fakes.FakeJube(mapping.record_link)
        self.docling = fakes.FakeDocling()
        self.openaleph = fakes.FakeOpenAleph()
        self.yente = fakes.FakeYente()

    def install(self, service_user: str) -> None:
        conf = fakes.FAKE_SERVICES

        def docling(_tenant: str, *, jobs: Any, sink: Any) -> Any:
            return config.docling_extraction(conf, jobs=jobs, sink=sink, transport=self.docling)

        def openaleph(_tenant: str) -> Any:
            return config.openaleph_ingest(conf, transport=self.openaleph)

        def yente(_tenant: str) -> Any:
            return config.yente_matcher(conf, transport=self.yente)

        store.set_services(
            docling=docling if self.mode["docling"] == "fake" else None,
            openaleph=openaleph if self.mode["openaleph"] == "fake" else None,
            yente=yente if self.mode["yente"] == "fake" else None,
            service_user=service_user,
        )
        if self.mode["jube"] == "fake":
            bridge_store.set_factories(lambda _t: self.jube, lambda _t: self.jube)
        else:
            bridge_store.set_factories(None, None)

    @staticmethod
    def uninstall() -> None:
        store.set_services()
        bridge_store.set_factories(None, None)

    def calls(self) -> dict[str, int]:
        return {
            "jube_invocations": self.jube.invocations,
            "docling_submissions": self.docling.submissions,
            "openaleph_writes": self.openaleph.writes,
            "yente_matches": self.yente.matches,
        }


def _as(user: str) -> None:
    frappe.set_user(user)
    clear_principal_cache()


def send_scenario(tenant: str, scenario: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Create the scenario's taxpayer and send its claim as that taxpayer (portal path)."""
    from vati_tax.portal import api as portal_api

    _as("Administrator")
    taxpayer = (
        frappe.get_doc(
            {
                "doctype": "Vati Taxpayer",
                "tenant_id": tenant,
                "display_name": f"Synthetic taxpayer {scenario['taxpayer']}",
                "taxpayer_type": "company",
                "tin": str(400000000 + uuid.uuid4().int % 99999999),
            }
        )
        .insert()
        .name
    )
    user = f"tax087-{scenario['case'].lower()}-{run_id}@example.test"
    frappe.get_doc(
        {"doctype": "User", "email": user, "first_name": f"Synthetic {scenario['case']}",
         "send_welcome_email": 0}
    ).insert(ignore_permissions=True)  # fmt: skip
    grant_membership(user, tenant, policy.TAXPAYER, taxpayer)
    frappe.db.commit()
    _as(user)

    def upload(content: bytes, name: str, kind: str) -> str:
        return str(
            evidence_store.register_upload(
                tenant_id=tenant,
                content=content,
                filename=f"{name}.csv",
                document_type=kind,
                idempotency_key=f"tax087-{run_id}-{name}",
            )["evidence_id"]
        )

    contracts = {
        contract: upload(scenarios.contract_document(contract, supplier), contract, "contract")
        for contract, supplier in scenario["contracts"].items()
    }
    evidence_ids = list(contracts.values())
    rows = []
    for day, invoice in enumerate(scenario["invoices"], start=1):
        evidence_id = upload(scenarios.invoice_document(invoice), invoice[0], "invoice")
        evidence_ids.append(evidence_id)
        rows.append(
            scenarios.schedule_row(scenario, day, invoice, evidence_id, contracts[invoice[1]])
        )
    # TAX-125: the claim form is the Online Refunds simulator. The demo stands in for RSL's
    # Online Refunds Application with it, on its synthetic tenant, until TAX-143.
    previous = frappe.local.conf.get(portal_rsl.SIMULATOR_KEY)
    frappe.local.conf[portal_rsl.SIMULATOR_KEY] = True
    try:
        result = _send_claim(portal_api, scenario, run_id, tenant, evidence_ids, rows)
    finally:
        frappe.local.conf[portal_rsl.SIMULATOR_KEY] = previous
    _as("Administrator")
    frappe.db.commit()
    submission = result["data"]["submission_id"]
    return {
        "scenario": scenario["case"],
        "submission_id": submission,
        "case_id": frappe.db.get_value("Vati Submission", submission, "case_id"),
        "evidence_ids": evidence_ids,
    }


def _send_claim(
    portal_api: Any,
    scenario: dict[str, Any],
    run_id: str,
    tenant: str,
    evidence_ids: list[str],
    rows: list[Any],
) -> dict[str, Any]:
    return portal_api.submit_refund_claim(
        idempotency_key=f"tax087-{run_id}-{scenario['case']}-claim",
        period=scenario["period"],
        claimed_amount=scenario["refund_claim"],
        claim_reason="excess_input_vat",
        contact_email="accounts@example.test",
        evidence_ids=evidence_ids,
        declaration=True,
        tenant_id=tenant,
        period_output_vat=scenario["output_vat"],
        period_input_vat=scenario["input_vat_claimed"],
        invoices=rows,
    )


def drain(tenant: str, rounds: int = 20) -> dict[str, int]:
    """Run the outbox worker until nothing is due for the tenant."""
    totals: dict[str, int] = {}
    for _ in range(rounds):
        counts = worker.dispatch(limit=50, tenant_id=tenant, now=datetime.now(UTC))
        if not counts:
            break
        for outcome, n in counts.items():
            totals[outcome] = totals.get(outcome, 0) + n
    return totals


def summary(tenant: str, sent: dict[str, Any]) -> dict[str, Any]:
    status = store.case_status(tenant_id=tenant, case_id=sent["case_id"])
    events: dict[str, int] = {}
    for event in status["events"]:
        key = f"{event['event_type']}:{event['status']}"
        events[key] = events.get(key, 0) + 1
    return {
        **sent,
        "jube_case": next(
            (link["external_id"] for link in status["links"] if link["service"] == "jube"), None
        ),  # fmt: skip
        "events": events,
        "extractions": [
            e["extraction"]["status"] if e["extraction"] else None for e in status["evidence"]
        ],  # fmt: skip
        "entities_indexed": status["entities_indexed"],
        "screened_parties": len(status["screenings"]),
        "candidates": sum(int(s["candidate_count"]) for s in status["screenings"]),
        "failing": status["failing"],
    }


def run(live: bool = False) -> dict[str, Any]:
    site = getattr(frappe.local, "site", None) or ""
    if not VERIFY_SITE.match(site):
        raise SystemExit("pipeline.demo runs only on disposable verify-* sites")
    _as("Administrator")
    run_id = uuid.uuid4().hex[:8]
    tenant = ensure_tenant(str(uuid.uuid4()), "Synthetic TAX-087 pipeline tenant")
    service_user = ensure_service_identity(f"tax087-pipeline-{run_id}@example.test", [tenant])
    frappe.db.commit()
    services = Services(live=bool(live))
    services.install(service_user)
    try:
        sent = [send_scenario(tenant, scenario, run_id) for scenario in scenarios.SCENARIOS]
        delivered = drain(tenant)
        cases = [summary(tenant, item) for item in sent]
    finally:
        _as("Administrator")
        services.uninstall()
    return {
        "site": site,
        "tenant_id": tenant,
        "services": services.mode,
        "outbox": delivered,
        "fake_calls": services.calls(),
        "cases": cases,
    }
