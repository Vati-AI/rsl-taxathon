"""Frappe binding: the assurance checks as a pipeline step (TAX-121, ADR-042).

Entry point (``doc_events`` on ``Vati Submission``, first in the list so it does not wait
for Jube): when a taxpayer sends a refund claim, or RSL's intake sends one read from the
Online Refunds Application (TAX-122), ``on_submission_sent`` registers (or
replays) the case with the TAX-039 bridge's own registration key and enqueues one
``assurance.requested`` event as the case's next event, in the sender's transaction. The
bridge's ``case.registered`` follows it with the same correlation ID. The Jube step
therefore never blocks the checks; the checks never call an upstream that can hang, so
they hold the Jube step back only while they run (or while the service identity is
missing, which retries).

Handler ``handle_assurance_requested`` (``vati_outbox_handlers``):

1. reads the RSL source fixture configured for the site (``factories.rsl_sources``; site
   key ``vati_rsl_sources``). Missing or unusable configuration, or a required RSL system
   that does not answer, records a ``not_run`` run with its reason code and delivers:
   nothing is computed from missing data and nothing is reported clean;
2. reads the taxpayer's own records from Frappe: the ``Vati Taxpayer`` (TIN) and the
   latest stored records document (``Vati Evidence`` referencing the taxpayer, re-hashed
   before use), and the claim of the sent submission, matched to its Online Refunds
   Application record by TIN and period (amounts must agree);
3. runs ``ai.evaluation.assurance_pipeline.assess_taxpayer`` (TAX-112-116, unchanged) through
   the ``checks`` adapter;
4. as the pipeline service identity, records every finding through
   ``vati_tax.findings.store`` (producer ``vati_check``) and the claim's lane in
   ``Vati Lane Proposal``; a replay writes nothing twice;
5. keeps one ``Vati Assurance Run`` per event: status, per-check status, which services
   were live, recorded or unavailable, counts and timings.

Nothing decides, pays, denies or assesses. ``case_assurance`` is the staff read view.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import frappe

from vati_tax.assurance import checks, engine, rules
from vati_tax.assurance.engine import Duplicate, LaneRecord, RunRecord
from vati_tax.bridge import rules as bridge_rules
from vati_tax.findings import store as findings_store
from vati_tax.identity import permissions
from vati_tax.ids import mapping
from vati_tax.ids.canonical import CaseNotFound, InvalidMapping, MappingError
from vati_tax.integrations import config, factories
from vati_tax.outbox import policy as outbox_policy
from vati_tax.outbox import store as outbox_store
from vati_tax.pipeline import store as pipeline_store

SUBMISSION = "Vati Submission"
CLAIM = "Vati Refund Claim"
TAXPAYER = "Vati Taxpayer"
EVIDENCE = "Vati Evidence"
CASE = "Vati Case Identity"
LANE = "Vati Lane Proposal"
RUN = "Vati Assurance Run"
CORRELATION_FLAG = "vati_correlation_id"
NOT_USED = ("jube", "docling", "openaleph", "yente")
_UNIQUE = (frappe.UniqueValidationError, frappe.DuplicateEntryError)

_sources: Callable[[str], Any] | None = None
_checks: Callable[..., dict[str, Any]] | None = None


def set_sources(
    sources: Callable[[str], Any] | None = None,
    checks: Callable[..., dict[str, Any]] | None = None,
) -> None:
    """Tests inject a source fixture factory (and, rarely, the checks); ``None`` resets."""
    global _sources, _checks
    _sources, _checks = sources, checks


def sources_for(tenant_id: str) -> Any:
    return _sources(tenant_id) if _sources is not None else factories.rsl_sources(tenant_id)


def _assess() -> tuple[Callable[..., dict[str, Any]], str]:
    """The TAX-118 checks (``checks`` adapter) and the TAX-116 register version."""
    assess, register_version = checks.load()
    return (_checks or assess), register_version


def log(action: str, outcome: str, **ids: object) -> None:
    # IDs, codes and counts only: never amounts, names or record contents.
    frappe.logger("vati_tax.assurance").info({"action": action, "outcome": outcome, **ids})


def _utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _naive(value: datetime.datetime) -> datetime.datetime:
    return value.astimezone(datetime.UTC).replace(tzinfo=None)


def _aware(value: Any) -> datetime.datetime:
    return frappe.utils.get_datetime(value).replace(tzinfo=datetime.UTC)


def _json(value: Any) -> Any:
    return json.loads(value) if value else None


# --- repository ---------------------------------------------------------------------


class FrappeRepository:
    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        savepoint = "vati_assurance_" + uuid.uuid4().hex[:12]
        frappe.db.savepoint(savepoint)
        try:
            yield
        except BaseException:
            frappe.db.rollback(save_point=savepoint)
            raise

    _LANE_FIELDS = (
        "proposal_id", "tenant_id", "case_id", "claim_ref", "revision",
        "supersedes_proposal_id", "body_json", "producer", "producer_version",
        "input_digest", "run_key", "created_at", "correlation_id", "proposal_key",
        "request_digest",
    )  # fmt: skip

    @staticmethod
    def _lane(row: Any) -> LaneRecord:
        return LaneRecord(
            proposal_id=row.proposal_id,
            tenant_id=row.tenant_id,
            case_id=row.case_id,
            claim_ref=row.claim_ref,
            revision=int(row.revision),
            supersedes_proposal_id=row.supersedes_proposal_id or None,
            body=_json(row.body_json) or {},
            producer=row.producer,
            producer_version=row.producer_version,
            input_digest=row.input_digest,
            run_key=row.run_key,
            created_at=_aware(row.created_at),
            correlation_id=row.correlation_id,
            proposal_key=row.proposal_key,
            request_digest=row.request_digest,
        )

    def _lanes(self, filters: dict[str, Any], **kwargs: Any) -> list[LaneRecord]:
        rows = frappe.get_all(LANE, filters=filters, fields=list(self._LANE_FIELDS), **kwargs)
        return [self._lane(row) for row in rows]

    def lane_by_key(self, proposal_key: str) -> LaneRecord | None:
        rows = self._lanes({"proposal_key": proposal_key})
        return rows[0] if rows else None

    def latest_lane(self, tenant_id: str, case_id: str, claim_ref: str) -> LaneRecord | None:
        rows = self._lanes(
            {"tenant_id": tenant_id, "case_id": case_id, "claim_ref": claim_ref},
            order_by="revision desc",
            limit=1,
        )
        return rows[0] if rows else None

    def insert_lane(self, record: LaneRecord) -> None:
        body = record.body

        def amount(field: str) -> str:
            return str(body[field]["amount"])

        doc = frappe.get_doc(
            {
                "doctype": LANE,
                "proposal_id": record.proposal_id,
                "tenant_id": record.tenant_id,
                "case_id": record.case_id,
                "claim_ref": record.claim_ref,
                "period": body.get("period"),
                "revision": record.revision,
                "supersedes_proposal_id": record.supersedes_proposal_id,
                "lane": body["lane"],
                "decision_due": body["service_target"].get("decision_due"),
                "decision_target_working_days": body["service_target"].get(
                    "decision_target_working_days"
                ),
                "claimed_amount": amount("claimed_amount"),
                "undisputed_amount": amount("undisputed_amount"),
                "disputed_amount": amount("disputed_amount"),
                "currency": body["claimed_amount"]["currency"],
                "authenticity_indicator": int(bool(body.get("authenticity_indicator"))),
                "body_json": json.dumps(body, sort_keys=True),
                "producer": record.producer,
                "producer_version": record.producer_version,
                "input_digest": record.input_digest,
                "run_key": record.run_key,
                "created_at": _naive(record.created_at),
                "correlation_id": record.correlation_id,
                "proposal_key": record.proposal_key,
                "revision_key": rules.sha256_text(
                    "lane.revision",
                    record.tenant_id,
                    record.case_id,
                    record.claim_ref,
                    str(record.revision),
                ),  # fmt: skip
                "request_digest": record.request_digest,
            }
        )
        try:
            doc.insert(ignore_permissions=True)
        except _UNIQUE as exc:
            raise Duplicate(str(exc)) from exc

    def case_lanes(self, tenant_id: str, case_id: str) -> list[LaneRecord]:
        return self._lanes({"tenant_id": tenant_id, "case_id": case_id}, order_by="creation asc")

    _RUN_FIELDS = (
        "run_id", "tenant_id", "case_id", "submission_id", "claim_ref", "status", "reason",
        "services_json", "checks_json", "summary_json", "finding_ids", "lane_proposal_id",
        "producer_version", "input_digest", "event_id", "correlation_id", "started_at",
        "seconds", "run_key",
    )  # fmt: skip

    @staticmethod
    def _run(row: Any) -> RunRecord:
        return RunRecord(
            run_id=row.run_id,
            tenant_id=row.tenant_id,
            case_id=row.case_id,
            submission_id=row.submission_id,
            claim_ref=row.claim_ref or None,
            status=row.status,
            reason=row.reason or None,
            services=_json(row.services_json) or {},
            checks=_json(row.checks_json) or {},
            summary=_json(row.summary_json) or {},
            finding_ids=tuple(_json(row.finding_ids) or ()),
            lane_proposal_id=row.lane_proposal_id or None,
            producer_version=row.producer_version or None,
            input_digest=row.input_digest or None,
            event_id=row.event_id,
            correlation_id=row.correlation_id,
            started_at=_aware(row.started_at),
            seconds=float(row.seconds or 0),
            run_key=row.run_key,
        )

    def run_by_key(self, run_key: str) -> RunRecord | None:
        rows = frappe.get_all(RUN, filters={"run_key": run_key}, fields=list(self._RUN_FIELDS))
        return self._run(rows[0]) if rows else None

    def insert_run(self, record: RunRecord) -> None:
        doc = frappe.get_doc(
            {
                "doctype": RUN,
                "run_id": record.run_id,
                "tenant_id": record.tenant_id,
                "case_id": record.case_id,
                "submission_id": record.submission_id,
                "claim_ref": record.claim_ref,
                "status": record.status,
                "reason": record.reason,
                "services_json": json.dumps(record.services, sort_keys=True),
                "checks_json": json.dumps(record.checks, sort_keys=True),
                "summary_json": json.dumps(record.summary, sort_keys=True),
                "finding_ids": json.dumps(list(record.finding_ids)),
                "lane_proposal_id": record.lane_proposal_id,
                "producer_version": record.producer_version,
                "input_digest": record.input_digest,
                "event_id": record.event_id,
                "correlation_id": record.correlation_id,
                "started_at": _naive(record.started_at),
                "seconds": record.seconds,
                "run_key": record.run_key,
            }
        )
        try:
            doc.insert(ignore_permissions=True)
        except _UNIQUE as exc:
            raise Duplicate(str(exc)) from exc

    def case_runs(self, tenant_id: str, case_id: str) -> list[RunRecord]:
        rows = frappe.get_all(
            RUN,
            filters={"tenant_id": tenant_id, "case_id": case_id},
            fields=list(self._RUN_FIELDS),
            order_by="creation asc",
        )
        return [self._run(row) for row in rows]


# --- entry point --------------------------------------------------------------------


def on_submission_sent(doc: Any, method: str | None = None) -> None:
    """``doc_events``: a sent refund claim enqueues ``assurance.requested`` first."""
    before = doc.get_doc_before_save()
    origin = bridge_rules.submission_origin(bool(doc.flags.get("vati_mirror")), doc.get("source"))
    if before is None or not bridge_rules.should_register(before.status, doc.status, origin):
        return
    if doc.submission_type != "refund_claim":
        return
    # Same request as the bridge's own registration, so the bridge replays it.
    case = mapping.register_case(
        tenant_id=doc.tenant_id,
        idempotency_key=bridge_rules.case_registration_key(doc.name),
        case_id=doc.case_id or None,
        taxpayer_id=doc.taxpayer_id,
        submission_id=doc.name,
    )
    correlation = str(uuid.uuid4())
    # The bridge's case.registered for this send reuses it: one ID traces the claim.
    doc.flags[CORRELATION_FLAG] = correlation
    key, payload = rules.request_event(doc.name, int(doc.version))
    event = outbox_store.enqueue(
        tenant_id=doc.tenant_id,
        case_id=case["case_id"],
        type=rules.EVENT_REQUESTED,
        payload=payload,
        idempotency_key=key,
        correlation_id=correlation,
        source_service=rules.SOURCE_SERVICE,
    )
    log("enqueue", "replayed" if event["replayed"] else "enqueued",
        tenant_id=doc.tenant_id, case_id=case["case_id"], event_id=event["event_id"])  # fmt: skip


# --- handler ------------------------------------------------------------------------


def _bundle(tenant_id: str, taxpayer_id: str) -> tuple[str, str, dict[str, Any]] | None:
    """The taxpayer's latest stored records document, re-hashed and parsed."""
    rows = frappe.get_all(
        EVIDENCE,
        filters={
            "tenant_id": tenant_id,
            "taxpayer_id": taxpayer_id,
            "reference_doctype": TAXPAYER,
            "reference_name": taxpayer_id,
            "document_type": rules.BUNDLE_DOCUMENT_TYPE,
            "media_type": "text/csv",
        },
        fields=["name", "document_sha256", "file_ref"],
        order_by="received_at desc, version desc",
        limit=1,
    )
    if not rows:
        return None
    row = rows[0]
    path = frappe.get_doc("File", row.file_ref).get_full_path()
    with open(path, "rb") as handle:
        content = handle.read()
    if hashlib.sha256(content).hexdigest() != row.document_sha256:
        raise outbox_policy.PermanentDeliveryError("evidence_original_changed")
    return row.name, row.document_sha256, rules.parse_bundle(content)


def _not_used() -> dict[str, str]:
    configured = config.configured(frappe.conf)
    return {name: "configured" if configured.get(name) else "not_configured" for name in NOT_USED}


def handle_assurance_requested(delivery: Any) -> None:
    """Outbox handler for ``assurance.requested`` (see the module docstring)."""
    clock = time.perf_counter()
    started = _utc()
    env = delivery.envelope
    try:
        submission_id, version = rules.parse_request(env.payload)
    except InvalidMapping:
        raise outbox_policy.PermanentDeliveryError("invalid_payload") from None
    sub = frappe.db.get_value(
        SUBMISSION, submission_id,
        ["name", "tenant_id", "taxpayer_id", "case_id", "version", "period", "submission_type",
         "source", "source_ref"],
        as_dict=True,
    )  # fmt: skip
    if not sub or sub.tenant_id != env.tenant_id or sub.case_id != env.case_id:
        raise outbox_policy.PermanentDeliveryError("submission_not_found")
    repo = FrappeRepository()
    key = engine.run_key(env.tenant_id, env.event_id)
    if repo.run_by_key(key) is not None:
        return  # this event already ran (a replay)
    services: dict[str, Any] = {"frappe": "live", "not_used_by_this_step": _not_used()}
    run: dict[str, Any] = {
        "claim_ref": None, "checks": {c: "not_run" for c in rules.CHECKS}, "summary": {},
        "finding_ids": (), "lane_proposal_id": None, "producer_version": None,
        "input_digest": None,
    }  # fmt: skip

    def finish(status: str, reason: str | None) -> None:
        record = RunRecord(
            run_id=str(uuid.uuid4()),
            tenant_id=env.tenant_id,
            case_id=env.case_id,
            submission_id=sub.name,
            status=status,
            reason=reason,
            services=services,
            event_id=env.event_id,
            correlation_id=env.correlation_id,
            started_at=started,
            seconds=round(time.perf_counter() - clock, 4),
            run_key=key,
            **run,
        )
        engine.record_run(repo, record)
        log("run", status, tenant_id=env.tenant_id, case_id=env.case_id, reason=reason,
            findings=len(record.finding_ids))  # fmt: skip

    if int(sub.version or 0) != version:
        services["rsl_sources"] = {"mode": "not_read"}
        finish("superseded", "newer_send_pending")
        return
    claim = frappe.db.get_value(CLAIM, {"submission_id": sub.name}, ["claimed_amount"],
                                as_dict=True)  # fmt: skip
    tin = frappe.db.get_value(TAXPAYER, sub.taxpayer_id, "tin")
    try:
        fixture = sources_for(env.tenant_id)
    except config.ServiceNotConfigured as exc:
        services["rsl_sources"] = {"mode": "unavailable", "reason": exc.reason}
        finish("not_run", "upstream_unavailable")
        return
    services["rsl_sources"] = {
        "mode": fixture.mode,
        "fixture": True,
        "adapter": fixture.adapter_id,
        "adapter_version": fixture.adapter_version,
        "file_sha256": fixture.sha256,
    }
    bundle = _bundle(env.tenant_id, sub.taxpayer_id)
    if bundle is None or not claim or not tin:
        services["taxpayer_records"] = {"mode": "missing"}
        finish("not_run", "taxpayer_records_missing")
        return
    evidence_id, bundle_sha, records = bundle
    services["taxpayer_records"] = {"mode": "live", "evidence_id": evidence_id,
                                    "document_sha256": bundle_sha}  # fmt: skip
    if str(records.get("tin")) != str(tin):
        finish("not_run", "taxpayer_records_mismatch")
        return
    try:
        assess, register_version = _assess()
    except ImportError:
        services["checks"] = {"mode": "unavailable", "reason": "checks_not_installed"}
        finish("not_run", "checks_not_installed")
        return
    plan = rules.assess_case(
        fixture.sources, fixture.params, records, tin=str(tin), period=str(sub.period),
        claimed_amount=str(claim.claimed_amount), bundle_evidence_id=evidence_id,
        bundle_sha256=bundle_sha, assess=assess, register_version=register_version,
        claim_ref=sub.source_ref if sub.source == bridge_rules.ORIGIN_RSL_ONLINE_REFUNDS else None,
    )  # fmt: skip
    services["rsl_sources"]["systems"] = plan["systems"]
    run.update(checks=plan["checks"], claim_ref=plan["claim_ref"])
    if plan["status"] == "not_run":
        finish("not_run", plan["reason"])
        return
    version, digest = plan["version"], plan["input_digest"]
    services["checks"] = {"mode": "live", "pipeline_version": version,
                          "register_version": register_version}  # fmt: skip
    bodies, lane_body = plan["bodies"], plan["lane"]
    try:
        with pipeline_store.as_service():
            views = [
                findings_store.record_finding(
                    tenant_id=env.tenant_id, case_id=env.case_id, body=body,
                    idempotency_key=idem, correlation_id=env.correlation_id,
                )
                for body, idem in bodies
            ]  # fmt: skip
            lane = engine.record_lane_proposal(
                permissions.resolve_scope(env.tenant_id),
                repo,
                case_id=env.case_id,
                body=lane_body,
                producer_version=version,
                input_digest=digest,
                run_key=key,
                now=_utc(),
                correlation_id=env.correlation_id,
            )
    except MappingError as exc:
        # A refused record is a defect to inspect, not a retry: keep the code only.
        reason_code = exc.field_errors[0]["code"] if exc.field_errors else exc.code
        raise outbox_policy.PermanentDeliveryError(str(reason_code)) from None
    run.update(
        summary=plan["summary"],
        finding_ids=tuple(v["finding_id"] for v in views),
        lane_proposal_id=lane["proposal_id"],
        producer_version=version,
        input_digest=digest,
    )
    finish(plan["status"], plan["reason"])


# --- reads --------------------------------------------------------------------------


def case_assurance(
    *, tenant_id: str, case_id: str, include_superseded: bool = False
) -> dict[str, Any]:
    """Runs, lane proposals and ``vati_check`` findings of one case (staff and service)."""
    scope = permissions.resolve_scope(tenant_id)
    row = frappe.db.get_value(CASE, case_id, ["name", "tenant_id"], as_dict=True)
    case = {"case_id": row.name, "tenant_id": row.tenant_id} if row else None
    view = engine.case_assurance(
        scope, FrappeRepository(), case=case, include_superseded=include_superseded
    )
    findings = findings_store.list_findings(tenant_id=tenant_id, case_id=case_id,
                                            include_superseded=include_superseded)  # fmt: skip
    view["findings"] = [f for f in findings if f["producer"] == rules.PRODUCER]
    return view


def claim_lane(*, tenant_id: str, claim_ref: str) -> dict[str, Any]:
    """The current lane proposal for one Online Refunds Application claim."""
    scope = permissions.resolve_scope(tenant_id)
    rows = frappe.get_all(LANE, filters={"tenant_id": scope.tenant_id, "claim_ref": claim_ref},
                          pluck="case_id", order_by="creation desc", limit=1)  # fmt: skip
    if not rows:
        raise CaseNotFound("Claim not found.", "claim_ref", "not_found")
    view = case_assurance(tenant_id=tenant_id, case_id=rows[0])
    lanes = [lane for lane in view["lane_proposals"] if lane["claim_ref"] == claim_ref]
    if not lanes:
        raise CaseNotFound("Claim not found.", "claim_ref", "not_found")
    return lanes[-1]


def taxpayer_records_upload(
    *, tenant_id: str, taxpayer_id: str, records: Mapping[str, Any], idempotency_key: str
) -> dict[str, Any]:
    """Store the taxpayer's own records as one CSV evidence document (TAX-032 path)."""
    from vati_tax.evidence import store as evidence_store

    return evidence_store.register_upload(
        tenant_id=tenant_id,
        taxpayer_id=taxpayer_id,
        content=rules.bundle_csv(records),
        filename=rules.BUNDLE_FILENAME,
        document_type=rules.BUNDLE_DOCUMENT_TYPE,
        media_type="text/csv",
        idempotency_key=idempotency_key,
        reference_doctype=TAXPAYER,
        reference_name=taxpayer_id,
    )
