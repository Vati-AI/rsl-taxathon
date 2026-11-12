"""Refund-confidentiality access in Frappe (TAX-142, ADR-049).

A refund case (a case whose submission is a refund claim) is visible only to the refund
roles (``identity.policy.may_read_refund_case``): service identities, refund-team
supervisors, and the refund-team auditor or specialist the case is assigned to.

- ``require_case`` guards every case-scoped staff endpoint. A refusal is sealed into the
  audit ledger as ``<action>.denied`` with the code ``refund_confidentiality`` and
  committed, then raised as ``frappe.PermissionError`` (403).
- ``visible_cases`` filters case lists; hidden cases are simply not listed.
- ``identity.permissions`` applies the same rule to Desk reads (list queries and
  ``has_permission``) for every DocType that holds refund case data.
- ``assign_case`` lets a refund-team supervisor give a case to a refund-team officer.

Taxpayers are not affected (they only reach their own records) and site operators are
not narrowed, as for the tenant rules.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable
from typing import Any

import frappe

from vati_tax.audit import ledger
from vati_tax.audit import service as audit_service
from vati_tax.audit import store as audit_store
from vati_tax.identity import permissions, policy
from vati_tax.ids.canonical import CaseNotFound, InvalidMapping, canonical_uuid

CASE = "Vati Case Identity"
SUBMISSION = "Vati Submission"
ASSIGNMENT = "Vati Case Assignment"
MEMBERSHIP = "Vati Tenant Membership"
REASON_MIN = 10
REASON_MAX = 500


def _cache() -> dict[str, Any]:
    if not hasattr(frappe.local, "vati_refund_cases"):
        frappe.local.vati_refund_cases = {}
    cache: dict[str, Any] = frappe.local.vati_refund_cases
    return cache


def clear_cache(*_args: Any, **_kwargs: Any) -> None:
    _cache().clear()


def case_info(case_id: str | None) -> tuple[str | None, bool, str | None]:
    """``(tenant_id, is_refund_case, assignee)`` for a case; unknown case: ``(None, False, None)``."""
    if not case_id:
        return None, False, None
    cache = _cache()
    if case_id in cache:
        value: tuple[str | None, bool, str | None] = cache[case_id]
        return value
    row = frappe.db.get_value(CASE, case_id, ["tenant_id", "submission_id"], as_dict=True)
    if not row:
        result: tuple[str | None, bool, str | None] = (None, False, None)
    else:
        kind = (
            frappe.db.get_value(SUBMISSION, row.submission_id, "submission_type")
            if row.submission_id
            else None
        )
        assignee = frappe.db.get_value(ASSIGNMENT, case_id, "assignee")
        result = (row.tenant_id, kind == policy.REFUND_CLAIM, assignee or None)
    cache[case_id] = result
    return result


def submission_info(submission_id: str | None) -> tuple[str | None, bool, str | None]:
    """``(tenant_id, is_refund_claim, assignee)`` for a submission and its case, if any."""
    if not submission_id:
        return None, False, None
    row = frappe.db.get_value(
        SUBMISSION, submission_id, ["tenant_id", "submission_type", "case_id"], as_dict=True
    )
    if not row:
        return None, False, None
    assignee = frappe.db.get_value(ASSIGNMENT, row.case_id, "assignee") if row.case_id else None
    return row.tenant_id, row.submission_type == policy.REFUND_CLAIM, assignee or None


def _scope(tenant_id: str | None) -> policy.TenantScope | None:
    """The caller's scope, or None for an operator (not narrowed)."""
    if permissions.is_operator(frappe.session.user):
        return None
    return permissions.resolve_scope(tenant_id)


def _is_taxpayer_only(scope: policy.TenantScope) -> bool:
    return scope.roles <= {policy.TAXPAYER}


def record_denied(
    scope: policy.TenantScope, action: str, case_id: str, *, commit: bool = True
) -> None:
    """Seal a refused attempt into the tenant's audit ledger (and keep it)."""
    ctx = audit_service.Context.build(scope, None, datetime.datetime.now(datetime.UTC))
    audit_service.record_denied(
        ctx,
        audit_store.FrappeLedger(),
        attempted_action=action,
        subject_type="case",
        subject_id=case_id,
        code=policy.REFUND_CONFIDENTIALITY,
        case_id=case_id,
    )
    if commit:
        frappe.db.commit()  # nosemgrep: the refusal must outlive the failing request
    frappe.logger("vati_tax.refund_access").info(
        {"action": action, "outcome": "denied", "case_id": case_id,
         "code": policy.REFUND_CONFIDENTIALITY}
    )  # fmt: skip


def _deny(scope: policy.TenantScope, action: str, case_id: str) -> None:
    record_denied(scope, action, case_id)
    raise frappe.PermissionError(
        "This refund case is visible only to its assigned refund officer and the refund "
        "team's supervisors."
    )


def check_case(scope: policy.TenantScope, case_id: str, action: str) -> None:
    """Refuse (and record) ``action`` on a refund case outside the scope's refund access."""
    if _is_taxpayer_only(scope):
        return
    tenant, refund, assignee = case_info(case_id)
    if tenant != scope.tenant_id or not refund:
        return  # another tenant's or unknown case: the caller's own check reports not found
    if not policy.may_read_refund_case(scope, assignee):
        _deny(scope, action, case_id)


def require_case(case_id: str | None, action: str, tenant_id: str | None = None) -> None:
    """Guard a case-scoped endpoint (``tenant_id`` defaults to the request header)."""
    if not case_id:
        return
    scope = _scope(tenant_id)
    if scope is not None:
        check_case(scope, case_id, action)


def require_record(
    doctype: str, name: str | None, action: str, tenant_id: str | None = None
) -> None:
    """Guard an endpoint keyed by a record that belongs to a case (decision, evidence, ...)."""
    if not name:
        return
    case_id = frappe.db.get_value(doctype, name, "case_id")
    require_case(case_id, action, tenant_id)


def require_submission(
    submission_id: str | None, action: str, tenant_id: str | None = None
) -> None:
    """Guard an endpoint keyed by a submission (a refund claim before or after its case)."""
    scope = _scope(tenant_id)
    if scope is None or _is_taxpayer_only(scope) or not submission_id:
        return
    tenant, refund, assignee = submission_info(submission_id)
    if tenant == scope.tenant_id and refund and not policy.may_read_refund_case(scope, assignee):
        case_id = frappe.db.get_value(SUBMISSION, submission_id, "case_id") or submission_id
        _deny(scope, action, case_id)


def visible_cases(scope: policy.TenantScope, case_ids: Iterable[str]) -> set[str]:
    """The case IDs among ``case_ids`` the scope may see (lists omit the others)."""
    ids = list(case_ids)
    if _is_taxpayer_only(scope) or not ids:
        return set(ids)
    return {
        case_id
        for case_id in ids
        if not case_info(case_id)[1] or policy.may_read_refund_case(scope, case_info(case_id)[2])
    }


# --- assignment ---------------------------------------------------------------------


def _view(doc: Any) -> dict[str, Any]:
    return {
        "case_id": doc.case_id,
        "assignee": doc.assignee,
        "assignee_role": doc.assignee_role,
        "assigned_by": doc.assigned_by,
        "assigned_at": ledger.iso(frappe.utils.get_datetime(doc.assigned_at)),
        "reason": doc.reason,
        "version": int(doc.version),
    }


def assign_case(
    *,
    tenant_id: str,
    case_id: str,
    assignee: str,
    reason: str,
    expected_version: int | None = None,
) -> dict[str, Any]:
    """A refund-team supervisor gives a refund case to a refund-team officer.

    Refused (and recorded) for anyone else. ``expected_version`` guards against two
    supervisors reassigning at once (409 on a stale value).
    """
    scope = permissions.require_action(policy.CASE_ASSIGN, tenant_id)
    case = canonical_uuid(case_id, "case_id")
    tenant, refund, current = case_info(case)
    if tenant != scope.tenant_id:
        raise CaseNotFound("Case not found.", "case_id", "not_found")
    if not refund:
        raise InvalidMapping("Only refund cases are assigned here.", "case_id", "not_a_refund_case")
    if not policy.may_read_refund_case(scope, current):
        _deny(scope, policy.CASE_ASSIGN, case)
    text = " ".join(str(reason or "").split())
    if not REASON_MIN <= len(text) <= REASON_MAX:
        raise InvalidMapping("A reason of 10 to 500 characters is required.", "reason", "invalid")
    row = frappe.db.get_value(
        MEMBERSHIP,
        {"user": assignee, "tenant_id": scope.tenant_id, "enabled": 1},
        ["vati_role", "refund_team"],
        as_dict=True,
    )
    membership = (
        policy.Membership(scope.tenant_id, row.vati_role, None, bool(row.refund_team))
        if row and row.vati_role in policy.STAFF_ROLES
        else None
    )
    if not policy.may_be_assigned_refund_case(membership):
        raise InvalidMapping(
            "The officer is not on this tenant's refund team.", "assignee", "not_refund_team"
        )
    assert membership is not None
    now = datetime.datetime.now(datetime.UTC)
    if frappe.db.exists(ASSIGNMENT, case):
        doc = frappe.get_doc(ASSIGNMENT, case)
        if expected_version is not None and int(expected_version) != int(doc.version):
            raise InvalidMapping("The assignment changed; reload it.", "expected_version",
                                 "version_conflict")  # fmt: skip
        previous = doc.assignee
        doc.update({"assignee": assignee, "assignee_role": membership.role,
                    "assigned_by": scope.user, "assigned_at": now.replace(tzinfo=None),
                    "reason": text, "version": int(doc.version) + 1})  # fmt: skip
        doc.save(ignore_permissions=True)
    else:
        previous = None
        doc = frappe.get_doc({
            "doctype": ASSIGNMENT, "case_id": case, "tenant_id": scope.tenant_id,
            "assignee": assignee, "assignee_role": membership.role, "assigned_by": scope.user,
            "assigned_at": now.replace(tzinfo=None), "reason": text, "version": 1,
        }).insert(ignore_permissions=True)  # fmt: skip
    ctx = audit_service.Context.build(scope, None, now)
    ledger.append(
        audit_store.FrappeLedger(),
        ledger.Unsealed(
            event_id=ledger.new_event_id(),
            tenant_id=scope.tenant_id,
            action="case.assigned",
            outcome="succeeded",
            actor=scope.user,
            actor_role=policy.SUPERVISOR,
            occurred_at=now,
            correlation_id=ctx.correlation_id,
            subject_type="case",
            subject_id=case,
            payload_hash=ledger.digest(
                {"assignee": assignee, "previous": previous, "version": int(doc.version)}
            ),  # fmt: skip
            case_id=case,
            reason=text,
            target_actor=assignee,
            category="write",
        ),
    )
    clear_cache()
    return _view(doc)


def case_assignment(*, tenant_id: str, case_id: str) -> dict[str, Any] | None:
    """The case's current assignment, for staff who may see the case."""
    scope = permissions.require_action(policy.CASE_READ, tenant_id)
    case = canonical_uuid(case_id, "case_id")
    if frappe.db.get_value(CASE, case, "tenant_id") != scope.tenant_id:
        raise CaseNotFound("Case not found.", "case_id", "not_found")
    check_case(scope, case, policy.CASE_READ)
    if not frappe.db.exists(ASSIGNMENT, case):
        return None
    return _view(frappe.get_doc(ASSIGNMENT, case))
