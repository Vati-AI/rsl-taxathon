"""Fixture sink for Vati's outbound messages to RSL (TAX-124, ADR-048).

``FixtureRslSink`` stands in for the RSL endpoints that WSO2 API Manager would publish
(ADR-040). It opens no network connection. It checks each message against its outbound
contract, keeps it in a local directory and answers with a receipt that says it is a
fixture. It never changes anything: there is no RSL status, payment, offset or
assessment behind it.

- Delivery honours the idempotency key: the same key with the same message returns the
  stored receipt (``replayed: true``); the same key with a different message is refused.
- A system configured as unavailable raises ``SinkUnavailable``. The caller keeps the
  message queued and shows it as not delivered; nothing is recorded as sent.
- ``lookup`` finds an earlier delivery by key, so a sender can reconcile a call whose
  outcome it did not see.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from integration.rsl_sinks.contracts import ROUTE, RSL_SYSTEMS, validate_message

FIXTURE_ADAPTER_ID = "vati.rsl_sinks.fixture"
FIXTURE_ADAPTER_VERSION = "tax124-fixture-v1"
GATEWAY = "wso2-shaped-fixture-gateway"


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def message_digest(message: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical(message).encode()).hexdigest()


class SinkError(Exception):
    """A delivery failure with a stable code; the message is never echoed."""

    retryable = False

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SinkUnavailable(SinkError):
    """The RSL endpoint did not answer: keep the message queued and try again later."""

    retryable = True

    def __init__(self) -> None:
        super().__init__("rsl_sink_unavailable")


class SinkConflict(SinkError):
    """The idempotency key was already used for a different message."""

    def __init__(self) -> None:
        super().__init__("idempotency_key_reused")


class FixtureRslSink:
    """Receive outbound messages for one tenant into ``directory`` (synthetic only)."""

    def __init__(
        self, directory: str | os.PathLike[str], tenant_id: str, *, unavailable: Iterable[str] = ()
    ) -> None:
        self.directory = Path(directory)
        self.tenant_id = tenant_id
        self.unavailable = frozenset(unavailable)
        unknown = self.unavailable - set(RSL_SYSTEMS)
        if unknown:
            raise ValueError(f"unknown RSL system: {sorted(unknown)[0]}")

    def _path(self, system: str, idempotency_key: str) -> Path:
        name = hashlib.sha256(f"{self.tenant_id}\x1f{idempotency_key}".encode()).hexdigest()
        return self.directory / self.tenant_id / system / f"{name}.json"

    def _receipt(self, stored: Mapping[str, Any], replayed: bool) -> dict[str, Any]:
        return {**stored["receipt"], "replayed": replayed}

    def deliver(self, message: Mapping[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        """Accept one message (see the module docstring); returns a fixture receipt."""
        checked = validate_message(message)
        system = checked["rsl_reference"]["system"]
        if system in self.unavailable:
            raise SinkUnavailable()
        digest = message_digest(checked)
        path = self._path(system, idempotency_key)
        stored = self._read(path)
        if stored is not None:
            if stored["message_sha256"] != digest:
                raise SinkConflict()
            return self._receipt(stored, replayed=True)
        receipt = {
            "receipt_id": "FIXTURE-" + digest[:20].upper(),
            "fixture": True,
            "adapter_id": FIXTURE_ADAPTER_ID,
            "adapter_version": FIXTURE_ADAPTER_VERSION,
            "gateway": GATEWAY,
            "route": ROUTE,
            "system": system,
            "contract_id": checked["contract_id"],
            "message_sha256": digest,
        }
        record = {"message_sha256": digest, "receipt": receipt, "message": checked}
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.stem}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
        temp.write_text(canonical(record), encoding="utf-8")
        try:
            # A hard link appears complete and fails if the name exists, so two senders
            # racing on one key cannot both deliver and a reader never sees half a file.
            os.link(temp, path)
        except FileExistsError:
            stored = self._read(path)
            if stored is None or stored["message_sha256"] != digest:
                raise SinkConflict() from None
            return self._receipt(stored, replayed=True)
        finally:
            temp.unlink(missing_ok=True)
        return self._receipt(record, replayed=False)

    def lookup(self, system: str, idempotency_key: str) -> dict[str, Any] | None:
        """The receipt of an earlier delivery under ``idempotency_key``, if any."""
        stored = self._read(self._path(system, idempotency_key))
        return None if stored is None else self._receipt(stored, replayed=True)

    def received(self, system: str) -> list[dict[str, Any]]:
        """Every message this sink accepted for ``system`` (demo and tests)."""
        folder = self.directory / self.tenant_id / system
        if not folder.is_dir():
            return []
        rows = [self._read(path) for path in sorted(folder.glob("*.json"))]
        return [row["message"] for row in rows if row is not None]

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        loaded: dict[str, Any] = json.loads(text)
        return loaded
