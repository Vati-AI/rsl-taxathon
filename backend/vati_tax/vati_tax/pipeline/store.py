"""Frappe binding of the TAX-087 event wiring (ADR-037).

Entry points (all in the caller's transaction, so an event commits with the change
that caused it, or not at all):

- ``on_submission_sent`` (``doc_events`` on ``Vati Submission``, after the TAX-039
  bridge registered the case): every evidence document the refund claim refers to is
  linked to the case (a ``frappe``/``Vati Evidence`` service link, the TAX-032 way)
  and gets one ``evidence.received`` event.
- ``on_service_link_insert`` (``doc_events`` on ``Vati Service Link``): any evidence
  that becomes linked to a case later (an officer's upload with ``case_id``, a
  response to an evidence request) gets its ``evidence.received`` event too.

Outbox handlers (``vati_outbox_handlers`` in hooks.py; TAX-033 delivers them in case
order, retries with backoff and dead-letters):

- ``handle_evidence_received``: Docling converts the stored original and the result is
  recorded as an extraction revision (TAX-038 adapter, TAX-032 ``record_extraction``).
  The Docling submission is an outbox *effect* (not idempotent): its task ID is kept
  before the conversion is awaited, so a replay resumes the same task. Links:
  ``docling``/``conversion_job`` and ``docling``/``extraction`` (with the event ID).
  Then ``extraction.completed`` or ``extraction.failed`` is enqueued.
- ``handle_extraction_settled`` (both types): the case's taxpayer, invoice schedule and
  evidence are mapped to FtM entities (TAX-036); entities not yet in the site's
  OpenAleph collection are written and recorded in ``Vati Entity Index``; parties not
  yet screened on yente's current dataset version are matched (TAX-037) and their
  candidates stored, unreviewed, in ``Vati Screening Result``. Links:
  ``openaleph``/``document`` for the evidence documents.

Jube risk and case work stays in the TAX-039 ``case.registered`` handler.

The handlers act as the site's pipeline service identity (site config
``vati_pipeline_service_user``, a user with only ``Vati Service`` memberships), because
the evidence store authorises the session user. Services are built by the TAX-085
factories from ``vati_services``; an unconfigured or unreachable service is a retryable
``<service>_...`` failure that retries and then dead-letters for an operator.
``case_status`` shows operators the whole chain for one case.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import frappe

from vati_tax.bridge import rules as bridge_rules
from vati_tax.evidence import store as evidence_store
from vati_tax.identity.permissions import clear_principal_cache
from vati_tax.ids import mapping
from vati_tax.ids.canonical import CaseNotFound, InvalidMapping, MappingError, TenantBoundaryError
from vati_tax.integrations import config, factories
from vati_tax.outbox import policy as outbox_policy
from vati_tax.outbox import store as outbox_store
from vati_tax.pipeline import rules

SUBMISSION = "Vati Submission"
CLAIM = "Vati Refund Claim"
CLAIM_ROW = "Vati Refund Claim Invoice"
TAXPAYER = "Vati Taxpayer"
EVIDENCE = "Vati Evidence"
EXTRACTION = "Vati Evidence Extraction"
CASE = "Vati Case Identity"
LINK = "Vati Service Link"
INDEX = "Vati Entity Index"
SCREENING = "Vati Screening Result"
SERVICE_USER_KEY = "vati_pipeline_service_user"
DOCLING_SUBMIT = "docling.submit"
_UNIQUE = (frappe.UniqueValidationError, frappe.DuplicateEntryError)
# Docling answered (or refused the connection): the submission certainly made no task.
_NOT_ACCEPTED = frozenset({"upstream_status", "unreachable", "authentication_failed",
                           "invalid_response"})  # fmt: skip

_docling: Callable[..., Any] | None = None
_openaleph: Callable[[str], Any] | None = None
_yente: Callable[[str], Any] | None = None
_service_user: str | None = None


def set_services(
    *,
    docling: Callable[..., Any] | None = None,
    openaleph: Callable[[str], Any] | None = None,
    yente: Callable[[str], Any] | None = None,
    service_user: str | None = None,
) -> None:
    """Tests and the verify-site demo inject service factories and the service user."""
    global _docling, _openaleph, _yente, _service_user
    _docling, _openaleph, _yente, _service_user = docling, openaleph, yente, service_user


def docling_for(tenant_id: str, *, jobs: Any, sink: Any) -> Any:
    if _docling is not None:
        return _docling(tenant_id, jobs=jobs, sink=sink)
    return factories.docling_extraction(tenant_id, jobs=jobs, sink=sink)


def openaleph_for(tenant_id: str) -> Any:
    return (
        _openaleph(tenant_id) if _openaleph is not None else factories.openaleph_ingest(tenant_id)
    )


def yente_for(tenant_id: str) -> Any:
    return _yente(tenant_id) if _yente is not None else factories.yente_matcher(tenant_id)


def log(action: str, outcome: str, **ids: object) -> None:
    # IDs, codes and counts only: never document text, amounts or upstream bodies.
    frappe.logger("vati_tax.pipeline").info({"action": action, "outcome": outcome, **ids})


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@contextmanager
def as_service() -> Iterator[str]:
    """Run as the pipeline service identity, then restore the previous session user."""
    user = _service_user or frappe.conf.get(SERVICE_USER_KEY)
    if not user or not frappe.db.exists("User", user):
        raise outbox_policy.RetryableDeliveryError("service_identity_not_configured")
    previous = frappe.session.user
    frappe.set_user(user)
    clear_principal_cache()
    try:
        yield user
    finally:
        frappe.set_user(previous)
        clear_principal_cache()


# --- entry points -------------------------------------------------------------------


def _case_correlation(case_id: str) -> str | None:
    """The correlation ID of the case's registration, so one ID traces the whole claim."""
    rows = frappe.get_all(
        outbox_store.EVENT,
        filters={"case_id": case_id, "event_type": "case.registered"},
        pluck="correlation_id",
        order_by="sequence asc",
        limit=1,
    )
    return rows[0] if rows else None


def enqueue_evidence(
    tenant_id: str, case_id: str, evidence_id: str, correlation_id: str | None = None
) -> dict[str, Any] | None:
    """One ``evidence.received`` event for an evidence document of a case (replay-safe)."""
    sha = frappe.db.get_value(EVIDENCE, {"name": evidence_id, "tenant_id": tenant_id},
                              "document_sha256")  # fmt: skip
    if not sha:
        return None
    key, payload = rules.evidence_event(case_id, evidence_id, sha)
    event = outbox_store.enqueue(
        tenant_id=tenant_id,
        case_id=case_id,
        type=rules.EVIDENCE_RECEIVED,
        payload=payload,
        idempotency_key=key,
        correlation_id=correlation_id or _case_correlation(case_id) or str(uuid.uuid4()),
        source_service=rules.SOURCE_FRAPPE,
    )
    log("enqueue_evidence", "replayed" if event["replayed"] else "enqueued",
        tenant_id=tenant_id, case_id=case_id, evidence_id=evidence_id)  # fmt: skip
    return event


def on_service_link_insert(doc: Any, method: str | None = None) -> None:
    """``doc_events``: evidence newly linked to a case flows into extraction."""
    if doc.service == "frappe" and doc.external_type == EVIDENCE:
        enqueue_evidence(doc.tenant_id, doc.case_id, doc.external_id)


def on_submission_sent(doc: Any, method: str | None = None) -> None:
    """``doc_events``: runs after the bridge registered the case for a sent submission."""
    before = doc.get_doc_before_save()
    origin = bridge_rules.submission_origin(bool(doc.flags.get("vati_mirror")), doc.get("source"))
    if before is None or not bridge_rules.should_register(before.status, doc.status, origin):
        return
    if not doc.case_id:
        return
    claim = frappe.db.get_value(CLAIM, {"submission_id": doc.name}, ["name", "evidence_ids"],
                                as_dict=True)  # fmt: skip
    if not claim:
        return
    rows = frappe.get_all(
        CLAIM_ROW,
        filters={"parent": claim.name, "parenttype": CLAIM},
        fields=["invoice_evidence_id", "contract_evidence_id"],
    )
    try:
        listed = json.loads(claim.evidence_ids or "[]")
    except ValueError:
        listed = []
    correlation = _case_correlation(doc.case_id)
    for evidence_id in rules.claim_evidence_ids(listed, rows):
        owner = frappe.db.get_value(EVIDENCE, evidence_id, ["tenant_id", "taxpayer_id", "case_id"],
                                    as_dict=True)  # fmt: skip
        if not owner or owner.tenant_id != doc.tenant_id or owner.taxpayer_id != doc.taxpayer_id:
            continue  # the claim controller refuses foreign documents already
        if owner.case_id and owner.case_id != doc.case_id:
            continue  # registered for another case
        try:
            mapping.record_link(
                tenant_id=doc.tenant_id,
                case_id=doc.case_id,
                service="frappe",
                external_type=EVIDENCE,
                external_id=evidence_id,
            )
        except MappingError as exc:
            # Linked to another case already: it belongs there, not here.
            log("link_evidence", exc.code, tenant_id=doc.tenant_id, case_id=doc.case_id,
                evidence_id=evidence_id)  # fmt: skip
            continue
        enqueue_evidence(doc.tenant_id, doc.case_id, evidence_id, correlation)


# --- shared helpers -----------------------------------------------------------------


def _scope_case(tenant_id: str, case_id: str) -> Any:
    case = frappe.db.get_value(CASE, case_id, ["tenant_id", "taxpayer_id", "submission_id"],
                               as_dict=True)  # fmt: skip
    if case is None or case.tenant_id != tenant_id:
        raise outbox_policy.PermanentDeliveryError("case_not_found")
    return case


def _case_evidence(tenant_id: str, case_id: str) -> list[Any]:
    """Evidence registered for the case or linked to it (same rule as evidence.case_evidence)."""
    direct = frappe.get_all(EVIDENCE, filters={"tenant_id": tenant_id, "case_id": case_id},
                            pluck="name")  # fmt: skip
    linked = frappe.get_all(
        LINK,
        filters={
            "tenant_id": tenant_id,
            "case_id": case_id,
            "service": "frappe",
            "external_type": EVIDENCE,
        },  # fmt: skip
        pluck="external_id",
    )
    names = sorted(set(direct) | set(linked))
    if not names:
        return []
    return frappe.get_all(
        EVIDENCE,
        filters={"name": ["in", names], "tenant_id": tenant_id},
        fields=[
            "name as evidence_id",
            "document_sha256",
            "media_type",
            "size_bytes",
            "original_filename",
            "document_type",
            "received_at",
        ],  # fmt: skip
        order_by="name asc",
    )


def _link(tenant_id: str, case_id: str, service: str, external_type: str, external_id: str,
          source_event_id: str | None = None) -> None:  # fmt: skip
    """Record a service link; an upstream record already mapped elsewhere is only logged."""
    try:
        mapping.record_link(
            tenant_id=tenant_id,
            case_id=case_id,
            service=service,
            external_type=external_type,
            external_id=external_id,
            source_event_id=source_event_id,
        )
    except (InvalidMapping, CaseNotFound, TenantBoundaryError):
        raise
    except MappingError as exc:
        log("record_link", exc.code, tenant_id=tenant_id, case_id=case_id, service=service)


# --- evidence.received: Docling extraction ----------------------------------------


class _Jobs:
    """Docling job store for one delivery. The durable copy of the task ID is the
    ``docling.submit`` outbox effect; this only hands it to ``extract`` to resume."""

    def __init__(self) -> None:
        self.tasks: dict[str, str] = {}

    def get(self, job_key: str) -> str | None:
        return self.tasks.get(job_key)

    def put(self, job_key: str, task_id: str) -> None:
        self.tasks[job_key] = task_id

    def clear(self, job_key: str) -> None:
        self.tasks.pop(job_key, None)


def _forget_effect(delivery: Any, name: str) -> None:
    """Drop this event's ledger row for ``name`` after Docling showed it holds no task
    for it (it refused the submission, or lost the task). The next attempt submits
    once more; a timeout keeps the row, so an unknown outcome still dead-letters."""
    key = outbox_policy.effect_key(delivery.envelope.tenant_id, delivery.event, name)
    row = frappe.db.get_value(outbox_store.EFFECT, {"effect_key": key}, "name")
    if row:
        frappe.delete_doc(outbox_store.EFFECT, row, ignore_permissions=True, force=True)
        frappe.db.commit()


def _original(evidence: Any) -> bytes:
    """The stored original, re-hashed: the extraction must come from exactly these bytes."""
    path = frappe.get_doc("File", evidence.file_ref).get_full_path()
    with open(path, "rb") as handle:
        content = handle.read()
    if hashlib.sha256(content).hexdigest() != evidence.document_sha256:
        raise outbox_policy.PermanentDeliveryError("evidence_original_changed")
    return content


def _latest_extraction(evidence_id: str) -> Any:
    rows = frappe.get_all(
        EXTRACTION,
        filters={"evidence_id": evidence_id, "extractor": "docling"},
        fields=["name", "status"],
        order_by="revision desc",
        limit=1,
    )
    return rows[0] if rows else None


def handle_evidence_received(delivery: Any) -> None:
    """Outbox handler for ``evidence.received``."""
    env = delivery.envelope
    try:
        evidence_id, sha = rules.parse_evidence_event(env.payload)
    except InvalidMapping:
        raise outbox_policy.PermanentDeliveryError("invalid_payload") from None
    _scope_case(env.tenant_id, env.case_id)
    evidence = frappe.db.get_value(
        EVIDENCE,
        evidence_id,
        ["name", "tenant_id", "document_sha256", "media_type", "file_ref"],
        as_dict=True,
    )
    if not evidence or evidence.tenant_id != env.tenant_id or evidence.document_sha256 != sha:
        raise outbox_policy.PermanentDeliveryError("evidence_not_found")
    if evidence_id not in {e.evidence_id for e in _case_evidence(env.tenant_id, env.case_id)}:
        raise outbox_policy.PermanentDeliveryError("evidence_not_in_case")

    latest = _latest_extraction(evidence_id)
    if latest:
        # Already extracted (a replay, or the same document reached another event).
        settled = rules.Settled(evidence_id, latest.name, latest.status)
        task_id = None
    else:
        settled, task_id = _extract(delivery, evidence)
    if task_id:
        _link(env.tenant_id, env.case_id, "docling", "conversion_job", task_id)
    _link(env.tenant_id, env.case_id, "docling", "extraction", settled.extraction_id,
          source_event_id=delivery.event)  # fmt: skip
    event_type, key, payload = rules.settled_event(env.case_id, settled)
    outbox_store.enqueue(
        tenant_id=env.tenant_id,
        case_id=env.case_id,
        type=event_type,
        payload=payload,
        idempotency_key=key,
        correlation_id=env.correlation_id,
        source_service=rules.SOURCE_DOCLING,
    )
    log("extract", settled.status, tenant_id=env.tenant_id, case_id=env.case_id,
        evidence_id=evidence_id, extraction_id=settled.extraction_id,
        correlation_id=env.correlation_id)  # fmt: skip


def _extract(delivery: Any, evidence: Any) -> tuple[rules.Settled, str]:
    env = delivery.envelope
    content = _original(evidence)
    jobs = _Jobs()
    with as_service():
        try:
            adapter = docling_for(env.tenant_id, jobs=jobs, sink=evidence_store.record_extraction)
            module = config.adapter_module("extraction_adapter", "docling")
        except Exception as exc:
            raise rules.classify("docling", exc) from None

        def submit(_upstream_key: str) -> str:
            try:
                return str(adapter.submit(evidence_id=evidence.name,
                                          media_type=evidence.media_type, content=content))  # fmt: skip
            except Exception as exc:
                if getattr(exc, "reason", None) in _NOT_ACCEPTED:
                    _forget_effect(delivery, DOCLING_SUBMIT)
                raise rules.classify("docling", exc) from None

        # Not idempotent and not reconcilable by key: a submission whose outcome is
        # unknown (a timeout) dead-letters for an operator instead of converting twice.
        task_id = delivery.effect(DOCLING_SUBMIT, submit)
        key = module.job_key(env.tenant_id, evidence.name, evidence.document_sha256,
                             adapter.config.extractor_version)  # fmt: skip
        jobs.put(key, task_id)
        try:
            outcome = adapter.extract(
                tenant_id=env.tenant_id,
                evidence_id=evidence.name,
                document_sha256=evidence.document_sha256,
                media_type=evidence.media_type,
                content=content,
                correlation_id=env.correlation_id,
            )
        except outbox_policy.DeliveryError:
            raise
        except frappe.PermissionError:
            raise outbox_policy.PermanentDeliveryError("service_identity_forbidden") from None
        except Exception as exc:
            if getattr(exc, "reason", None) == "task_lost":
                _forget_effect(delivery, DOCLING_SUBMIT)
            raise rules.classify("docling", exc) from None
    return rules.Settled(evidence.name, outcome.recorded["extraction_id"], outcome.status), task_id


# --- extraction.completed / extraction.failed: entities and screening ------------------


def case_bundle(tenant_id: str, case_id: str) -> dict[str, Any]:
    """The FtM bundle for a case: taxpayer, refund-claim schedule and evidence."""
    case = _scope_case(tenant_id, case_id)
    claim = None
    if case.submission_id:
        claim = frappe.db.get_value(CLAIM, {"submission_id": case.submission_id},
                                    ["name", "currency"], as_dict=True)  # fmt: skip
    rows = []
    if claim:
        rows = frappe.get_all(
            CLAIM_ROW,
            filters={"parent": claim.name, "parenttype": CLAIM},
            fields=[
                "name",
                "idx",
                "invoice_number",
                "invoice_date",
                "supplier_name",
                "supplier_tin",
                "net_amount",
                "vat_amount",
                "gross_amount",
                "amount_paid",
                "invoice_evidence_id",
                "contract_evidence_id",
            ],  # fmt: skip
            order_by="idx asc",
        )
    taxpayer = frappe.db.get_value(
        TAXPAYER,
        {"name": case.taxpayer_id, "tenant_id": tenant_id},
        ["name", "display_name", "taxpayer_type", "tin"],
        as_dict=True,
    )
    if not taxpayer:
        raise outbox_policy.PermanentDeliveryError("taxpayer_not_found")
    return rules.claim_bundle(
        tenant_id=tenant_id,
        taxpayer=taxpayer,
        currency=(claim.currency if claim and claim.currency else "LSL"),
        rows=rows,
        evidence=_case_evidence(tenant_id, case_id),
    )


def handle_extraction_settled(delivery: Any) -> None:
    """Outbox handler for ``extraction.completed`` and ``extraction.failed``."""
    env = delivery.envelope
    try:
        rules.parse_settled_event(env.payload)
    except InvalidMapping:
        raise outbox_policy.PermanentDeliveryError("invalid_payload") from None
    bundle = case_bundle(env.tenant_id, env.case_id)
    try:
        tax_mapping = config.adapter_module("tax_mapping", "openaleph")
        mapped = tax_mapping.map_bundle(bundle)
    except config.ServiceNotConfigured as exc:
        raise rules.classify("openaleph", exc) from None
    except (tax_mapping.MappingError, tax_mapping.EntityValidationError) as exc:
        raise outbox_policy.PermanentDeliveryError(rules.error_code("ftm", exc)) from None
    indexed = _index(delivery, mapped)
    screened = _screen(delivery, mapped)
    log("index_and_screen", "done", tenant_id=env.tenant_id, case_id=env.case_id,
        correlation_id=env.correlation_id, **indexed, **screened)  # fmt: skip


def _index(delivery: Any, mapped: Any) -> dict[str, int]:
    env = delivery.envelope
    try:
        ingest = openaleph_for(env.tenant_id)
    except Exception as exc:
        raise rules.classify("openaleph", exc) from None
    collection = int(ingest.collection_id)
    ids = [str(e["id"]) for e in mapped.entities]
    rows = frappe.get_all(
        INDEX,
        filters={"tenant_id": env.tenant_id, "collection_id": collection, "entity_id": ["in", ids]},
        fields=["name", "entity_id", "entity_digest"],
    )
    ledger = {r.entity_id: r.entity_digest for r in rows}
    plan = rules.index_plan(mapped.entities, mapped.digests, ledger)
    schemata = {str(e["id"]): str(e["schema"]) for e in mapped.entities}

    def write(_upstream_key: str) -> str:
        # The bulk write is replay-safe (same IDs, same fragments), so it is an
        # idempotent effect. The ledger rows written here commit with the effect, so a
        # later failure of this event (yente) does not forget what OpenAleph accepted.
        try:
            ingest.ingest(plan.new, mapped.digests, ledger)
        except Exception as exc:
            raise rules.classify("openaleph", exc) from None
        now = utcnow()
        for entity in plan.new:
            entity_id = str(entity["id"])
            doc = frappe.get_doc(
                {
                    "doctype": INDEX,
                    "tenant_id": env.tenant_id,
                    "case_id": env.case_id,
                    "collection_id": collection,
                    "entity_id": entity_id,
                    "entity_schema": schemata[entity_id],
                    "entity_digest": mapped.digests[entity_id],
                    "indexed_at": now,
                    "event_id": delivery.event,
                    "correlation_id": env.correlation_id,
                    "index_key": rules.index_key(env.tenant_id, collection, entity_id),
                }
            )
            frappe.db.savepoint("vati_entity_index")
            try:
                doc.insert(ignore_permissions=True)
            except _UNIQUE:
                frappe.db.rollback(save_point="vati_entity_index")  # a concurrent run wrote it
        return f"collection:{collection}:{len(plan.new)}"

    if plan.new:
        delivery.effect("openaleph.ingest", write, idempotent=True)
    by_entity = {r.entity_id: r.name for r in rows}
    for entity_id in plan.changed:
        # Never re-sent: OpenAleph needs a reviewed re-index (TAX-036). Shown to operators.
        frappe.db.set_value(INDEX, by_entity[entity_id],
                            {"reindex_required": 1, "pending_digest": mapped.digests[entity_id]})  # fmt: skip
    for entity_id in rules.document_entities(mapped.entities):
        _link(env.tenant_id, env.case_id, "openaleph", "document", entity_id)
    return {"entities_new": len(plan.new), "entities_unchanged": len(plan.unchanged),
            "entities_changed": len(plan.changed)}  # fmt: skip


def _screen(delivery: Any, mapped: Any) -> dict[str, int]:
    env = delivery.envelope
    targets = rules.screening_targets(mapped.entities)
    if not targets:
        return {"parties_screened": 0}
    try:
        matcher = yente_for(env.tenant_id)
        review = config.adapter_module("candidate_review", "yente")
        version = matcher.dataset_version()
    except Exception as exc:
        raise rules.classify("yente", exc) from None

    def key(entity_id: str) -> str:
        return rules.screening_key(env.tenant_id, env.case_id, entity_id, version.name,
                                   version.version)  # fmt: skip

    pending = [t for t in targets
               if not frappe.db.exists(SCREENING, {"result_key": key(str(t["id"]))})]  # fmt: skip
    if not pending:
        return {"parties_screened": 0}

    def screen(_upstream_key: str) -> str:
        # Matching only reads yente, so the effect is idempotent; its rows commit with it.
        retrieved = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            sets = matcher.match(pending, retrieved_at=retrieved)
        except Exception as exc:
            raise rules.classify("yente", exc) from None
        for entity in pending:
            entity_id = str(entity["id"])
            found = sets[entity_id]
            candidates = [review.to_contract(c, None) for c in found.candidates]
            doc = frappe.get_doc(
                {
                    "doctype": SCREENING,
                    "tenant_id": env.tenant_id,
                    "case_id": env.case_id,
                    "entity_id": entity_id,
                    "entity_schema": str(entity["schema"]),
                    "dataset": found.dataset,
                    "dataset_version": found.dataset_version,
                    "candidate_count": len(candidates),
                    "above_threshold": sum(1 for c in found.candidates if c.above_threshold),
                    "ambiguous": int(found.ambiguous),
                    "candidates_json": json.dumps(candidates, sort_keys=True),
                    "retrieved_at": utcnow(),
                    "event_id": delivery.event,
                    "correlation_id": env.correlation_id,
                    "result_key": key(entity_id),
                }
            )
            frappe.db.savepoint("vati_screening_result")
            try:
                doc.insert(ignore_permissions=True)
            except _UNIQUE:
                frappe.db.rollback(save_point="vati_screening_result")
        return f"{version.name}@{version.version}:{len(pending)}"[:255]

    delivery.effect("yente.match", screen, idempotent=True)
    return {"parties_screened": len(pending)}


# --- operator view ------------------------------------------------------------------


def case_status(*, tenant_id: str, case_id: str) -> dict[str, Any]:
    """The whole pipeline for one case: events, effects, links, extractions, entities,
    screenings. IDs, states and codes only, for operators (``System Manager``)."""
    owner = frappe.db.get_value(CASE, case_id, "tenant_id")
    if owner is None or owner != tenant_id:
        raise CaseNotFound("Case not found.", "case_id", "not_found")
    events = frappe.get_all(
        outbox_store.EVENT,
        filters={"tenant_id": tenant_id, "case_id": case_id},
        fields=[
            "name as event_id",
            "event_type",
            "status",
            "attempts",
            "max_attempts",
            "last_error_code",
            "next_attempt_at",
            "correlation_id",
            "sequence",
            "manual_retries",
        ],  # fmt: skip
        order_by="sequence asc",
    )
    names = [e.event_id for e in events]
    effects = frappe.get_all(
        outbox_store.EFFECT,
        filters={"event": ["in", names or [""]]},
        fields=["event", "effect_name", "state", "result_ref"],
        order_by="creation asc",
    )
    links = frappe.get_all(
        LINK,
        filters={"tenant_id": tenant_id, "case_id": case_id},
        fields=["service", "external_type", "external_id", "source_event_id"],
        order_by="creation asc",
    )
    evidence = []
    for row in _case_evidence(tenant_id, case_id):
        latest = frappe.get_all(
            EXTRACTION,
            filters={"evidence_id": row.evidence_id},
            fields=["name", "revision", "status", "error_code", "item_count"],
            order_by="revision desc",
            limit=1,
        )
        evidence.append({"evidence_id": row.evidence_id,
                         "extraction": dict(latest[0]) if latest else None})  # fmt: skip
    entities = frappe.get_all(
        INDEX,
        filters={"tenant_id": tenant_id, "case_id": case_id},
        fields=["entity_id", "entity_schema", "reindex_required"],
        order_by="entity_id asc",
    )
    screenings = frappe.get_all(
        SCREENING,
        filters={"tenant_id": tenant_id, "case_id": case_id},
        fields=[
            "entity_id",
            "dataset",
            "dataset_version",
            "candidate_count",
            "above_threshold",
            "ambiguous",
        ],  # fmt: skip
        order_by="entity_id asc",
    )
    for event in events:
        event["sync"] = outbox_policy.SYNC_STATE.get(event["status"], "pending")
        if event.get("next_attempt_at"):
            event["next_attempt_at"] = str(event["next_attempt_at"])
    return {
        "case_id": case_id,
        "events": [dict(e) for e in events],
        "effects": [dict(e) for e in effects],
        "links": [dict(link) for link in links],
        "evidence": evidence,
        "entities_indexed": len(entities),
        "reindex_required": [e.entity_id for e in entities if e.reindex_required],
        "screenings": [dict(s) for s in screenings],
        "failing": [dict(e) for e in events if e["status"] in ("retry", "dead_letter")],
    }


# --- TAX-126: reading a response document before it is sent -------------------------


def request_draft_extraction(tenant_id: str, taxpayer_id: str, evidence_id: str) -> bool:
    """Queue one Docling read of a taxpayer's own unsent document (replay-safe).

    The portal's pre-send checks need the text of documents that are not yet part of a
    case, so they cannot take the ``evidence.received`` route. The job records an
    ordinary extraction revision; when the response is sent and the document joins the
    case, ``handle_evidence_received`` finds that revision and does not convert again.
    Returns whether a read was queued (False when one exists or the document is not
    the taxpayer's own unsent upload).
    """
    row = frappe.db.get_value(
        EVIDENCE,
        {"name": evidence_id, "tenant_id": tenant_id, "taxpayer_id": taxpayer_id},
        ["name", "case_id"],
        as_dict=True,
    )
    if not row or row.case_id or frappe.db.exists(EXTRACTION, {"evidence_id": evidence_id}):
        return False
    frappe.enqueue(
        "vati_tax.pipeline.store.extract_draft",
        queue="short",
        job_id=f"vati-draft-extraction-{evidence_id}",
        deduplicate=True,
        enqueue_after_commit=True,
        tenant_id=tenant_id,
        evidence_id=evidence_id,
    )
    return True


def extract_draft(tenant_id: str, evidence_id: str) -> str | None:
    """Background job: Docling reads one unsent document and the result is recorded.

    A failure to reach Docling records nothing; the checks keep saying the document is
    still being read, and the case pipeline reads it after the response is sent.
    """
    evidence = frappe.db.get_value(
        EVIDENCE,
        {"name": evidence_id, "tenant_id": tenant_id},
        ["name", "tenant_id", "case_id", "document_sha256", "media_type", "file_ref"],
        as_dict=True,
    )
    if not evidence or evidence.case_id or _latest_extraction(evidence_id):
        return None
    try:
        content = _original(evidence)
        with as_service():
            adapter = docling_for(tenant_id, jobs=_Jobs(), sink=evidence_store.record_extraction)
            outcome = adapter.extract(
                tenant_id=tenant_id,
                evidence_id=evidence.name,
                document_sha256=evidence.document_sha256,
                media_type=evidence.media_type,
                content=content,
                correlation_id=str(uuid.uuid4()),
            )
    except Exception as exc:  # noqa: BLE001 - advisory read; the case pipeline retries later
        log("extract_draft", type(exc).__name__, tenant_id=tenant_id, evidence_id=evidence_id)
        return None
    log("extract_draft", outcome.status, tenant_id=tenant_id, evidence_id=evidence_id)
    return str(outcome.status)
