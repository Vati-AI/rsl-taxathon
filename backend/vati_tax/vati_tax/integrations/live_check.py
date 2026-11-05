"""Live proof that an officer decision reaches the real local Jube (TAX-085).

Run only by ``scripts/frappe_dev.py verify --live-services`` on a disposable
``verify-*`` site, after the site has been given the same ``vati_services``
configuration as the developer site. Nothing is injected: the decision store
resolves ``vati_case_lifecycle`` from site config exactly as in normal use.

Steps (synthetic data only): register a case; invoke the TAX-035 risk model via
``vati_risk_evaluator`` (Jube raises cases only from an archived invocation);
create the Jube case via ``vati_case_lifecycle`` (records the Vati Service
Link); move it to ``AWAITING_HUMAN`` as the investigation workflow would; an
auditor proposes ``continue_investigation`` (the engine reads the status from
Jube) and a supervisor approves it (the engine moves the Jube case). The Jube
case is then read back. Finally, an unreachable Jube and a missing
configuration must be refused as ``upstream_unavailable`` without recording a
decision. The report holds IDs and statuses only, never configuration values.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable
from typing import Any

import frappe

from vati_tax.decisions import store as decisions
from vati_tax.identity import policy
from vati_tax.identity.permissions import clear_principal_cache
from vati_tax.identity.provision import ensure_tenant, grant_membership
from vati_tax.ids import mapping
from vati_tax.integrations import config

VERIFY_SITE = re.compile(r"^verify-[0-9]{8}t[0-9]{6}\.localhost$")
ARCHIVE_WAIT_S = 120


def _key() -> str:
    return "synthetic-" + uuid.uuid4().hex


def _user(name: str, run_id: str, tenant: str, role: policy.Role) -> str:
    user = f"tax085-{name}-{run_id}@example.test"
    frappe.get_doc(
        {
            "doctype": "User",
            "email": user,
            "first_name": f"Synthetic {name}",
            "send_welcome_email": 0,
        }
    ).insert(ignore_permissions=True)
    grant_membership(user, tenant, role)
    return user


def _as(user: str) -> None:
    frappe.set_user(user)
    clear_principal_cache()


def _refusal(fn: Callable[[], Any]) -> dict[str, Any]:
    try:
        fn()
    except Exception as exc:  # report the stable code only, never the message
        return {
            "refused": True,
            "error": type(exc).__name__,
            "code": getattr(exc, "code", None),
            "field_errors": getattr(exc, "field_errors", None),
        }
    return {"refused": False}


def run() -> dict[str, Any]:
    site = getattr(frappe.local, "site", None) or ""
    if not VERIFY_SITE.match(site):
        raise SystemExit("live_check runs only on disposable verify-* sites")
    frappe.set_user("Administrator")
    decisions.set_lifecycle_factory(None)  # resolve from site config, as in normal use
    report: dict[str, Any] = {
        "site": site,
        "configured": config.configured(frappe.conf),
        "vati_case_lifecycle": frappe.conf.get("vati_case_lifecycle"),
        "vati_risk_evaluator": frappe.conf.get("vati_risk_evaluator"),
    }
    tenant = str(config.section(frappe.conf, "jube")["vati_tenant_id"])
    run_id = uuid.uuid4().hex[:8]
    ensure_tenant(tenant, "Synthetic TAX-085 live check tenant")
    auditor = _user("auditor", run_id, tenant, policy.AUDITOR)
    supervisor = _user("supervisor", run_id, tenant, policy.SUPERVISOR)
    taxpayer = str(uuid.uuid4())
    case_id = mapping.register_case(tenant_id=tenant, idempotency_key=_key(), taxpayer_id=taxpayer)[
        "case_id"
    ]
    frappe.db.commit()
    report["case_id"] = case_id
    corr = str(uuid.uuid4())

    evaluator = frappe.get_attr(frappe.conf["vati_risk_evaluator"])(tenant)
    facts = {
        f["name"]: "1" if f["type"] == "integer" else "100.00"
        for f in evaluator.ruleset["features"]
    }
    report["risk_invocation"] = evaluator.invoke_declared(
        case_id=case_id,
        taxpayer_id=taxpayer,
        period_end="2026-09-30",
        facts=facts,
        correlation_id=corr,
    )

    lifecycle = frappe.get_attr(frappe.conf["vati_case_lifecycle"])(tenant)
    report["adapter"] = type(lifecycle).__name__
    deadline = time.monotonic() + ARCHIVE_WAIT_S
    while True:  # Jube archives invocations asynchronously
        try:
            created = lifecycle.ensure_case(tenant, case_id, correlation_id=corr)
            break
        except Exception as exc:
            waiting = getattr(exc, "reason", None) == "risk_evaluation_missing"
            if not waiting or time.monotonic() > deadline:
                raise
            time.sleep(3)
    frappe.db.commit()
    report["jube_case_id"] = created.case.jube_case_id
    report["jube_status_created"] = created.case.status
    report["service_links"] = [
        {"service": link["service"], "external_type": link["external_type"]}
        for link in mapping.case_service_links(tenant_id=tenant, case_id=case_id)
    ]
    moved = lifecycle.transition(
        tenant,
        case_id,
        "AWAITING_HUMAN",
        expected_status=created.case.status,
        correlation_id=corr,
        rating=2,
    )
    report["jube_status_before_decision"] = moved.case.status

    _as(auditor)
    proposed = decisions.propose_decision(
        tenant_id=tenant,
        case_id=case_id,
        body={
            "outcome": "continue_investigation",
            "rationale": "Synthetic live check: compare the import declarations with invoices.",
            "rating": 2,
            "expected_case_status": "AWAITING_HUMAN",
            "expected_version": decisions.FrappeRepository().case_version(tenant, case_id),
        },
        idempotency_key=_key(),
    )
    frappe.db.commit()
    _as(supervisor)
    approved = decisions.review_decision(
        tenant_id=tenant,
        decision_id=proposed["decision_id"],
        action="approve",
        body={"expected_version": decisions.FrappeRepository().case_version(tenant, case_id)},
        idempotency_key=_key(),
    )
    frappe.db.commit()
    frappe.set_user("Administrator")
    after = lifecycle.get_case(tenant, case_id, correlation_id=corr)
    report["decision"] = {
        "decision_id": approved["decision_id"],
        "proposed_case_status": proposed["case_status_at_proposal"],
        "status": approved["status"],
        "jube_sync": approved["jube_sync"],
    }
    report["jube_status_read_back"] = after.status

    # Refusals: nothing may be recorded and nothing faked.
    original = frappe.local.conf.get("vati_services")
    before = frappe.db.count(decisions.DECISION, {"case_id": case_id})

    def propose() -> Any:
        _as(auditor)
        try:
            return decisions.propose_decision(
                tenant_id=tenant,
                case_id=case_id,
                body={
                    "outcome": "escalate_to_supervisor",
                    "rationale": "Synthetic live check of an unavailable Jube dependency.",
                    "rating": 2,
                    "expected_case_status": after.status,
                    "expected_version": decisions.FrappeRepository().case_version(tenant, case_id),
                },
                idempotency_key=_key(),
            )
        finally:
            frappe.set_user("Administrator")

    try:
        unreachable = {**original, "jube": {**original["jube"], "base_url": "http://127.0.0.1:9"}}
        frappe.local.conf["vati_services"] = unreachable
        report["jube_unreachable"] = _refusal(propose)
        frappe.local.conf["vati_services"] = {}
        report["jube_missing"] = _refusal(propose)
    finally:
        frappe.local.conf["vati_services"] = original
    frappe.db.rollback()
    report["decisions_recorded_by_refusals"] = (
        frappe.db.count(decisions.DECISION, {"case_id": case_id}) - before
    )

    report["ok"] = bool(
        approved["jube_sync"]["state"] == "synced"
        and after.status == "INVESTIGATING"
        and report["jube_unreachable"].get("code") == "upstream_unavailable"
        and report["jube_missing"].get("code") == "upstream_unavailable"
        and report["decisions_recorded_by_refusals"] == 0
    )
    return report
