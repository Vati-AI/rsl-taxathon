"""Frappe binding for learned-workflow approval and rollback (TAX-065).

``FrappeWorkflows`` implements ``service.WorkflowStore`` on ``Vati Workflow
Version`` and ``Vati Workflow Activation``. Refusals are written to the audit
ledger by ``service`` before it raises; ``_call`` commits so they survive the
failing request (on every refusal path only ledger rows were written), and
commits GET reads/checks too, because Frappe does not commit GET requests.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import uuid
from collections.abc import Callable, Iterator
from typing import Any, TypeVar

import frappe

from vati_tax.audit import ledger
from vati_tax.audit import store as audit_store
from vati_tax.audit.store import FrappeLedger
from vati_tax.identity import policy
from vati_tax.ids.canonical import MappingError
from vati_tax.workflows import rules, service
from vati_tax.workflows.rules import Activation, WorkflowVersion

VERSION = "Vati Workflow Version"
ACTIVATION = "Vati Workflow Activation"
_UNIQUE = (frappe.UniqueValidationError, frappe.DuplicateEntryError)
_VERSION_FIELDS = [
    "name",
    "tenant_id",
    "workflow",
    "version",
    "status",
    "record_version",
    "steps",
    "proposal_id",
    "observer_version",
    "proposal_digest",
    "holdout_evaluation",
    "request_digest",
    "proposed_by",
    "proposed_at",
    "decided_by",
    "decided_at",
    "decision_reason",
]
_ACTIVATION_FIELDS = [
    "tenant_id",
    "workflow",
    "seq",
    "kind",
    "version",
    "replaced_version",
    "rollback_to",
    "activated_by",
    "activated_at",
    "reason",
]
T = TypeVar("T")


def _naive(value: datetime.datetime | None) -> datetime.datetime | None:
    return value.astimezone(datetime.UTC).replace(tzinfo=None) if value else None


def _aware(value: Any) -> datetime.datetime | None:
    if not value:
        return None
    return frappe.utils.get_datetime(value).replace(tzinfo=datetime.UTC)


def _version(row: Any) -> WorkflowVersion:
    proposed_at = _aware(row.proposed_at)
    if proposed_at is None:
        raise ValueError("proposed_at is required")
    return WorkflowVersion(
        tenant_id=row.tenant_id,
        workflow=row.workflow,
        version=row.version,
        steps=tuple(json.loads(row.steps or "[]")),
        proposal_id=row.proposal_id,
        observer_version=row.observer_version,
        proposal_digest=row.proposal_digest,
        holdout=json.loads(row.holdout_evaluation or "{}"),
        request_digest=row.request_digest,
        proposed_by=row.proposed_by,
        proposed_at=proposed_at,
        status=row.status,
        record_version=int(row.record_version),
        decided_by=row.decided_by or None,
        decided_at=_aware(row.decided_at),
        decision_reason=row.decision_reason or None,
    )


def _activation(row: Any) -> Activation:
    activated_at = _aware(row.activated_at)
    if activated_at is None:
        raise ValueError("activated_at is required")
    return Activation(
        tenant_id=row.tenant_id,
        workflow=row.workflow,
        seq=int(row.seq),
        kind=row.kind,
        version=row.version,
        replaced_version=row.replaced_version or None,
        rollback_to=row.rollback_to or None,
        activated_by=row.activated_by,
        activated_at=activated_at,
        reason=row.reason,
    )


class FrappeWorkflows:
    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        savepoint = "vati_workflow_" + uuid.uuid4().hex[:12]
        frappe.db.savepoint(savepoint)
        try:
            yield
        except BaseException:
            frappe.db.rollback(save_point=savepoint)
            raise

    def _row(self, tenant_id: str, workflow: str, version: str) -> Any:
        rows = frappe.get_all(
            VERSION,
            filters={"version_key": rules.version_key(tenant_id, workflow, version)},
            fields=_VERSION_FIELDS,
            limit=1,
        )
        return rows[0] if rows and rows[0].tenant_id == tenant_id else None

    def get_version(self, tenant_id: str, workflow: str, version: str) -> WorkflowVersion | None:
        row = self._row(tenant_id, workflow, version)
        return _version(row) if row else None

    def versions(self, tenant_id: str, workflow: str) -> dict[str, WorkflowVersion]:
        rows = frappe.get_all(
            VERSION, filters={"tenant_id": tenant_id, "workflow": workflow}, fields=_VERSION_FIELDS
        )
        return {row.version: _version(row) for row in rows}

    def insert_version(self, record: WorkflowVersion) -> None:
        doc = frappe.get_doc(
            {
                "doctype": VERSION,
                "tenant_id": record.tenant_id,
                "workflow": record.workflow,
                "version": record.version,
                "status": record.status,
                "record_version": record.record_version,
                "steps": json.dumps(list(record.steps)),
                "proposal_id": record.proposal_id,
                "observer_version": record.observer_version,
                "proposal_digest": record.proposal_digest,
                "holdout_evaluation": json.dumps(record.holdout, sort_keys=True),
                "request_digest": record.request_digest,
                "proposed_by": record.proposed_by,
                "proposed_at": _naive(record.proposed_at),
            }
        )
        try:
            doc.insert(ignore_permissions=True)
        except _UNIQUE as exc:
            raise ledger.Duplicate(str(exc)) from exc

    def update_version(self, record: WorkflowVersion, expected: int) -> bool:
        row = self._row(record.tenant_id, record.workflow, record.version)
        if row is None:
            return False
        locked = frappe.db.sql(
            f"select `record_version` from `tab{VERSION}` where `name` = %s for update",  # noqa: S608
            (row.name,),
        )
        if not locked or int(locked[0][0]) != expected:
            return False
        doc = frappe.get_doc(VERSION, row.name)
        doc.update(
            {
                "status": record.status,
                "record_version": record.record_version,
                "decided_by": record.decided_by,
                "decided_at": _naive(record.decided_at),
                "decision_reason": record.decision_reason,
            }
        )
        doc.save(ignore_permissions=True)
        return True

    def history(self, tenant_id: str, workflow: str) -> list[Activation]:
        rows = frappe.get_all(
            ACTIVATION,
            filters={"tenant_id": tenant_id, "workflow": workflow},
            fields=_ACTIVATION_FIELDS,
            order_by="seq asc",
        )
        return [_activation(row) for row in rows]

    def append_activation(self, entry: Activation) -> None:
        doc = frappe.get_doc(
            {
                "doctype": ACTIVATION,
                "tenant_id": entry.tenant_id,
                "workflow": entry.workflow,
                "seq": entry.seq,
                "kind": entry.kind,
                "version": entry.version,
                "replaced_version": entry.replaced_version,
                "rollback_to": entry.rollback_to,
                "activated_by": entry.activated_by,
                "activated_at": _naive(entry.activated_at),
                "reason": entry.reason,
            }
        )
        try:
            doc.insert(ignore_permissions=True)
        except _UNIQUE as exc:
            raise ledger.Duplicate(str(exc)) from exc


def ports() -> service.Ports:
    return service.Ports(workflows=FrappeWorkflows(), ledger=FrappeLedger())


def _call(fn: Callable[[], T]) -> T:
    """Run a service step; keep recorded refusals and reads, map policy refusals."""
    try:
        result = fn()
    except policy.ActionDenied as exc:
        frappe.db.commit()  # nosemgrep: keep the recorded refusal
        raise frappe.PermissionError(str(exc)) from exc
    except MappingError:
        frappe.db.commit()  # nosemgrep: keep the recorded refusal (only ledger rows)
        raise
    frappe.db.commit()  # nosemgrep: GET checks are not committed by Frappe
    return result


def _ctx(tenant_id: str, correlation_id: str | None = None) -> Any:
    return audit_store.context(tenant_id, correlation_id)


# --- service functions --------------------------------------------------------------


def propose_workflow(
    *, tenant_id: str, body: dict[str, Any], idempotency_key: str, correlation_id: str | None
) -> dict[str, Any]:
    ctx = _ctx(tenant_id, correlation_id)
    return _call(
        lambda: service.propose_workflow(ctx, ports(), body=body, idempotency_key=idempotency_key)
    )


def decide_workflow(
    *,
    tenant_id: str,
    workflow: str,
    version: str,
    expected_version: object,
    approve: bool,
    reason: str | None,
    holdout_reviewed: bool,
    correlation_id: str | None,
) -> dict[str, Any]:
    ctx = _ctx(tenant_id, correlation_id)
    return _call(
        lambda: service.decide_workflow(
            ctx,
            ports(),
            workflow=workflow,
            version=version,
            expected_version=expected_version,
            approve=approve,
            reason=reason,
            holdout_reviewed=holdout_reviewed,
        )
    )


def activate_workflow(
    *,
    tenant_id: str,
    workflow: str,
    version: str,
    expected_seq: object,
    reason: str | None,
    correlation_id: str | None,
) -> dict[str, Any]:
    ctx = _ctx(tenant_id, correlation_id)
    return _call(
        lambda: service.activate_workflow(
            ctx,
            ports(),
            workflow=workflow,
            version=version,
            expected_seq=expected_seq,
            reason=reason,
        )
    )


def rollback_workflow(
    *,
    tenant_id: str,
    workflow: str,
    expected_seq: object,
    reason: str | None,
    correlation_id: str | None,
) -> dict[str, Any]:
    ctx = _ctx(tenant_id, correlation_id)
    return _call(
        lambda: service.rollback_workflow(
            ctx, ports(), workflow=workflow, expected_seq=expected_seq, reason=reason
        )
    )


def get_workflow(*, tenant_id: str, workflow: str) -> dict[str, Any]:
    ctx = _ctx(tenant_id)
    return _call(lambda: service.get_workflow(ctx, ports(), workflow=workflow))


def check_execution(*, tenant_id: str, workflow: str, version: str) -> dict[str, Any]:
    ctx = _ctx(tenant_id)
    return _call(lambda: service.check_execution(ctx, ports(), workflow=workflow, version=version))
