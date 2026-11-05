"""Frappe binding for objections and appeals (TAX-069).

``FrappeChallenges`` stores ``Vati Decision Challenge`` rows. Decisions and
evidence ownership are read through the TAX-046 ``FrappeRepository`` and are
never written here. Refusals are written to the audit ledger by ``service``
before it raises; ``_call`` commits them (and GET reads) before Frappe would
roll the request back. On every refusal path the only rows written are those
ledger entries.
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
from vati_tax.challenges import service
from vati_tax.challenges.rules import ChallengeRecord
from vati_tax.decisions.store import FrappeRepository
from vati_tax.identity import policy
from vati_tax.ids.canonical import MappingError

CHALLENGE = "Vati Decision Challenge"
_UNIQUE = (frappe.UniqueValidationError, frappe.DuplicateEntryError)
_FIELDS = [
    "challenge_id",
    "tenant_id",
    "taxpayer_id",
    "case_id",
    "decision_id",
    "stage",
    "parent_challenge_id",
    "status",
    "version",
    "grounds",
    "evidence_ids",
    "route_reference",
    "lodged_by",
    "lodger_role",
    "lodged_at",
    "decision_digest",
    "request_digest",
    "outcome",
    "reviewed_by",
    "reviewed_at",
    "review_reason",
]
T = TypeVar("T")


def _naive(value: datetime.datetime | None) -> datetime.datetime | None:
    return value.astimezone(datetime.UTC).replace(tzinfo=None) if value else None


def _aware(value: Any) -> datetime.datetime | None:
    if not value:
        return None
    return frappe.utils.get_datetime(value).replace(tzinfo=datetime.UTC)


def _record(row: Any) -> ChallengeRecord:
    lodged_at = _aware(row.lodged_at)
    if lodged_at is None:
        raise ValueError("lodged_at is required")
    return ChallengeRecord(
        challenge_id=row.challenge_id,
        tenant_id=row.tenant_id,
        taxpayer_id=row.taxpayer_id,
        case_id=row.case_id,
        decision_id=row.decision_id,
        stage=row.stage,
        parent_challenge_id=row.parent_challenge_id or None,
        grounds=row.grounds,
        evidence_ids=tuple(json.loads(row.evidence_ids or "[]")),
        route_reference=row.route_reference or None,
        decision_digest=row.decision_digest,
        request_digest=row.request_digest,
        lodged_by=row.lodged_by,
        lodger_role=row.lodger_role,
        lodged_at=lodged_at,
        status=row.status,
        version=int(row.version),
        reviewed_by=row.reviewed_by or None,
        reviewed_at=_aware(row.reviewed_at),
        outcome=row.outcome or None,
        review_reason=row.review_reason or None,
    )


class FrappeChallenges:
    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        savepoint = "vati_challenge_" + uuid.uuid4().hex[:12]
        frappe.db.savepoint(savepoint)
        try:
            yield
        except BaseException:
            frappe.db.rollback(save_point=savepoint)
            raise

    def get(self, tenant_id: str, challenge_id: str) -> ChallengeRecord | None:
        row = frappe.db.get_value(CHALLENGE, challenge_id, _FIELDS, as_dict=True)
        if not row or row.tenant_id != tenant_id:
            return None
        return _record(row)

    def for_decision(self, tenant_id: str, decision_id: str) -> list[ChallengeRecord]:
        rows = frappe.get_all(
            CHALLENGE,
            filters={"tenant_id": tenant_id, "decision_id": decision_id},
            fields=_FIELDS,
            order_by="lodged_at asc, creation asc",
        )
        return [_record(row) for row in rows]

    def insert(self, record: ChallengeRecord) -> None:
        doc = frappe.get_doc(
            {
                "doctype": CHALLENGE,
                "challenge_id": record.challenge_id,
                "tenant_id": record.tenant_id,
                "taxpayer_id": record.taxpayer_id,
                "case_id": record.case_id,
                "decision_id": record.decision_id,
                "stage": record.stage,
                "parent_challenge_id": record.parent_challenge_id,
                "status": record.status,
                "version": record.version,
                "grounds": record.grounds,
                "evidence_ids": json.dumps(list(record.evidence_ids)),
                "route_reference": record.route_reference,
                "lodged_by": record.lodged_by,
                "lodger_role": record.lodger_role,
                "lodged_at": _naive(record.lodged_at),
                "decision_digest": record.decision_digest,
                "request_digest": record.request_digest,
            }
        )
        try:
            doc.insert(ignore_permissions=True)
        except _UNIQUE as exc:
            raise ledger.Duplicate(str(exc)) from exc

    def update(self, record: ChallengeRecord, expected_version: int) -> bool:
        """Compare-and-set on ``version``: the row is locked, then checked."""
        rows = frappe.db.sql(
            f"select `version` from `tab{CHALLENGE}` where `name` = %s for update",  # noqa: S608
            (record.challenge_id,),
        )
        if not rows or int(rows[0][0]) != expected_version:
            return False
        doc = frappe.get_doc(CHALLENGE, record.challenge_id)
        doc.update(
            {
                "status": record.status,
                "version": record.version,
                "outcome": record.outcome,
                "reviewed_by": record.reviewed_by,
                "reviewed_at": _naive(record.reviewed_at),
                "review_reason": record.review_reason,
            }
        )
        doc.save(ignore_permissions=True)
        return True


def ports() -> service.Ports:
    return service.Ports(
        challenges=FrappeChallenges(), decisions=FrappeRepository(), ledger=FrappeLedger()
    )


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
    frappe.db.commit()  # nosemgrep: GET reads are not committed by Frappe
    return result


def lodge(
    *, tenant_id: str, body: dict[str, Any], idempotency_key: str, correlation_id: str | None
) -> dict[str, Any]:
    ctx = audit_store.context(tenant_id, correlation_id)
    return _call(lambda: service.lodge(ctx, ports(), body=body, idempotency_key=idempotency_key))


def review(
    *,
    tenant_id: str,
    challenge_id: str,
    expected_version: object,
    outcome: str,
    reason: str,
    correlation_id: str | None,
) -> dict[str, Any]:
    ctx = audit_store.context(tenant_id, correlation_id)
    return _call(
        lambda: service.review(
            ctx,
            ports(),
            challenge_id=challenge_id,
            expected_version=expected_version,
            outcome=outcome,
            reason=reason,
        )
    )


def history(*, tenant_id: str, decision_id: str) -> dict[str, Any]:
    ctx = audit_store.context(tenant_id)
    return _call(lambda: service.history(ctx, ports(), decision_id=decision_id))
