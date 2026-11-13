"""Frappe binding: read refund claims filed with RSL into Vati cases (TAX-122, ADR-043).

``ingest_online_refunds(tenant_id)`` reads each taxpayer's claims from the site's TAX-111
RSL source (``factories.rsl_sources``; a recorded fixture until RSL supplies an interface)
and, running as the pipeline service identity, records each new claim as a ``Vati
Submission`` (source ``rsl_online_refunds``) with its ``Vati Refund Claim`` and sends it.
Sending runs the same ``doc_events`` as a portal send: the TAX-121 assurance step, the
TAX-039 case registration and TAX-087 evidence linking. Nothing new is added downstream.

- One submission per RSL claim ID (scoped idempotency key). Reading the same record again
  replays it; a different record under the same claim ID is a conflict and changes nothing.
- RSL-held claim lines and documents are kept as references with hashes, never copied.
- RSL not answering is reported as ``unavailable`` (for the run or per taxpayer), never as
  "no claims".
- Nothing decides, pays, denies or assesses; RSL keeps the claim's status.
- The claim ID is recorded as an ``online_refunds`` service link on the case (TAX-133).
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import frappe

from vati_tax.ids import mapping
from vati_tax.ids.canonical import MappingError
from vati_tax.integrations import config, factories
from vati_tax.pipeline import store as pipeline_store
from vati_tax.rsl_intake import rules
from vati_tax.submissions import domain

SUBMISSION = "Vati Submission"
CLAIM = "Vati Refund Claim"
TAXPAYER = "Vati Taxpayer"
RSL_SERVICE, RSL_TYPE = "online_refunds", "claim"


_sources: Callable[[str], Any] | None = None


def set_sources(sources: Callable[[str], Any] | None = None) -> None:
    """Tests inject an RSL source factory; ``None`` resets to the site's configuration."""
    global _sources
    _sources = sources


def sources_for(tenant_id: str) -> Any:
    return _sources(tenant_id) if _sources is not None else factories.rsl_sources(tenant_id)


def log(action: str, outcome: str, **ids: object) -> None:
    # IDs, codes and counts only: never amounts, names or record contents.
    frappe.logger("vati_tax.rsl_intake").info({"action": action, "outcome": outcome, **ids})


@contextlib.contextmanager
def _atomic() -> Iterator[None]:
    savepoint = "vati_rsl_intake_" + uuid.uuid4().hex[:12]
    frappe.db.savepoint(savepoint)
    try:
        yield
    except BaseException:
        frappe.db.rollback(save_point=savepoint)
        raise


def _existing(tenant_id: str, taxpayer_id: str, claim: rules.IntakeClaim) -> Any:
    key = domain.scoped_key(tenant_id, taxpayer_id, claim.idempotency_key)
    return frappe.db.get_value(
        SUBMISSION,
        {"submission_key": key},
        ["name", "case_id", "source", "source_record_sha256"],
        as_dict=True,
    )


def _record(tenant_id: str, taxpayer_id: str, claim: rules.IntakeClaim) -> dict[str, Any]:
    """Record and send one RSL claim (caller runs as the service identity)."""
    sub = frappe.get_doc(
        {
            "doctype": SUBMISSION,
            "tenant_id": tenant_id,
            "taxpayer_id": taxpayer_id,
            "submission_type": "refund_claim",
            "period": claim.period,
            "source": rules.SOURCE,
            "source_ref": claim.claim_ref,
            "source_contract": claim.contract_id,
            "source_record_sha256": claim.record_sha256,
            "source_refs": json.dumps(claim.source_refs, sort_keys=True),
            "idempotency_key": claim.idempotency_key,
            "synthetic": 1 if claim.fixture else 0,
        }
    ).insert(ignore_permissions=True)
    frappe.get_doc(
        {
            "doctype": CLAIM,
            "submission_id": sub.name,
            "tenant_id": tenant_id,
            "taxpayer_id": taxpayer_id,
            "claimed_amount": claim.claimed_amount,
            "claim_reason": rules.CLAIM_REASON,
            "explanation": rules.explanation(claim.claim_ref),
            "evidence_ids": "[]",
        }
    ).insert(ignore_permissions=True)
    sub.reload()
    sub.status = domain.SUBMITTED
    sub.save(ignore_permissions=True)
    case_id = frappe.db.get_value(SUBMISSION, sub.name, "case_id")
    if case_id:
        # TAX-133: the RSL claim joins the canonical reference register.
        mapping.record_link(
            tenant_id=tenant_id,
            case_id=case_id,
            service=RSL_SERVICE,
            external_type=RSL_TYPE,
            external_id=claim.claim_ref,
        )
    return {"submission_id": sub.name, "case_id": case_id}


def _take(tenant_id: str, taxpayer_id: str, claim: rules.IntakeClaim) -> dict[str, Any]:
    item: dict[str, Any] = {"claim_ref": claim.claim_ref, "taxpayer_id": taxpayer_id,
                            "submission_id": None, "case_id": None, "reason": None}  # fmt: skip
    if claim.refused:
        return {**item, "outcome": rules.REFUSED, "reason": claim.refused}
    existing = _existing(tenant_id, taxpayer_id, claim)
    if existing:
        outcome = rules.replay_outcome(existing.source_record_sha256, claim)
        reason = None if outcome == rules.REPLAYED else "rsl_record_changed"
        return {**item, "outcome": outcome, "reason": reason,
                "submission_id": existing.name, "case_id": existing.case_id}  # fmt: skip
    try:
        with _atomic():
            ids = _record(tenant_id, taxpayer_id, claim)
    except MappingError as exc:
        # E.g. the RSL claim is already mapped to another case: reported, nothing changed.
        reason = exc.field_errors[0]["code"] if exc.field_errors else exc.code
        return {**item, "outcome": rules.REFUSED, "reason": reason}
    except (frappe.ValidationError, frappe.PermissionError) as exc:
        # A claim Vati's rules refuse (e.g. a period in the future) is reported, not retried.
        return {**item, "outcome": rules.REFUSED, "reason": type(exc).__name__}
    return {**item, "outcome": rules.CREATED, **ids}


def ingest_online_refunds(
    tenant_id: str, *, taxpayer_ids: Iterable[str] | None = None
) -> dict[str, Any]:
    """Read and record the tenant's Online Refunds claims (see the module docstring)."""
    summary: dict[str, Any] = {
        "tenant_id": tenant_id, "status": rules.UNAVAILABLE, "reason": None, "source": None,
        "taxpayers": [], "claims": [], "counts": {},
    }  # fmt: skip
    try:
        fixture = sources_for(tenant_id)
    except config.ServiceNotConfigured as exc:
        summary["reason"] = exc.reason or "rsl_sources_not_configured"
        log("ingest", rules.UNAVAILABLE, tenant_id=tenant_id, reason=summary["reason"])
        return summary
    summary["source"] = {"mode": fixture.mode, "fixture": True, "adapter": fixture.adapter_id,
                         "adapter_version": fixture.adapter_version,
                         "file_sha256": fixture.sha256}  # fmt: skip
    filters: dict[str, Any] = {"tenant_id": tenant_id, "tin": ["is", "set"]}
    if taxpayer_ids is not None:
        filters["name"] = ["in", list(taxpayer_ids)]
    taxpayers = frappe.get_all(TAXPAYER, filters=filters, fields=["name", "tin"], order_by="name")
    rsl_tenant = str(fixture.params["tenant_id"])
    with pipeline_store.as_service():
        for taxpayer in taxpayers:
            envelope = fixture.sources.read(
                rules.SYSTEM, rules.KIND, tenant_id=rsl_tenant, taxpayer=str(taxpayer.tin)
            )
            try:
                status, claims = rules.plan_claims(envelope, tin=str(taxpayer.tin))
            except rules.InvalidIntake as exc:
                # A read that breaks the contract is reported for inspection, not guessed at.
                summary["taxpayers"].append({"taxpayer_id": taxpayer.name,
                                             "status": rules.REFUSED, "reason": exc.reason,
                                             "claims": 0})  # fmt: skip
                continue
            summary["taxpayers"].append({"taxpayer_id": taxpayer.name, "status": status,
                                         "claims": len(claims)})  # fmt: skip
            for claim in claims:
                summary["claims"].append(_take(tenant_id, taxpayer.name, claim))
    summary["status"] = rules.run_status(t["status"] for t in summary["taxpayers"])
    counts: dict[str, int] = {}
    for item in summary["claims"]:
        counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1
    summary["counts"] = dict(sorted(counts.items()))
    log("ingest", summary["status"], tenant_id=tenant_id, **summary["counts"])
    return summary
