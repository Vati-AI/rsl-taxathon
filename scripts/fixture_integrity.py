"""Fixture data for getIntegrityReview (TAX-077, ADR-036).

Reads integration/api/fixtures/integrity.json, seals its ledger events into one
tenant chain with the real TAX-059 code, and builds the response with the
Frappe-free ``vati_tax.integrity.aggregate`` rules, so the fixture follows the
same privacy rules the Vati service will. Supervisors only; everyone else is
refused with ``forbidden`` and no data.
"""

from __future__ import annotations

import datetime
import importlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INTEGRITY_PATH = ROOT / "integration" / "api" / "fixtures" / "integrity.json"
BACKEND_DIR = ROOT / "backend" / "vati_tax"
_EVENT_NS = uuid.UUID("5a7e0000-0000-4000-8000-000000000077")
FORBIDDEN_MESSAGE = "Only supervisors can view the integrity review."


def _backend(name: str) -> Any:
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    return importlib.import_module(name)


def _ts(value: str | None) -> datetime.datetime | None:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def load_integrity(path: Path = INTEGRITY_PATH) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if data.get("synthetic") is not True:
        raise ValueError("integrity fixture data must be marked synthetic")
    return data


def ledger_entries(tenant_id: str, events: list[dict[str, Any]]) -> list[Any]:
    """Seal the fixture events with the TAX-059 ledger code (outcomes kept as given)."""
    ledger = _backend("vati_tax.audit.ledger")
    store = ledger.MemoryLedger()
    for index, event in enumerate(events):
        key = f"{index}|{event['action']}|{event['occurred_at']}"
        outcome = event.get("outcome") or (
            "denied" if event["action"].endswith(".denied") else "ok"
        )
        ledger.append(
            store,
            ledger.Unsealed(
                event_id=str(uuid.uuid5(_EVENT_NS, key)),
                tenant_id=tenant_id,
                action=event["action"],
                outcome=outcome,
                actor=event["actor"],
                actor_role=event["actor_role"],
                occurred_at=_ts(event["occurred_at"]),
                correlation_id=str(uuid.uuid5(_EVENT_NS, "correlation|" + key)),
                subject_type=event["subject_type"],
                subject_id=event.get("case_id") or event["subject_type"],
                payload_hash=ledger.digest(event),
                case_id=event.get("case_id"),
                target_actor=event.get("target_actor"),
                reason=event.get("reason"),
            ),
        )
    return list(store.entries(tenant_id))


def integrity_review(
    tenant_id: str,
    fixture: dict[str, Any],
    window_days: int,
    *,
    viewer: dict[str, Any],
    entries: list[Any] | None = None,
    ledger_available: bool = True,
) -> dict[str, Any] | str:
    """The getIntegrityReview body for the acting fixture principal, or an error code.

    ``entries`` replaces the sealed fixture ledger (tests use it to tamper with the
    chain); ``ledger_available=False`` simulates an unreadable ledger.
    """
    if "supervisor" not in viewer["roles"]:
        return "forbidden"
    logins = {o["officer_id"]: o["login"] for o in fixture["officers"]}
    login = logins.get(viewer["officer_id"])
    if login is None:
        # Without a ledger identity the viewer's own signals could not be withheld.
        return "forbidden"
    now = _ts(fixture["as_of"])
    if now is None:
        raise ValueError("integrity fixture needs as_of")
    if entries is None and ledger_available:
        entries = ledger_entries(tenant_id, fixture["ledger_events"])
    gaps = [{**gap, "reviewed_at": _ts(gap.get("reviewed_at"))} for gap in fixture["policy_gaps"]]
    aggregate = _backend("vati_tax.integrity.aggregate")
    result: dict[str, Any] = aggregate.integrity_review(
        tenant_id=tenant_id,
        gaps=gaps,
        gap_labels=fixture["policy_areas"],
        entries=entries if ledger_available else None,
        viewer=login,
        now=now,
        window_days=window_days,
        min_group_size=int(fixture["min_group_size"]),
    )
    return result
