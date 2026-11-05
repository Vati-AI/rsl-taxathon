"""Write mapped FtM entities into a restricted OpenAleph collection, replay-safely (TAX-036).

Route from the locked OpenAleph source (aleph/views/collections_api.py, ``bulk``):
``POST /api/2/collections/{collection_id}/_bulk`` with a JSON list of entity dicts answers
204. It needs WRITE on the collection and a logged-in key (``can_bulk_import``). For
non-admin keys OpenAleph forces ``safe`` and ``clean``: it strips checksum properties and
cleans values. It signs every ID with the collection namespace, so the same Vati entity ID
always lands on the same OpenAleph entity in that collection.

Repeated ingest: the caller keeps a ledger of ``entity_id -> digest`` already written to
the collection (``tax_mapping.MappingResult.digests``). ``plan`` splits a mapping into new,
unchanged and changed entities, and only new ones are sent. Bulk writes go through FtM's
fragment aggregator (aleph/logic/processing.py ``bulk_write``), so this adapter does not
rely on a second write replacing the first. A changed entity is refused as
``idempotency_conflict`` and needs a reviewed re-index of that collection instead.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

BATCH = 500


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes = b""


Transport = Callable[[str, str, Any], Response]


class IngestError(Exception):
    """``code`` is an ``ErrorCode`` from integration/api/openapi.json."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        entity_ids: list[str] | None = None,
        upstream_status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.entity_ids = entity_ids or []
        self.upstream_status = upstream_status
        self.retryable = retryable


@dataclass(frozen=True)
class IngestPlan:
    new: list[dict[str, Any]]
    unchanged: list[str]
    changed: list[str]


def plan(
    entities: list[dict[str, Any]], digests: Mapping[str, str], ledger: Mapping[str, str]
) -> IngestPlan:
    new: list[dict[str, Any]] = []
    unchanged: list[str] = []
    changed: list[str] = []
    for entity in entities:
        previous = ledger.get(entity["id"])
        if previous is None:
            new.append(entity)
        elif previous == digests[entity["id"]]:
            unchanged.append(entity["id"])
        else:
            changed.append(entity["id"])
    return IngestPlan(new=new, unchanged=unchanged, changed=changed)


def _error_code(status: int) -> str:
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "upstream_unavailable"
    if status in (401, 403):
        return "forbidden"
    return "invalid_request"


def ingest(
    transport: Transport,
    collection_id: int,
    entities: list[dict[str, Any]],
    digests: Mapping[str, str],
    ledger: Mapping[str, str],
) -> dict[str, str]:
    """Send entities not yet in the collection and return the updated ledger.

    Nothing is sent if any entity changed since it was written. The ledger grows only after
    OpenAleph accepted a batch, so a failure part-way can simply be retried.
    """
    if isinstance(collection_id, bool) or not isinstance(collection_id, int) or collection_id < 1:
        raise IngestError("invalid_domain_input", "collection_id must be a positive integer")
    work = plan(entities, digests, ledger)
    if work.changed:
        raise IngestError(
            "idempotency_conflict",
            "Entities changed since they were written; re-index the collection after review",
            entity_ids=sorted(work.changed),
        )
    updated = dict(ledger)
    path = f"/api/2/collections/{collection_id}/_bulk"
    for start in range(0, len(work.new), BATCH):
        batch = work.new[start : start + BATCH]
        response = transport("POST", path, batch)
        if response.status != 204:
            raise IngestError(
                _error_code(response.status),
                f"OpenAleph bulk write answered {response.status}",
                entity_ids=[e["id"] for e in batch],
                upstream_status=response.status,
                retryable=response.status >= 500 or response.status == 429,
            )
        updated.update({e["id"]: digests[e["id"]] for e in batch})
    return updated


def http_transport(base_url: str, api_key: str, timeout_s: float = 30.0) -> Transport:
    """Transport for a real OpenAleph (``Authorization: ApiKey``). The key must belong to a
    non-admin service user with WRITE on only its tenant's collections."""
    # host.docker.internal: the developer's machine seen from the Frappe container (TAX-085).
    local = ("http://127.0.0.1", "http://localhost", "http://host.docker.internal:", "https://")
    if not base_url.startswith(local):
        raise ValueError("OPENALEPH_BASE_URL must be https or loopback")

    def call(method: str, path: str, body: Any = None) -> Response:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            base_url.rstrip("/") + path,
            data=data,
            method=method,
            headers={"Authorization": "ApiKey " + api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as resp:
                return Response(resp.status, resp.read())
        except urllib.error.HTTPError as exc:
            return Response(exc.code, exc.read())

    return call
