"""Frappe binding of the Frappe to Jube bridge (TAX-039).

Outward (submission -> Jube), all replay-safe:

1. ``on_submission_update`` (``doc_events`` on ``Vati Submission``): when the
   taxpayer sends a submission, register the canonical case (one per submission),
   record ``case_id`` on the submission, show the taxpayer ``under_review`` and
   enqueue one ``case.registered`` outbox event, all in the sender's transaction.
2. ``handle_case_registered`` (outbox handler, TAX-033): invoke the Jube risk
   model with the declared figures (effect ``jube.risk_invoke``), ask the TAX-034
   adapter for the case (effect ``jube.case``), then mirror what Jube holds. The
   effect ledger means a replay after a crash reuses completed calls; both calls
   are safe to repeat anyway (an extra archived evaluation; an adapter lookup).

Inward (Jube -> Frappe):

- ``apply_observation`` writes the staff-only ``Vati Case Sync`` and the
  submission's ``review_status``/``review_status_at`` with direct database
  writes. No document is saved, so no controller, ``doc_events`` hook or outbox
  event runs: a mirror write can never send anything back to Jube.
- ``receive_observation``: a posted ``case.status_projected`` envelope, deduplicated
  in ``Vati Bridge Inbound Event`` (same event and content -> replay; changed
  content -> ``idempotency_conflict``). Older observations are refused.
- ``poll_case_states`` (scheduler): Jube sends no webhooks (ADR-009), so open
  cases are read through the adapter and changes applied.

The Jube adapter and risk invoker live outside this app. A site names factories
in ``site_config.json``::

    "vati_case_lifecycle": "dotted.path.to.lifecycle_factory",   # shared with TAX-046
    "vati_risk_evaluator": "dotted.path.to.risk_factory"

``lifecycle_factory(tenant_id)`` returns the TAX-034 adapter (``ensure_case`` and
``get_case``). ``risk_factory(tenant_id)`` returns an object with
``invoke_declared(case_id, taxpayer_id, period_end, facts, correlation_id) -> str``
(``integration/jube/risk_invoke.py`` ``DeclaredRiskEvaluator``). Without them the
outbox event retries and then dead-letters with ``jube_not_configured``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

import frappe

from vati_tax.bridge import rules
from vati_tax.bridge.rules import Mirror, Observation, Projection
from vati_tax.ids import mapping
from vati_tax.ids.canonical import CaseNotFound, InvalidMapping, TenantBoundaryError
from vati_tax.outbox import policy as outbox_policy
from vati_tax.outbox import store as outbox_store
from vati_tax.submissions import domain

SUBMISSION = "Vati Submission"
REFUND_CLAIM = "Vati Refund Claim"
VAT_RETURN = "Vati VAT Return"
IMPORTED = "Vati Imported Service"
SYNC = "Vati Case Sync"
INBOUND = "Vati Bridge Inbound Event"
_UNIQUE = (frappe.UniqueValidationError, frappe.DuplicateEntryError)


class CaseLifecycle(Protocol):
    def ensure_case(
        self,
        tenant_id: str,
        case_id: str,
        *,
        correlation_id: str,
        source_event_id: str | None = None,
    ) -> Any: ...

    def get_case(self, tenant_id: str, case_id: str, *, correlation_id: str) -> Any: ...


class RiskEvaluator(Protocol):
    def invoke_declared(
        self,
        *,
        case_id: str,
        taxpayer_id: str,
        period_end: str,
        facts: Mapping[str, str],
        correlation_id: str,
    ) -> str: ...


_lifecycle_override: Callable[[str], CaseLifecycle] | None = None
_risk_override: Callable[[str], RiskEvaluator] | None = None


def set_factories(
    lifecycle: Callable[[str], CaseLifecycle] | None,
    risk: Callable[[str], RiskEvaluator] | None,
) -> None:
    """Tests and local smoke runs inject the Jube ports without site config."""
    global _lifecycle_override, _risk_override
    _lifecycle_override, _risk_override = lifecycle, risk


def _factory(override: Callable[[str], Any] | None, key: str, tenant_id: str) -> Any:
    if override is not None:
        return override(tenant_id)
    path = frappe.conf.get(key)
    if not path:
        raise outbox_policy.RetryableDeliveryError("jube_not_configured")
    return frappe.get_attr(path)(tenant_id)


def lifecycle_for(tenant_id: str) -> CaseLifecycle:
    return _factory(_lifecycle_override, "vati_case_lifecycle", tenant_id)


def risk_for(tenant_id: str) -> RiskEvaluator:
    return _factory(_risk_override, "vati_risk_evaluator", tenant_id)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _aware(value: Any) -> datetime | None:
    if not value:
        return None
    return frappe.utils.get_datetime(value).replace(tzinfo=UTC)


def log(action: str, outcome: str, **ids: object) -> None:
    # IDs and codes only: never amounts, statuses shown to taxpayers or upstream text.
    frappe.logger("vati_tax.bridge").info({"action": action, "outcome": outcome, **ids})


def _upstream(exc: BaseException) -> outbox_policy.DeliveryError:
    """Classify an adapter error by its code and reason; keep no message."""
    reason = getattr(exc, "reason", None) or getattr(exc, "code", None) or "handler_error"
    if getattr(exc, "retryable", False):
        return outbox_policy.RetryableDeliveryError(str(reason))
    return outbox_policy.PermanentDeliveryError(str(reason))


# --- outward ------------------------------------------------------------------------


def on_submission_update(doc: Any, method: str | None = None) -> None:
    """``doc_events`` hook: a taxpayer's send registers a case and enqueues work.

    Runs in the sender's transaction, so the case identity, the submission's
    ``case_id`` and the outbox event commit or roll back with the send itself.
    """
    before = doc.get_doc_before_save()
    origin = rules.submission_origin(bool(doc.flags.get("vati_mirror")), doc.get("source"))
    if before is None or not rules.should_register(before.status, doc.status, origin):
        return
    case = mapping.register_case(
        tenant_id=doc.tenant_id,
        idempotency_key=rules.case_registration_key(doc.name),
        case_id=doc.case_id or None,
        taxpayer_id=doc.taxpayer_id,
        submission_id=doc.name,
    )
    case_id = case["case_id"]
    values: dict[str, Any] = {}
    if not doc.case_id:
        values["case_id"] = case_id
    if not doc.review_status:
        values.update(review_status=rules.UNDER_REVIEW, review_status_at=_naive(utcnow()))
    if values:
        # Direct write: the controller already ran for this save, and these fields
        # are server-owned (vati_submission.MIRRORED and the write-once case_id).
        frappe.db.set_value(SUBMISSION, doc.name, values, update_modified=False)
        doc.update(values)
    if not frappe.db.exists(SYNC, case_id):
        frappe.get_doc(
            {
                "doctype": SYNC,
                "tenant_id": doc.tenant_id,
                "case_id": case_id,
                "submission_id": doc.name,
                "review_status": rules.UNDER_REVIEW,
            }
        ).insert(ignore_permissions=True)
    key, payload = rules.registration_event(doc.name, int(doc.version))
    event = outbox_store.enqueue(
        tenant_id=doc.tenant_id,
        case_id=case_id,
        type=rules.EVENT_REGISTERED,
        payload=payload,
        idempotency_key=key,
        # TAX-121: the assurance step enqueued just before for this send set it, so one
        # correlation ID traces the claim through both steps.
        correlation_id=doc.flags.get("vati_correlation_id") or str(uuid.uuid4()),
        source_service=rules.SOURCE_SUBMISSION,
    )
    log(
        "register", "enqueued", tenant_id=doc.tenant_id, case_id=case_id, event_id=event["event_id"]
    )


def _money(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return domain.parse_money(value, "amount", allow_negative=True)


def declared_facts(submission: Any) -> dict[str, str]:
    """Rule inputs from the submission's declared figures (no documents yet)."""
    claim = frappe.db.get_value(
        REFUND_CLAIM,
        {"submission_id": submission.name},
        ["claimed_amount", "vat_return_id"],
        as_dict=True,
    )
    vat_return_id = claim.vat_return_id if claim else None
    filters: dict[str, Any] = (
        {"name": vat_return_id} if vat_return_id else {"submission_id": submission.name}
    )
    filters.update(tenant_id=submission.tenant_id, taxpayer_id=submission.taxpayer_id)
    input_vat = frappe.db.get_value(VAT_RETURN, filters, "input_vat")
    treatments = frappe.get_all(
        IMPORTED,
        filters={
            "submission_id": submission.name,
            "tenant_id": submission.tenant_id,
            "taxpayer_id": submission.taxpayer_id,
        },
        pluck="declared_treatment",
    )
    return rules.declared_facts(
        refund_claimed=_money(claim.claimed_amount) if claim else None,
        input_vat_claimed=_money(input_vat),
        imported_treatments=treatments,
    )


def handle_case_registered(delivery: Any) -> None:
    """Outbox handler for ``case.registered``: risk evaluation, Jube case, mirror."""
    env = delivery.envelope
    try:
        submission_id, version = rules.parse_registration(env.payload)
    except InvalidMapping:
        raise outbox_policy.PermanentDeliveryError("invalid_payload") from None
    submission = frappe.db.get_value(
        SUBMISSION,
        submission_id,
        ["name", "tenant_id", "taxpayer_id", "case_id", "status", "version", "period_end"],
        as_dict=True,
    )
    if not submission or submission.tenant_id != env.tenant_id or submission.case_id != env.case_id:
        raise outbox_policy.PermanentDeliveryError("submission_not_found")
    correlation = env.correlation_id
    if int(submission.version or 0) == version:
        # An older send's event skips this: a later event carries the newer figures.
        facts = declared_facts(submission)
        period_end = str(submission.period_end)
        risk = risk_for(env.tenant_id)

        def invoke(_key: str) -> str:
            try:
                return risk.invoke_declared(
                    case_id=env.case_id,
                    taxpayer_id=submission.taxpayer_id,
                    period_end=period_end,
                    facts=facts,
                    correlation_id=correlation,
                )
            except outbox_policy.DeliveryError:
                raise
            except Exception as exc:  # adapter errors carry code/reason/retryable
                raise _upstream(exc) from None

        # Repeating an invocation only archives one more evaluation of the same
        # facts in Jube; it creates and changes no case.
        delivery.effect("jube.risk_invoke", invoke, idempotent=True)
    lifecycle = lifecycle_for(env.tenant_id)

    def ensure(_key: str) -> str:
        try:
            outcome = lifecycle.ensure_case(
                env.tenant_id, env.case_id, correlation_id=correlation, source_event_id=None
            )
        except outbox_policy.DeliveryError:
            raise
        except Exception as exc:
            raise _upstream(exc) from None
        return str(outcome.case.jube_case_id)

    # The adapter looks the case up (link, then Jube by key) before creating it.
    delivery.effect("jube.case", ensure, idempotent=True)
    try:
        case = lifecycle.get_case(env.tenant_id, env.case_id, correlation_id=correlation)
    except Exception as exc:
        raise _upstream(exc) from None
    seen = rules.projection(case.jube_case_id, case.status, case.escalated, _closed(case))
    apply_observation(rules.observe(env.tenant_id, env.case_id, seen, utcnow()))
    log("deliver", "mirrored", tenant_id=env.tenant_id, case_id=env.case_id)


def _closed(case: Any) -> bool:
    return getattr(case, "closed_state", None) == "closed"


# --- inward -------------------------------------------------------------------------


def _mirror(case_id: str) -> tuple[Any, Mirror | None]:
    row = frappe.db.get_value(
        SYNC,
        case_id,
        [
            "name",
            "tenant_id",
            "submission_id",
            "jube_case_id",
            "case_status",
            "escalated",
            "closed",
            "observed_at",
        ],
        as_dict=True,
    )
    if not row:
        return None, None
    seen = None
    if row.jube_case_id and row.case_status:
        seen = Projection(
            str(row.jube_case_id), row.case_status, bool(row.escalated), bool(row.closed)
        )
    return row, Mirror(row.jube_case_id or None, seen, _aware(row.observed_at))


def _linked_jube_case(tenant_id: str, case_id: str) -> str | None:
    for link in mapping.case_service_links(tenant_id=tenant_id, case_id=case_id):
        if link.get("service") == "jube" and link.get("external_type") == "case":
            return str(link["external_id"])
    return None


def _scope_case(tenant_id: str, case_id: str) -> Any:
    case = frappe.db.get_value(mapping.CASE, case_id, ["tenant_id", "submission_id"], as_dict=True)
    if case is None:
        raise CaseNotFound("Case not found.", "case_id", "not_found")
    if case.tenant_id != tenant_id:
        raise TenantBoundaryError("Case not found.", "case_id", "not_found")
    return case


def apply_observation(seen: Observation) -> dict[str, Any]:
    """Mirror one observation of Jube state (no commit; the caller's transaction).

    Never saves a document, so no controller, hook or outbox event runs.
    """
    case = _scope_case(seen.tenant_id, seen.case_id)
    row, stored = _mirror(seen.case_id)
    decision = rules.decide_observation(
        seen, stored, _linked_jube_case(seen.tenant_id, seen.case_id)
    )
    at = _naive(seen.observed_at)
    review = seen.projection.review_status
    values = {
        "jube_case_id": seen.projection.jube_case_id,
        "case_status": seen.projection.status,
        "escalated": int(seen.projection.escalated),
        "closed": int(seen.projection.closed),
        "review_status": review,
        "observed_at": at,
        "last_event_id": seen.event_id,
        "last_error_code": None,
    }
    if row is None:
        frappe.get_doc(
            {
                "doctype": SYNC,
                "tenant_id": seen.tenant_id,
                "case_id": seen.case_id,
                "submission_id": case.submission_id,
                **values,
            }
        ).insert(ignore_permissions=True)
    else:
        frappe.db.set_value(SYNC, seen.case_id, values)
    submission_id = case.submission_id or (row.submission_id if row else None)
    if submission_id and decision == "apply":
        current = frappe.db.get_value(SUBMISSION, submission_id, "review_status")
        if current != review:
            # Direct write on purpose: no controller, no doc_events, no outbox.
            frappe.db.set_value(
                SUBMISSION,
                submission_id,
                {"review_status": review, "review_status_at": at},
                update_modified=False,
            )
    log(
        "observe",
        decision,
        tenant_id=seen.tenant_id,
        case_id=seen.case_id,
        event_id=seen.event_id,
    )
    return {
        "case_id": seen.case_id,
        "outcome": "applied" if decision == "apply" else "unchanged",
        "review_status": review,
    }


def receive_observation(envelope: Any, *, tenant_id: str, actor: str) -> dict[str, Any]:
    """A posted ``case.status_projected`` envelope for ``tenant_id`` (already authorised).

    Same event and content: the stored outcome is returned with ``replayed``.
    Same event, changed content: ``idempotency_conflict``. An envelope for another
    tenant is ``not_found``, exactly like a missing case.
    """
    seen = rules.parse_envelope(envelope)
    if seen.tenant_id != tenant_id:
        raise TenantBoundaryError("Case not found.", "case_id", "not_found")
    key = rules.inbound_key(seen.tenant_id, seen.event_id)
    stored = frappe.db.get_value(
        INBOUND, {"inbound_key": key}, ["request_digest", "outcome", "case_id"], as_dict=True
    )
    if rules.decide_inbound(seen.digest, stored.request_digest if stored else None) == "replay":
        log("receive", "replayed", tenant_id=seen.tenant_id, event_id=seen.event_id)
        return {"case_id": stored.case_id, "outcome": stored.outcome, "replayed": True}
    savepoint = "vati_bridge_" + uuid.uuid4().hex[:12]
    frappe.db.savepoint(savepoint)
    try:
        result = apply_observation(seen)
        frappe.get_doc(
            {
                "doctype": INBOUND,
                "tenant_id": seen.tenant_id,
                "case_id": seen.case_id,
                "event_id": seen.event_id,
                "outcome": result["outcome"],
                "received_at": _naive(utcnow()),
                "received_by": actor,
                "inbound_key": key,
                "request_digest": seen.digest,
            }
        ).insert(ignore_permissions=True)
    except _UNIQUE:
        # A concurrent delivery of the same event won: answer as a replay.
        frappe.db.rollback(save_point=savepoint)
        return receive_observation(envelope, tenant_id=tenant_id, actor=actor)
    except BaseException:
        frappe.db.rollback(save_point=savepoint)
        raise
    return {**result, "replayed": False}


def poll_case_states(limit: int = 50) -> dict[str, int]:
    """Read open cases from Jube and mirror any change (scheduler, every 5 minutes).

    Commits per case, so one failing case never blocks the others. Errors keep
    only their code on the sync record.
    """
    counts: dict[str, int] = {}
    rows = frappe.get_all(
        SYNC,
        filters={"closed": 0, "jube_case_id": ["is", "set"]},
        fields=["case_id", "tenant_id", "case_status", "escalated", "closed", "jube_case_id"],
        order_by="last_polled_at asc",
        limit_page_length=limit,
    )
    for row in rows:
        outcome = _poll_one(row)
        counts[outcome] = counts.get(outcome, 0) + 1
        frappe.db.commit()
    if counts:
        log("poll", "ran", **counts)
    return counts


def _poll_one(row: Any) -> str:
    now = utcnow()
    correlation = str(uuid.uuid4())
    try:
        case = lifecycle_for(row.tenant_id).get_case(
            row.tenant_id, row.case_id, correlation_id=correlation
        )
        seen = rules.projection(case.jube_case_id, case.status, case.escalated, _closed(case))
        before = Projection(
            str(row.jube_case_id), row.case_status, bool(row.escalated), bool(row.closed)
        )
        if seen == before:
            frappe.db.set_value(
                SYNC, row.case_id, {"last_polled_at": _naive(now), "last_error_code": None}
            )
            return "unchanged"
        apply_observation(rules.observe(row.tenant_id, row.case_id, seen, now))
        frappe.db.set_value(SYNC, row.case_id, {"last_polled_at": _naive(now)})
        return "applied"
    except Exception as exc:
        frappe.db.rollback()
        code = getattr(exc, "reason", None) or getattr(exc, "code", None) or "poll_error"
        frappe.db.set_value(
            SYNC,
            row.case_id,
            {"last_polled_at": _naive(now), "last_error_code": str(code)[:64]},
        )
        log("poll", "failed", tenant_id=row.tenant_id, case_id=row.case_id, error_code=code)
        return "failed"


def poll_scheduled() -> None:
    """Scheduler entry point (hooks.py ``scheduler_events``)."""
    if frappe.conf.get("vati_case_lifecycle") or _lifecycle_override is not None:
        poll_case_states()


# --- taxpayer view ------------------------------------------------------------------


def taxpayer_view(submission_id: str) -> dict[str, Any]:
    """The bridge's part of a taxpayer's submission view: one status word and when."""
    row = frappe.db.get_value(
        SUBMISSION, submission_id, ["status", "review_status", "review_status_at"], as_dict=True
    )
    if not row:
        raise CaseNotFound("Submission not found.", "submission_id", "not_found")
    at = _aware(row.review_status_at)
    return {
        "review_status": rules.taxpayer_status(row.status, row.review_status),
        "review_status_at": rules.iso(at) if at else None,
    }
