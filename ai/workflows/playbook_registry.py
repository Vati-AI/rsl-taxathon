"""Versioned registry of approved investigation tools and playbooks (TAX-061).

Each registry entry pins a tool or playbook version to the SHA-256 of its exact
source files, its approval status and approver, the operations it may perform
and the provenance its outputs must carry. The runner executes only an
``approved`` playbook whose tools are all approved and whose current source
still matches the approved digest; anything else is rejected with a stable
code. A run record lets a known approved version be replayed and checked for
an identical output digest.

The v1 registry is a synthetic prototype: approvals use ``SYN-SUPERVISOR-NN``
placeholder identities and the loader refuses anything else. Real activation
requires the supervisor-only TAX-046 ``playbook.approve`` action, holdout
evaluation and rollback (docs/ARCHITECTURE.md). The JSON names entries only;
builders come from the in-code ``PLAYBOOK_BUILDERS`` table, so registry data can
never make the worker import or execute arbitrary code.

Run ``python -m ai.workflows.playbook_registry`` to print current source
digests when a reviewed change needs a new registry version.
"""

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypeAlias

from ai.workflows.human_checkpoints import action_digest
from ai.workflows.imported_services_playbook import (
    build_imported_services_graph,
    make_imported_services_tool,
)
from ai.workflows.refund_lanes import (
    RefundRecordSource,
    build_refund_lane_graph,
    make_refund_reconciliation_tool,
)
from ai.workflows.related_party_playbook import build_related_party_graph, make_related_party_tool
from ai.workflows.state import InvestigationState
from ai.workflows.work_arrangement_playbook import (
    build_work_arrangement_graph,
    make_work_arrangement_tool,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_PATH = Path(__file__).with_name("playbook_registry.synthetic.json")

EntryKind: TypeAlias = Literal["tool", "playbook"]
Status: TypeAlias = Literal["approved", "proposed", "retired"]
STATUSES = frozenset({"approved", "proposed", "retired"})
# Every operation a registered tool or playbook may declare.
OPERATIONS = frozenset(
    {
        "read_case_records",
        "read_case_findings",
        "write_advisory_finding",
        "interrupt_for_human",
        "apply_approved_action",
    }
)
# Provenance every output of a registered entry must carry.
PROVENANCE_REQUIREMENTS = frozenset(
    {
        "case_scoped",
        "evidence_citations",
        "advisory_only",
        "human_checkpoint",
        "deterministic",
    }
)
_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_VERSION = re.compile(r"[0-9]+(\.[0-9]+){0,2}")
_APPROVER = re.compile(r"SYN-SUPERVISOR-\d{2}")
_PROPOSER = re.compile(r"[A-Za-z0-9:._\-]{1,64}")
_SOURCE = re.compile(r"ai/(tools|workflows)/[a-z0-9_.]+\.(py|json)")
_DIGEST = re.compile(r"[0-9a-f]{64}")
MAX_ENTRIES = 100

GraphBuilder = Callable[..., Any]

# Fixed name -> builder table. The registry can select among these, never add to them.
PLAYBOOK_BUILDERS: dict[str, GraphBuilder] = {
    # ``models`` (TAX-097) is ``ai.models.get_model`` or a double; None keeps the
    # playbook model-free. Models only interpret; figures and lanes stay deterministic.
    "refund_lanes": lambda source, models=None, **_: build_refund_lane_graph(
        make_refund_reconciliation_tool(source), models=models
    ),
    "imported_services": lambda source, **_: build_imported_services_graph(
        make_imported_services_tool(source)
    ),
    "related_party": lambda source, models=None, **_: build_related_party_graph(
        make_related_party_tool(source), models=models
    ),
    "work_arrangement": lambda source, *, checkpointer, agent_id, **_: build_work_arrangement_graph(
        make_work_arrangement_tool(source), checkpointer, agent_id=agent_id
    ),
}


class RegistryError(ValueError):
    """The registry document is malformed or violates the approval policy."""


class PlaybookRejected(Exception):
    """A tool or playbook version may not run; carries a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RegistryEntry:
    kind: EntryKind
    name: str
    version: str
    status: Status
    source_files: tuple[str, ...]
    content_digest: str
    allowed_operations: frozenset[str]
    provenance: frozenset[str]
    tools: tuple[tuple[str, str], ...]
    proposed_by: str
    approved_by: str | None
    approved_on: date | None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.name, self.version)

    def stamp(self, registry_version: str) -> dict[str, object]:
        return {
            "kind": self.kind,
            "name": self.name,
            "version": self.version,
            "content_digest": self.content_digest,
            "approved_by": self.approved_by,
            "registry_version": registry_version,
        }


def source_digest(files: tuple[str, ...], root: Path = ROOT) -> str:
    """SHA-256 over the named sources with line endings normalised (CRLF == LF)."""
    digest = hashlib.sha256()
    for name in files:
        if not _SOURCE.fullmatch(name):
            raise RegistryError(f"source file {name!r} is outside ai/tools or ai/workflows")
        text = (root / name).read_bytes().replace(b"\r\n", b"\n")
        digest.update(name.encode("utf-8") + b"\x00" + text + b"\x00")
    return digest.hexdigest()


def _str_list(
    raw: Mapping[str, object], key: str, *, allowed: frozenset[str] | None = None
) -> list[str]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RegistryError(f"{key} must be a list of strings")
    if allowed is not None and set(value) - allowed:
        raise RegistryError(f"{key} contains unknown values")
    return list(value)


def _entry(raw: object) -> RegistryEntry:
    if not isinstance(raw, Mapping):
        raise RegistryError("entry must be an object")
    kind, name, version, status = (raw.get(k) for k in ("kind", "name", "version", "status"))
    if kind not in ("tool", "playbook"):
        raise RegistryError("entry kind must be tool or playbook")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise RegistryError("entry name is invalid")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise RegistryError("entry version is invalid")
    if status not in STATUSES:
        raise RegistryError("entry status is invalid")
    sources = _str_list(raw, "source_files")
    if not sources or not all(_SOURCE.fullmatch(s) for s in sources):
        raise RegistryError("source_files must name files under ai/tools or ai/workflows")
    digest = raw.get("content_digest")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise RegistryError("content_digest must be a SHA-256 hex digest")
    operations = frozenset(_str_list(raw, "allowed_operations", allowed=OPERATIONS))
    provenance = frozenset(_str_list(raw, "provenance", allowed=PROVENANCE_REQUIREMENTS))
    if not operations or not {"case_scoped", "advisory_only"} <= provenance:
        raise RegistryError("entries need operations and case-scoped advisory provenance")
    if "apply_approved_action" in operations and "human_checkpoint" not in provenance:
        raise RegistryError("apply_approved_action requires a human checkpoint")
    tools: list[tuple[str, str]] = []
    for ref in _str_list(raw, "tools"):
        tool_name, _, tool_version = ref.partition("@")
        if not _NAME.fullmatch(tool_name) or not _VERSION.fullmatch(tool_version):
            raise RegistryError("tool references must be name@version")
        tools.append((tool_name, tool_version))
    if kind == "tool" and tools:
        raise RegistryError("tools cannot reference other tools")
    if kind == "playbook" and (not tools or name not in PLAYBOOK_BUILDERS):
        raise RegistryError("playbooks need tools and a builder known to the code")
    proposed_by = raw.get("proposed_by")
    if not isinstance(proposed_by, str) or not _PROPOSER.fullmatch(proposed_by):
        raise RegistryError("proposed_by is required")
    approved_by = raw.get("approved_by")
    approved_on_raw = raw.get("approved_on")
    approved_on: date | None = None
    if status == "approved":
        if not isinstance(approved_by, str) or not _APPROVER.fullmatch(approved_by):
            raise RegistryError("approved entries need a synthetic supervisor approver")
        if approved_by == proposed_by:
            raise RegistryError("an entry cannot be approved by its proposer")
        try:
            approved_on = date.fromisoformat(str(approved_on_raw))
        except ValueError as error:
            raise RegistryError("approved_on must be an ISO date") from error
    elif approved_by is not None:
        raise RegistryError("only approved entries carry an approver")
    return RegistryEntry(
        kind=kind,
        name=name,
        version=version,
        status=status,
        source_files=tuple(sources),
        content_digest=digest,
        allowed_operations=operations,
        provenance=provenance,
        tools=tuple(tools),
        proposed_by=proposed_by,
        approved_by=approved_by if isinstance(approved_by, str) else None,
        approved_on=approved_on,
    )


class PlaybookRegistry:
    def __init__(
        self, registry_version: str, entries: list[RegistryEntry], *, root: Path = ROOT
    ) -> None:
        self.registry_version = registry_version
        self.root = root
        self._entries: dict[tuple[str, str, str], RegistryEntry] = {}
        for entry in entries:
            if entry.key in self._entries:
                raise RegistryError(f"duplicate entry {entry.key}")
            self._entries[entry.key] = entry
        approved_names = [(e.kind, e.name) for e in entries if e.status == "approved"]
        if len(approved_names) != len(set(approved_names)):
            raise RegistryError("at most one approved version per tool or playbook")
        for entry in entries:
            for tool_name, tool_version in entry.tools:
                if ("tool", tool_name, tool_version) not in self._entries:
                    raise RegistryError(f"{entry.name} references unknown tool {tool_name}")

    def entries(self) -> tuple[RegistryEntry, ...]:
        return tuple(self._entries.values())

    def require_approved(self, kind: EntryKind, name: str, version: str) -> RegistryEntry:
        """Return the entry only if it is approved and its source still matches."""
        entry = self._entries.get((kind, name, version))
        if entry is None:
            raise PlaybookRejected("version_not_registered")
        if entry.status != "approved":
            raise PlaybookRejected(f"version_{entry.status}")
        if source_digest(entry.source_files, self.root) != entry.content_digest:
            raise PlaybookRejected("source_digest_mismatch")
        for tool_name, tool_version in entry.tools:
            self.require_approved("tool", tool_name, tool_version)
        return entry


def parse_registry(document: object, *, root: Path = ROOT) -> PlaybookRegistry:
    if not isinstance(document, Mapping) or document.get("synthetic") is not True:
        raise RegistryError("v1 registry must be marked synthetic")
    version = document.get("registry_version")
    if not isinstance(version, str) or not version.startswith("SYN-PBR-"):
        raise RegistryError("registry_version must be a synthetic registry version")
    entries = document.get("entries")
    if not isinstance(entries, list) or not entries or len(entries) > MAX_ENTRIES:
        raise RegistryError("entries must be a non-empty list")
    return PlaybookRegistry(version, [_entry(raw) for raw in entries], root=root)


def load_registry(path: Path = DEFAULT_REGISTRY_PATH, *, root: Path = ROOT) -> PlaybookRegistry:
    return parse_registry(json.loads(path.read_text(encoding="utf-8")), root=root)


# Run-time measurements of a model interpretation (TAX-097). They differ between
# otherwise identical runs, so they are recorded but not part of the replay digest.
_RUNTIME_KEYS = frozenset({"analysis_provenance", "disclosure", "router_provenance"})


def _without_runtime(finding: object) -> object:
    if not isinstance(finding, dict) or not isinstance(finding.get("interpretation"), dict):
        return finding
    interpretation = {k: v for k, v in finding["interpretation"].items() if k not in _RUNTIME_KEYS}
    return {**finding, "interpretation": interpretation}


def _output_digest(final: Mapping[str, object]) -> str:
    results = final.get("tool_results")
    findings = final.get("findings", [])
    return action_digest(
        {
            "findings": [_without_runtime(f) for f in findings]
            if isinstance(findings, list)
            else findings,
            "tool_results": [
                {k: v for k, v in r.items() if k != "correlation_id"}
                for r in results
                if isinstance(r, dict)
            ]
            if isinstance(results, list)
            else [],
        }
    )


def run_playbook(
    registry: PlaybookRegistry,
    name: str,
    version: str,
    source: RefundRecordSource,
    state: InvestigationState,
    *,
    config: Mapping[str, object] | None = None,
    build_options: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    """Run an approved playbook version; return (final state, replayable run record)."""
    entry = registry.require_approved("playbook", name, version)
    graph = PLAYBOOK_BUILDERS[name](source, **dict(build_options or {}))
    final = graph.invoke(state, config) if config is not None else graph.invoke(state)
    stamp = entry.stamp(registry.registry_version)
    findings = final.get("findings", [])
    final["findings"] = [
        {**item, "provenance": stamp} if isinstance(item, dict) else item for item in findings
    ]
    record: dict[str, object] = {
        "playbook": name,
        "version": version,
        "content_digest": entry.content_digest,
        "registry_version": registry.registry_version,
        "tenant_id": state["tenant_id"],
        "case_id": state["case_id"],
        "run_id": state["run_id"],
        "input_digest": action_digest(dict(state)),
        "output_digest": _output_digest(final),
    }
    return final, record


def replay_run(
    registry: PlaybookRegistry,
    record: Mapping[str, object],
    source: RefundRecordSource,
    state: InvestigationState,
    *,
    config: Mapping[str, object] | None = None,
    build_options: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Re-run the recorded approved version and confirm it reproduces the output."""
    name, version = record.get("playbook"), record.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise PlaybookRejected("invalid_run_record")
    entry = registry.require_approved("playbook", name, version)
    if entry.content_digest != record.get("content_digest"):
        raise PlaybookRejected("source_digest_mismatch")
    if action_digest(dict(state)) != record.get("input_digest"):
        raise PlaybookRejected("input_mismatch")
    _, replayed = run_playbook(
        registry, name, version, source, state, config=config, build_options=build_options
    )
    return {
        "status": "replay_matched"
        if replayed["output_digest"] == record.get("output_digest")
        else "replay_diverged",
        "playbook": name,
        "version": version,
        "content_digest": entry.content_digest,
        "output_digest": replayed["output_digest"],
    }


if __name__ == "__main__":  # pragma: no cover - maintenance helper
    for item in load_registry().entries():
        current = source_digest(item.source_files)
        state = "ok" if current == item.content_digest else "CHANGED"
        print(f"{item.kind:8} {item.name}@{item.version}: {current} [{state}]")
