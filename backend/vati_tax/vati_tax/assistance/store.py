"""Frappe ports for the taxpayer-assistance service (TAX-100).

``service()`` wires :class:`vati_tax.assistance.service.AssistanceService` to Frappe:

- ``caller``: the signed-in principal's TAX-029 memberships in the header tenant;
- ``records``: the taxpayer's own records only, each query filtered by tenant *and*
  taxpayer and selecting named columns, in the shape the assistant reads:
  submissions with their refund claim and invoice rows (``Vati Submission``,
  ``Vati Refund Claim``), documents with their latest TAX-038 extraction status and
  extracted fields (``Vati Evidence``, ``Vati Evidence Extraction``) and evidence
  requests in their taxpayer-facing form (``Vati Evidence Request``). Officer-side
  DocTypes (cases, findings, lanes, decisions, notes) are never queried;
- ``runtime``: ``assistance.runtime`` (the only module here that reaches ``ai``);
- ``drafts``: ``Vati Assistance Draft``;
- ``respond``: the unchanged TAX-045 ``responses.store.respond``, which checks the
  request and the documents, records the response, moves the request to
  ``responded`` and writes the ledger;
- ``ledger``: TAX-059 entries under the ``assistance`` subject type. Refusals and
  denials are committed at once, as ``audit_store.guarded`` does, because the
  /api/v1 route rolls back everything else a refused request wrote.
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from typing import Any

import frappe

from vati_tax.assistance import runtime
from vati_tax.assistance.service import AssistanceService, Caller, Ports, ResponseRejected
from vati_tax.audit import ledger
from vati_tax.audit import service as audit_service
from vati_tax.audit import store as audit_store
from vati_tax.identity import permissions, policy
from vati_tax.ids.canonical import MappingError
from vati_tax.responses import rules as response_rules
from vati_tax.responses import store as response_store

DRAFT = "Vati Assistance Draft"
SUBMISSION = "Vati Submission"
CLAIM = "Vati Refund Claim"
CLAIM_ROW = "Vati Refund Claim Invoice"
EVIDENCE = "Vati Evidence"
EXTRACTION = "Vati Evidence Extraction"
PROVENANCE = "Vati Extraction Provenance"
REQUEST = "Vati Evidence Request"
RESPONSE = "Vati Evidence Response"
TAXPAYER = "Vati Taxpayer"
EXTRACTION_STATUS = {"success": "completed", "partial": "completed", "failure": "failed"}
_JSON_FIELDS = ("data", "disclosure", "confirmation")


def _money(value: Any) -> str:
    return f"{Decimal(str(value or 0)):.2f}"


def _date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    iso = getattr(value, "isoformat", None)
    return str(iso()) if callable(iso) else str(value)


# --- caller ---------------------------------------------------------------------------


def caller(tenant_id: str) -> Caller:
    scope = permissions.resolve_scope(tenant_id)
    return Caller(
        scope.user, scope.tenant_id, frozenset(scope.roles), frozenset(scope.taxpayer_ids)
    )


# --- the taxpayer's own records -------------------------------------------------------


def _submissions(tenant_id: str, taxpayer_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    rows = frappe.get_all(
        SUBMISSION,
        filters={
            "tenant_id": tenant_id,
            "taxpayer_id": taxpayer_id,
            "submission_type": "refund_claim",
        },  # fmt: skip
        fields=["name", "status", "period_start", "period_end"],
    )
    for row in rows:
        claim = frappe.db.get_value(
            CLAIM,
            {"submission_id": row.name, "tenant_id": tenant_id, "taxpayer_id": taxpayer_id},
            ["name", "claimed_amount", "period_output_vat", "period_input_vat"],
            as_dict=True,
        )
        if not claim:
            continue
        invoices = frappe.get_all(
            CLAIM_ROW,
            filters={"parent": claim.name, "parenttype": CLAIM},
            fields=["idx", "invoice_number", "invoice_date", "supplier_name", "supplier_tin",
                    "net_amount", "vat_amount", "gross_amount", "amount_paid",
                    "invoice_evidence_id", "contract_evidence_id"],
            order_by="idx asc",
        )  # fmt: skip
        out[row.name] = {
            "submission_id": row.name,
            "taxpayer_id": taxpayer_id,
            "status": row.status,
            "period": {"start": _date(row.period_start), "end": _date(row.period_end)},
            "declared": {
                "claimed_refund": _money(claim.claimed_amount),
                "output_vat": _money(claim.period_output_vat),
                "input_vat_claimed": _money(claim.period_input_vat),
            },
            "invoices": [
                {
                    "invoice_number": n,
                    "supplier_invoice_number": r.invoice_number,
                    "invoice_date": _date(r.invoice_date),
                    "supplier_name": r.supplier_name,
                    "supplier_tin": r.supplier_tin,
                    "net_amount": _money(r.net_amount),
                    "vat_amount": _money(r.vat_amount),
                    "gross_amount": _money(r.gross_amount),
                    "amount_paid": _money(r.amount_paid) if r.amount_paid is not None else None,
                    "invoice_evidence_id": r.invoice_evidence_id or None,
                    "contract_evidence_id": r.contract_evidence_id or None,
                }
                for n, r in enumerate(invoices, start=1)
            ],
        }
    return out


def _documents(tenant_id: str, taxpayer_id: str) -> dict[str, Any]:
    rows = frappe.get_all(
        EVIDENCE,
        filters={"tenant_id": tenant_id, "taxpayer_id": taxpayer_id},
        fields=["name", "document_type", "original_filename", "document_sha256"],
    )
    latest: dict[str, Any] = {}
    if rows:
        for ext in frappe.get_all(
            EXTRACTION,
            filters={"evidence_id": ["in", [r.name for r in rows]], "tenant_id": tenant_id},
            fields=["name", "evidence_id", "revision", "status"],
            order_by="revision desc",
        ):
            latest.setdefault(ext.evidence_id, ext)
    fields: dict[str, dict[str, str]] = {}
    if latest:
        for item in frappe.get_all(
            PROVENANCE,
            filters={
                "parent": ["in", [e.name for e in latest.values()]],
                "parenttype": EXTRACTION,
                "extracted_field": ["is", "set"],
            },  # fmt: skip
            fields=["parent", "extracted_field", "text"],
            order_by="item_index asc",
        ):
            fields.setdefault(item.parent, {}).setdefault(item.extracted_field, item.text or "")
    out = {}
    for row in rows:
        ext = latest.get(row.name)
        out[row.name] = {
            "evidence_id": row.name,
            "taxpayer_id": taxpayer_id,
            "document_type": row.document_type,
            "file_name": row.original_filename or None,
            "document_sha256": row.document_sha256,
            "extraction_status": EXTRACTION_STATUS.get(ext.status, "pending") if ext else "pending",
            "extracted": fields.get(ext.name, {}) if ext else {},
        }
    return out


def _requests(tenant_id: str, taxpayer_id: str) -> dict[str, Any]:
    out = {}
    for row in frappe.get_all(
        REQUEST,
        filters={"tenant_id": tenant_id, "taxpayer_id": taxpayer_id},
        # Taxpayer-facing columns only: never case, finding or authoriser fields.
        fields=["name", "status", "due_date", "items", "message_to_taxpayer", "line_items"],
    ):
        out[row.name] = {
            "request_id": row.name,
            "taxpayer_id": taxpayer_id,
            "status": row.status,
            "due_date": _date(row.due_date),
            "request_text": row.message_to_taxpayer or "",
            "items": [
                {
                    "item_number": i.index + 1,
                    "document_type": i.document_type,
                    "description": i.description,
                }
                for i in response_rules.requested_items(row["items"], row["line_items"])
            ],  # fmt: skip
        }
    return out


def records(tenant_id: str, taxpayer_id: str) -> dict[str, Any]:
    name = frappe.db.get_value(TAXPAYER, {"name": taxpayer_id, "tenant_id": tenant_id},
                               "display_name")  # fmt: skip
    if name is None:
        return {"taxpayers": {}, "submissions": {}, "documents": {}, "evidence_requests": {}}
    return {
        "taxpayers": {
            taxpayer_id: {"taxpayer_id": taxpayer_id, "tenant_id": tenant_id, "name": name}
        },  # fmt: skip
        "submissions": _submissions(tenant_id, taxpayer_id),
        "documents": _documents(tenant_id, taxpayer_id),
        "evidence_requests": _requests(tenant_id, taxpayer_id),
        "validation_history": [],
    }


# --- drafts ---------------------------------------------------------------------------


def _record(doc: Any) -> dict[str, Any]:
    out = {k: doc.get(k) for k in ("draft_id", "tenant_id", "taxpayer_id", "request_id", "status",
                                   "idempotency_key", "request_digest", "confirm_key",
                                   "confirm_digest")}  # fmt: skip
    out["version"] = int(doc.get("version") or 1)
    for key in _JSON_FIELDS:
        raw = doc.get(key)
        out[key] = json.loads(raw) if raw else None
    return out


class FrappeDrafts:
    def get(self, tenant_id: str, taxpayer_id: str, draft_id: str) -> dict[str, Any] | None:
        name = frappe.db.get_value(
            DRAFT, {"name": draft_id, "tenant_id": tenant_id, "taxpayer_id": taxpayer_id}, "name"
        )
        return _record(frappe.get_doc(DRAFT, name)) if name else None

    def by_key(self, tenant_id: str, taxpayer_id: str, key: str) -> dict[str, Any] | None:
        name = frappe.db.get_value(DRAFT, {"registration_key": _registration(tenant_id,
                                                                             taxpayer_id, key)},
                                   "name")  # fmt: skip
        return _record(frappe.get_doc(DRAFT, name)) if name else None

    def save(self, record: dict[str, Any]) -> None:
        values = {
            **{k: record.get(k) for k in ("tenant_id", "taxpayer_id", "request_id", "status",
                                          "version", "idempotency_key", "request_digest",
                                          "confirm_key", "confirm_digest")},
            **{k: json.dumps(record.get(k), sort_keys=True) if record.get(k) is not None else None
               for k in _JSON_FIELDS},
        }  # fmt: skip
        if frappe.db.exists(DRAFT, record["draft_id"]):
            doc = frappe.get_doc(DRAFT, record["draft_id"])
            doc.update(values)
            doc.save(ignore_permissions=True)
            return
        frappe.get_doc(
            {
                "doctype": DRAFT,
                "draft_id": record["draft_id"],
                "registration_key": _registration(
                    record["tenant_id"], record["taxpayer_id"], record["idempotency_key"]
                ),
                **values,
            }
        ).insert(ignore_permissions=True)


def _registration(tenant_id: str, taxpayer_id: str, key: str) -> str:
    return ledger.digest(["assistance.draft", tenant_id, taxpayer_id, key])


# --- TAX-045 and the ledger -----------------------------------------------------------


def respond(tenant_id: str, taxpayer_id: str, request_id: str, body: dict[str, Any]) -> str:
    request = getattr(frappe.local, "request", None)
    correlation = request.headers.get("X-Vati-Correlation-ID") if request is not None else None
    try:
        response_store.respond([(tenant_id, taxpayer_id)], request_id, body, correlation)
    except response_rules.ResponseInputError as exc:
        raise ResponseRejected("invalid_domain_input", str(exc),
                               [e.as_dict() for e in exc.errors]) from None  # fmt: skip
    except MappingError as exc:
        raise ResponseRejected("invalid_domain_input" if exc.http_status == 422 else str(exc.code),
                               str(exc), exc.field_errors) from None  # fmt: skip
    return str(frappe.db.get_value(RESPONSE, {"request_id": request_id}, "name"))


def record_ledger(action: str, outcome: str, **ids: Any) -> None:
    tenant_id = ids.get("tenant_id")
    if not tenant_id:
        return
    ctx = audit_store.context(tenant_id)
    denied = outcome in ("denied", "refused")
    ledger.append(
        audit_store.FrappeLedger(),
        ledger.Unsealed(
            event_id=ledger.new_event_id(),
            tenant_id=tenant_id,
            action=action,
            outcome=outcome,
            actor=ctx.scope.user,
            actor_role=audit_service.acting_role(ctx.scope, policy.ASSISTANCE_USE),
            occurred_at=ctx.now,
            correlation_id=ctx.correlation_id,
            subject_type="assistance",
            subject_id=str(ids.get("subject_id") or ids.get("taxpayer_id") or tenant_id),
            payload_hash=ledger.digest({"action": action, "outcome": outcome,
                                        "reason": ids.get("reason")}),  # fmt: skip
            taxpayer_id=ids.get("taxpayer_id"),
            reason=ids.get("reason"),
            category="denied" if outcome == "denied" else
            ("read" if outcome == "answered" else "write"),
        ),
    )  # fmt: skip
    if denied:
        frappe.db.commit()  # kept although the request's other writes are rolled back


def _report(kind: str) -> None:
    frappe.logger("vati_tax.assistance").error({"event": "assistance_answer_refused",
                                                "kind": kind})  # fmt: skip


def service() -> AssistanceService:
    return AssistanceService(
        Ports(
            caller=caller,
            records=records,
            runtime=runtime.load,
            drafts=FrappeDrafts(),
            respond=respond,
            ledger=record_ledger,
            clock=lambda: datetime.datetime.now(datetime.UTC),
            report=_report,
        )
    )
