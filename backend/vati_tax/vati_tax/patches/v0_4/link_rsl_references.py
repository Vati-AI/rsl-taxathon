"""Record RSL references stored before TAX-133 as canonical service links.

Before TAX-133 the RSL audit case reference lived only on ``Vati RSL Referral`` and the
Online Refunds claim ID only on the ``Vati Submission`` it opened (``source_ref``). This
records each as an ``audit_system`` / ``online_refunds`` link on its case, through the
same ``ids.mapping.record_link`` the intake now calls, so replays are no-ops and the
register's conflict rules apply. A reference that conflicts (already mapped to another
case, or the case already has one of that type) is left unlinked and counted; nothing
existing is changed. Logs carry counts only.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

import frappe

from vati_tax.ids import mapping
from vati_tax.ids.canonical import MappingError

REFERRAL = "Vati RSL Referral"
SUBMISSION = "Vati Submission"
ONLINE_REFUNDS = "rsl_online_refunds"


def _candidates() -> Iterable[tuple[str, str, str, str, str]]:
    for row in frappe.get_all(
        REFERRAL,
        filters={"case_id": ["is", "set"], "audit_case_ref": ["is", "set"]},
        fields=["tenant_id", "case_id", "audit_case_ref"],
        order_by="creation asc",
    ):
        yield row.tenant_id, row.case_id, "audit_system", "audit_case", row.audit_case_ref
    for row in frappe.get_all(
        SUBMISSION,
        filters={"source": ONLINE_REFUNDS, "case_id": ["is", "set"], "source_ref": ["is", "set"]},
        fields=["tenant_id", "case_id", "source_ref"],
        order_by="creation asc",
    ):
        yield row.tenant_id, row.case_id, "online_refunds", "claim", row.source_ref


def link_all() -> dict[str, int]:
    counts: Counter[str] = Counter()
    for tenant_id, case_id, service, external_type, external_id in _candidates():
        try:
            result: dict[str, Any] = mapping.record_link(
                tenant_id=tenant_id,
                case_id=case_id,
                service=service,
                external_type=external_type,
                external_id=external_id,
            )
        except MappingError as exc:
            reason = exc.field_errors[0]["code"] if exc.field_errors else exc.code
            counts[f"{service}.{reason}"] += 1
            continue
        counts[f"{service}.{'replayed' if result['replayed'] else 'linked'}"] += 1
    return dict(sorted(counts.items()))


def execute() -> None:
    counts = link_all()
    frappe.logger("vati_tax.patches").info(
        {"patch": "link_rsl_references", "outcome": "done", **counts}
    )
