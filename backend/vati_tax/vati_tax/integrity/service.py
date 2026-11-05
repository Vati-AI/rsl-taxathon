"""The restricted integrity review with policy checks (TAX-077, ADR-036), Frappe-free.

``api.py`` binds it to Frappe and the ``Vati Audit Event`` ledger; tests bind it
to ``ledger.MemoryLedger``. The rules:

- Only supervisors of the selected tenant may read it (the TAX-029 ``ledger.read``
  action). Anyone else is refused, the refusal is recorded in the ledger before it
  is raised, and no aggregate is computed.
- A successful read is itself recorded as a ``ledger.read`` entry.
- The body is built by ``aggregate.integrity_review``: aggregates only, small cells
  combined, no person identified, and review signals about the viewer withheld.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from vati_tax.audit import service as audit
from vati_tax.audit.ledger import LedgerStore
from vati_tax.identity import policy
from vati_tax.ids.canonical import InvalidMapping
from vati_tax.integrity import aggregate

# Reads are recorded as ledger reads (an allowed subject type); detail.view says which.
SUBJECT_TYPE = "ledger"


def integrity_review(
    ctx: audit.Context,
    store: LedgerStore,
    *,
    gaps: Iterable[Mapping[str, Any]] | None,
    gap_labels: Mapping[str, str],
    window_days: int,
    min_group_size: int = aggregate.MIN_GROUP_SIZE,
    ledger_available: bool = True,
) -> dict[str, Any]:
    """The review body for a supervisor; ``policy.ActionDenied`` for anyone else."""
    tenant = ctx.scope.tenant_id
    audit.guarded(
        ctx,
        store,
        attempted_action=policy.LEDGER_READ,
        subject_type=SUBJECT_TYPE,
        subject_id=tenant,
        fn=lambda: policy.authorize_action(ctx.scope, policy.LEDGER_READ),
    )
    if not 1 <= window_days <= 365:
        raise InvalidMapping("window_days must be between 1 and 365.", "window_days", "invalid")
    entries = list(store.entries(tenant)) if ledger_available else None
    body = aggregate.integrity_review(
        tenant_id=tenant,
        gaps=gaps,
        gap_labels=gap_labels,
        entries=entries,
        viewer=ctx.scope.user,
        now=ctx.now,
        window_days=window_days,
        min_group_size=min_group_size,
    )
    audit.record_read(
        ctx,
        store,
        action="ledger.read",
        subject_type=SUBJECT_TYPE,
        subject_id=tenant,
        detail={"view": "integrity_review", "window_days": window_days},
    )
    return body
