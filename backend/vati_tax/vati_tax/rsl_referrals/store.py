"""Frappe binding: RSL audit referrals open Vati cases (TAX-123, ADR-043 §1, ADR-047).

``ingest_audit_referrals(tenant_id)`` reads each taxpayer's referrals from the site's RSL
source (``factories.rsl_sources``; a recorded fixture until RSL supplies an interface) and,
running as the pipeline service identity, opens one Vati case per RSL audit case
reference and records the referral (``Vati RSL Referral``) linked to it.

- One case per audit case reference: the case registration key is scoped to the tenant
  and the reference (``ids.mapping.register_case``), so a second read reuses the case.
- Reading the same record again replays and writes nothing. A different record under the
  same reference is a conflict and changes nothing.
- RSL's risk sets and scores are stored as RSL-owned inputs, never as a Vati score.
- RSL not answering is reported as ``unavailable`` (for the run or per taxpayer), never
  as "no referrals".
- A referral selects nothing on its own and decides nothing: officers investigate.

``case_referrals`` gives officers the referral behind a case.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import uuid
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import frappe

from vati_tax.identity import permissions, policy
from vati_tax.ids import mapping
from vati_tax.ids.canonical import CaseNotFound, MappingError, canonical_uuid
from vati_tax.integrations import config, factories
from vati_tax.pipeline import store as pipeline_store
from vati_tax.rsl_referrals import rules

REFERRAL = "Vati RSL Referral"
TAXPAYER = "Vati Taxpayer"
CASE = "Vati Case Identity"
_UNIQUE = (frappe.UniqueValidationError, frappe.DuplicateEntryError)
_FIELDS = [
    "referral_id", "tenant_id", "case_id", "taxpayer_id", "audit_case_ref", "referred_on",
    "selection_reason", "period", "rsl_inputs", "contract_id", "record_sha256",
    "retrieved_at", "fixture", "adapter_id",
]  # fmt: skip

_sources: Callable[[str], Any] | None = None


def set_sources(sources: Callable[[str], Any] | None = None) -> None:
    """Tests inject an RSL source factory; ``None`` resets to the site's configuration."""
    global _sources
    _sources = sources


def sources_for(tenant_id: str) -> Any:
    return _sources(tenant_id) if _sources is not None else factories.rsl_sources(tenant_id)


def log(action: str, outcome: str, **ids: object) -> None:
    # IDs, codes and counts only: never scores, names or record contents.
    frappe.logger("vati_tax.rsl_referrals").info({"action": action, "outcome": outcome, **ids})


def referral_key(tenant_id: str, audit_case_ref: str) -> str:
    return hashlib.sha256(f"{tenant_id}\x1f{audit_case_ref}".encode()).hexdigest()


@contextlib.contextmanager
def _atomic() -> Iterator[None]:
    savepoint = "vati_rsl_referral_" + uuid.uuid4().hex[:12]
    frappe.db.savepoint(savepoint)
    try:
        yield
    except BaseException:
        frappe.db.rollback(save_point=savepoint)
        raise


def _record(tenant_id: str, taxpayer_id: str, referral: rules.Referral) -> dict[str, Any]:
    """Open (or reuse) the case and record the referral (caller runs as the service)."""
    case = mapping.register_case(
        tenant_id=tenant_id, idempotency_key=referral.idempotency_key, taxpayer_id=taxpayer_id
    )
    doc = frappe.get_doc(
        {
            "doctype": REFERRAL,
            "referral_id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "case_id": case["case_id"],
            "taxpayer_id": taxpayer_id,
            "audit_case_ref": referral.audit_case_ref,
            "referred_on": referral.referred_on,
            "selection_reason": referral.selection_reason,
            "period": referral.period,
            "rsl_inputs": json.dumps(referral.rsl_inputs, sort_keys=True),
            "contract_id": referral.contract_id,
            "record_sha256": referral.record_sha256,
            "retrieved_at": referral.retrieved_at,
            "fixture": 1 if referral.fixture else 0,
            "adapter_id": referral.adapter_id,
            "created_at": datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
            "referral_key": referral_key(tenant_id, referral.audit_case_ref),
        }
    )
    doc.insert(ignore_permissions=True)
    return {"case_id": case["case_id"], "referral_id": doc.referral_id,
            "case_reused": bool(case.get("replayed"))}  # fmt: skip


def _take(tenant_id: str, taxpayer_id: str, referral: rules.Referral) -> dict[str, Any]:
    item: dict[str, Any] = {"audit_case_ref": referral.audit_case_ref, "taxpayer_id": taxpayer_id,
                            "case_id": None, "referral_id": None, "reason": None}  # fmt: skip
    key = referral_key(tenant_id, referral.audit_case_ref)

    def stored() -> dict[str, Any] | None:
        existing = frappe.db.get_value(
            REFERRAL, {"referral_key": key}, ["referral_id", "case_id", "record_sha256"],
            as_dict=True,
        )  # fmt: skip
        if not existing:
            return None
        outcome = rules.replay_outcome(existing.record_sha256, referral)
        reason = None if outcome == rules.REPLAYED else "rsl_record_changed"
        return {**item, "outcome": outcome, "reason": reason,
                "case_id": existing.case_id, "referral_id": existing.referral_id}  # fmt: skip

    found = stored()
    if found:
        return found
    try:
        with _atomic():
            ids = _record(tenant_id, taxpayer_id, referral)
    except _UNIQUE:
        # A concurrent run recorded the same reference first: report what it stored.
        return stored() or {**item, "outcome": rules.REFUSED, "reason": "duplicate_reference"}
    except MappingError as exc:
        # E.g. the reference was registered for another taxpayer: reported, nothing changed.
        reason = exc.field_errors[0]["code"] if exc.field_errors else exc.code
        return {**item, "outcome": rules.REFUSED, "reason": reason}
    except (frappe.ValidationError, frappe.PermissionError) as exc:
        return {**item, "outcome": rules.REFUSED, "reason": type(exc).__name__}
    return {**item, "outcome": rules.CREATED, **ids}


def ingest_audit_referrals(
    tenant_id: str, *, taxpayer_ids: Iterable[str] | None = None
) -> dict[str, Any]:
    """Read and record the tenant's RSL audit referrals (see the module docstring)."""
    summary: dict[str, Any] = {
        "tenant_id": tenant_id, "status": rules.UNAVAILABLE, "reason": None, "source": None,
        "taxpayers": [], "referrals": [], "counts": {},
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
                status, referrals = rules.plan_referrals(envelope, tin=str(taxpayer.tin))
            except rules.InvalidReferral as exc:
                # A read that breaks the contract is reported for inspection, not guessed at.
                summary["taxpayers"].append({"taxpayer_id": taxpayer.name,
                                             "status": rules.REFUSED, "reason": exc.reason,
                                             "referrals": 0})  # fmt: skip
                continue
            summary["taxpayers"].append({"taxpayer_id": taxpayer.name, "status": status,
                                         "referrals": len(referrals)})  # fmt: skip
            for referral in referrals:
                summary["referrals"].append(_take(tenant_id, taxpayer.name, referral))
    summary["status"] = rules.run_status(t["status"] for t in summary["taxpayers"])
    counts: dict[str, int] = {}
    for item in summary["referrals"]:
        counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1
    summary["counts"] = dict(sorted(counts.items()))
    log("ingest", summary["status"], tenant_id=tenant_id, **summary["counts"])
    return summary


def case_referrals(*, tenant_id: str, case_id: str) -> list[dict[str, Any]]:
    """The RSL referral(s) behind a case, for officers (``case.read``)."""
    scope = permissions.require_action(policy.CASE_READ, tenant_id)
    case = canonical_uuid(case_id, "case_id")
    if frappe.db.get_value(CASE, case, "tenant_id") != scope.tenant_id:
        raise CaseNotFound("Case not found.", "case_id", "not_found")
    rows = frappe.get_all(
        REFERRAL,
        filters={"tenant_id": scope.tenant_id, "case_id": case},
        fields=_FIELDS,
        order_by="referred_on asc, referral_id asc",
    )
    return [rules.referral_view({**row, "rsl_inputs": json.loads(row.rsl_inputs)}) for row in rows]
