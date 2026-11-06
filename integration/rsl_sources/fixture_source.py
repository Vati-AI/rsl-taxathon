"""Fixture adapter for RSL source systems (TAX-111, ADR-040).

``FixtureRslSources`` serves synthetic records through the TAX-111 contracts, the way a
WSO2-published read API would, without any network access. Every envelope says it is a
fixture. A read is scoped to one tenant and one taxpayer. A system configured as
unavailable returns ``status: unavailable`` with no records: the caller must treat the
evidence as missing, never substitute a value.

Vati keeps references (source system, record ID, contract version, retrieval time and a
digest of the record), never a second copy of the source store.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from integration.rsl_sources.contracts import (
    ASSUMED_SHAPE_NOTICE,
    SourceContractError,
    contract_for,
    validate_record,
)

FIXTURE_ADAPTER_ID = "vati.rsl_sources.fixture"
FIXTURE_ADAPTER_VERSION = "tax111-fixture-v1"
GATEWAY = "wso2-shaped-fixture-gateway"


def record_digest(record: Mapping[str, Any]) -> str:
    text = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


class SourceAccessError(PermissionError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class FixtureRslSources:
    """Serve synthetic RSL records for one tenant through the source contracts."""

    def __init__(
        self,
        tenant_id: str,
        records: Mapping[tuple[str, str], Iterable[Mapping[str, Any]]],
        *,
        retrieved_at: str,
        unavailable: Iterable[str] = (),
    ) -> None:
        self.tenant_id = tenant_id
        self.retrieved_at = retrieved_at
        self.unavailable = frozenset(unavailable)
        self._by_taxpayer: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
        self._index: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
        for (system, kind), items in records.items():
            contract = contract_for(system, kind)
            bucket: dict[str, list[dict[str, Any]]] = {}
            seen: set[str] = set()
            for raw in items:
                record = validate_record(contract, raw)
                ident = str(record[contract.id_field])
                if ident in seen:
                    raise SourceContractError("duplicate_id", contract.id_field, ident)
                seen.add(ident)
                bucket.setdefault(str(record[contract.taxpayer_field]), []).append(record)
            self._by_taxpayer[(system, kind)] = bucket

    def read(self, system: str, kind: str, *, tenant_id: str, taxpayer: str) -> dict[str, Any]:
        """Return a fixture envelope with the taxpayer's records of one kind."""
        if tenant_id != self.tenant_id:
            raise SourceAccessError("cross_tenant_read", "fixture serves another tenant")
        contract = contract_for(system, kind)
        base: dict[str, Any] = {
            "fixture": True,
            "adapter_id": FIXTURE_ADAPTER_ID,
            "adapter_version": FIXTURE_ADAPTER_VERSION,
            "gateway": GATEWAY,
            "source_system": system,
            "kind": kind,
            "contract_id": contract.contract_id,
            "contract_notice": ASSUMED_SHAPE_NOTICE,
            "tenant_id": tenant_id,
            "taxpayer": taxpayer,
            "retrieved_at": self.retrieved_at,
        }
        if system in self.unavailable:
            return {**base, "status": "unavailable", "records": [], "references": []}
        records = [dict(r) for r in self._by_taxpayer.get((system, kind), {}).get(taxpayer, [])]
        references = [
            {
                "source_system": system,
                "record_id": str(r[contract.id_field]),
                "contract_id": contract.contract_id,
                "retrieved_at": self.retrieved_at,
                "record_sha256": record_digest(r),
            }
            for r in records
        ]
        return {**base, "status": "ok", "records": records, "references": references}

    def lookup(
        self, system: str, kind: str, *, tenant_id: str, field: str, values: Iterable[str]
    ) -> dict[str, Any]:
        """Reference-data lookup (registry, Lekuka integration status) by one field.

        Returns matching records keyed by ``field`` value. An unavailable system returns
        ``status: unavailable`` and no matches.
        """
        if tenant_id != self.tenant_id:
            raise SourceAccessError("cross_tenant_read", "fixture serves another tenant")
        contract = contract_for(system, kind)
        if system in self.unavailable:
            return {
                "fixture": True,
                "status": "unavailable",
                "contract_id": contract.contract_id,
                "matches": {},
            }
        index = self._index.get((system, kind, field))
        if index is None:
            index = {}
            for bucket in self._by_taxpayer.get((system, kind), {}).values():
                for record in bucket:
                    if record.get(field) is not None:
                        index[str(record[field])] = record
            self._index[(system, kind, field)] = index
        wanted = set(values)
        return {
            "fixture": True,
            "status": "ok",
            "contract_id": contract.contract_id,
            "matches": {v: dict(index[v]) for v in wanted if v in index},
        }


def asycuda_entries_from_tax073(cases: Iterable[Mapping[str, Any]], importer_tin: str):
    """Map TAX-073 ``customs_value`` bases onto the ASYCUDA entry contract.

    Only values recorded from a customs declaration are mapped. Import VAT is not part of
    the TAX-073 fixture, so it is reported as ``0.00`` and flagged ``import_vat_known``
    false by the caller; nothing is computed or guessed here.
    """
    entries = []
    for case in cases:
        for tx in case.get("transactions", []):
            for value in tx.get("values", []):
                if value.get("basis") != "customs_value":
                    continue
                entries.append(
                    {
                        "entry_id": str(value.get("reference") or value["id"]),
                        "importer_tin": importer_tin,
                        "registered_on": value["date"],
                        "origin_country": "za",
                        "customs_value": value["amount"],
                        "import_vat": "0.00",
                        "currency": value["currency"],
                    }
                )
    contract = contract_for("asycuda", "entry")
    return [validate_record(contract, e) for e in entries]
