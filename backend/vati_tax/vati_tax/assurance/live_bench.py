"""Seed the TAX-118 test taxpayers into a disposable site and read results back (TAX-121).

Run by ``python3 -m ai.evaluation.assurance_live`` on the host, which prepares the files and
calls ``bench --site <site> execute vati_tax.assurance.live_bench.run``. Refused on any
site that is not a disposable ``verify-*`` or ``tax121-live-*`` site.

``run(seed_path, out_path, tenant_id)``:

1. creates the tenant, the pipeline service identity and one auditor (the reader);
2. for each taxpayer: a ``Vati Taxpayer`` and a taxpayer user; as that taxpayer, uploads
   the taxpayer's own records as one evidence document and sends one refund claim per
   Online Refunds Application claim (period and amount), the way ``Vati Submission`` is
   sent everywhere else, so every doc event and outbox step runs;
3. runs the outbox worker until no ``assurance.requested`` event is left to deliver;
4. as the auditor, reads every case back through ``assurance.store.case_assurance`` (runs,
   lane proposals, ``vati_check`` findings) and the operator view of each case's events,
   and writes them to ``out_path``.

The seed file holds no answer key; scoring happens on the host.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import frappe

from vati_tax.assurance import store
from vati_tax.identity import policy
from vati_tax.identity.permissions import clear_principal_cache
from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
from vati_tax.outbox import store as outbox_store
from vati_tax.outbox import worker
from vati_tax.pipeline import store as pipeline_store

SITE = re.compile(r"^(verify-[0-9]{8}t[0-9]{6}|tax121-live-[0-9]{8}t[0-9]{6})\.localhost$")


def _as(user: str) -> None:
    frappe.set_user(user)
    clear_principal_cache()


def _user(email: str, name: str) -> str:
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email, "first_name": name,
                        "send_welcome_email": 0}).insert(ignore_permissions=True)  # fmt: skip
    return email


def seed_taxpayer(tenant: str, run_id: str, item: dict[str, Any]) -> dict[str, Any]:
    """One taxpayer, their records document and one sent refund claim per RSL claim."""
    _as("Administrator")
    tin = str(item["tin"])
    taxpayer = frappe.get_doc(
        {"doctype": "Vati Taxpayer", "tenant_id": tenant, "display_name": item["name"],
         "taxpayer_type": "company", "tin": tin, "synthetic": 1}
    ).insert().name  # fmt: skip
    user = _user(f"tax121-{tin}-{run_id}@example.test", f"Synthetic {tin}")
    grant_membership(user, tenant, policy.TAXPAYER, taxpayer)
    frappe.db.commit()
    _as(user)
    records = store.taxpayer_records_upload(
        tenant_id=tenant, taxpayer_id=taxpayer, records=item["records"],
        idempotency_key=f"tax121-{run_id}-{tin}-records",
    )  # fmt: skip
    sent = []
    for claim in item["claims"]:
        sub = frappe.get_doc(
            {"doctype": "Vati Submission", "tenant_id": tenant, "taxpayer_id": taxpayer,
             "submission_type": "refund_claim", "period": claim["period"], "declaration": 1,
             "contact_email": "accounts@example.test", "source": "portal",
             "idempotency_key": f"tax121-{run_id}-{claim['claim_id']}"}
        ).insert()  # fmt: skip
        frappe.get_doc(
            {"doctype": "Vati Refund Claim", "submission_id": sub.name, "tenant_id": tenant,
             "taxpayer_id": taxpayer, "claimed_amount": claim["claimed_amount"],
             "claim_reason": "excess_input_vat", "evidence_ids": "[]"}
        ).insert()  # fmt: skip
        sub.reload()
        sub.status = "submitted"
        sub.save()
        sent.append({"claim_ref": claim["claim_id"], "submission_id": sub.name,
                     "case_id": frappe.db.get_value("Vati Submission", sub.name, "case_id")})  # fmt: skip
    _as("Administrator")
    frappe.db.commit()
    return {"tin": tin, "taxpayer_id": taxpayer, "records_evidence_id": records["evidence_id"],
            "cases": sent}  # fmt: skip


def _pending(tenant: str) -> int:
    return frappe.db.count(
        outbox_store.EVENT,
        {"tenant_id": tenant, "event_type": "assurance.requested",
         "status": ["in", ["pending", "retry", "in_progress"]]},
    )  # fmt: skip


def drain(tenant: str, max_rounds: int = 10000) -> dict[str, Any]:
    """Run the outbox worker until no assurance step is left (other steps keep retrying)."""
    totals: dict[str, int] = {}
    rounds = 0
    while _pending(tenant) and rounds < max_rounds:
        counts = worker.dispatch(limit=200, tenant_id=tenant, now=datetime.now(UTC))
        rounds += 1
        for outcome, n in counts.items():
            totals[outcome] = totals.get(outcome, 0) + n
        if not counts:
            time.sleep(1)
    return {"dispatch_rounds": rounds, "outcomes": totals, "assurance_pending": _pending(tenant)}


def _events(tenant: str, case_id: str) -> list[dict[str, Any]]:
    rows = frappe.get_all(
        outbox_store.EVENT,
        filters={"tenant_id": tenant, "case_id": case_id},
        fields=["event_type", "status", "attempts", "last_error_code", "correlation_id",
                "sequence"],
        order_by="sequence asc",
    )  # fmt: skip
    return [dict(r) for r in rows]


def run(seed_path: str, out_path: str, tenant_id: str, limit: int | None = None) -> dict[str, Any]:
    site = getattr(frappe.local, "site", None) or ""
    if not SITE.match(site):
        raise SystemExit("live_bench runs only on disposable verify-* or tax121-live-* sites")
    _as("Administrator")
    with open(seed_path, encoding="utf-8") as handle:
        seed = json.load(handle)
    taxpayers = seed["taxpayers"][: int(limit)] if limit else seed["taxpayers"]
    run_id = uuid.uuid4().hex[:8]
    tenant = ensure_tenant(tenant_id, "Synthetic TAX-121 live benchmark tenant")
    service = ensure_service_identity(f"tax121-pipeline-{run_id}@example.test", [tenant])
    auditor = _user(f"tax121-auditor-{run_id}@example.test", "Synthetic TAX-121 auditor")
    grant_membership(auditor, tenant, policy.AUDITOR)
    frappe.db.commit()
    pipeline_store.set_services(service_user=service)
    timings: dict[str, float] = {}
    try:
        started = time.perf_counter()
        seeded = [seed_taxpayer(tenant, run_id, item) for item in taxpayers]
        timings["seed_seconds"] = round(time.perf_counter() - started, 1)
        started = time.perf_counter()
        drained = drain(tenant)
        timings["drain_seconds"] = round(time.perf_counter() - started, 1)
        started = time.perf_counter()
        _as(auditor)
        for item in seeded:
            for case in item["cases"]:
                case["assurance"] = store.case_assurance(tenant_id=tenant, case_id=case["case_id"])
        _as("Administrator")
        for item in seeded:
            for case in item["cases"]:
                case["events"] = _events(tenant, case["case_id"])
        timings["readback_seconds"] = round(time.perf_counter() - started, 1)
    finally:
        _as("Administrator")
        pipeline_store.set_services()
    report = {
        "synthetic": True,
        "task": "TAX-121",
        "site": site,
        "tenant_id": tenant,
        "seed_sha256": seed.get("seed_sha256"),
        "taxpayers": len(seeded),
        "cases": sum(len(i["cases"]) for i in seeded),
        "drain": drained,
        "timings": timings,
        "site_services": store._not_used(),
        "read_by": "auditor (Vati Auditor membership) through vati_tax.assurance.store.case_assurance",
        "results": seeded,
    }
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, sort_keys=True, default=str)
    summary = {k: v for k, v in report.items() if k != "results"}
    print(json.dumps(summary, default=str))
    return summary
