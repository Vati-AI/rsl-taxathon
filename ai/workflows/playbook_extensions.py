"""Governed extension contracts for additional tax-domain playbooks (TAX-080, ADR-033).

A domain such as PAYE, withholding, treaty/permanent-establishment facts or
digital services starts as a design-stage contract in
``playbook_extensions.synthetic.json``. The contract names the sources it
needs, the legal-review gate it must pass, the evaluation fixtures that must be
authored independently, and the deterministic checks, model roles, operations,
provenance, finding codes, alternative explanations and budgets of the future
playbook. It contains no code and cannot run.

``readiness`` reports why a domain may not be implemented yet: a required
source is not available in v1, the legal-review gate has no recorded reviewer,
a finding code has no entry in the TAX-052 legal-route registry, a fixture has
not been authored, or a new contract Finding category is still pending.
Readiness is always computed, never declared, so a contract cannot promote
itself. ``check_registry`` refuses any TAX-061 playbook registry entry named
after a domain that still has blockers.

The module is pure Python. It reads no case data and states no law.
"""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_PATH = Path(__file__).with_name("playbook_extensions.synthetic.json")
DEFAULT_FIXTURE_PATH = ROOT / "data/synthetic/tax080_domain_fixtures.json"
FORBIDDEN_CODES_PATH = ROOT / "data/synthetic/tax056_evaluation_fixtures.json"

# get_model roles (ADR-031). Models interpret; deterministic checks produce figures.
MODEL_ROLES = frozenset({"reasoning", "fast", "document"})
# Subset of the TAX-061 registry OPERATIONS. Extensions never apply actions themselves.
OPERATIONS = frozenset(
    {"read_case_records", "read_case_findings", "write_advisory_finding", "interrupt_for_human"}
)
# Subset of the TAX-061 registry PROVENANCE_REQUIREMENTS.
PROVENANCE = frozenset(
    {"case_scoped", "evidence_citations", "advisory_only", "deterministic", "human_checkpoint"}
)
REQUIRED_PROVENANCE = frozenset(
    {"case_scoped", "evidence_citations", "advisory_only", "deterministic"}
)
# Contract Finding.category enum (integration/api/openapi.json).
CONTRACT_CATEGORIES = frozenset(
    {
        "refund_reconciliation",
        "related_party",
        "worker_classification",
        "imported_services",
        "policy_gap",
        "other",
    }
)
SYSTEMS_OF_RECORD = frozenset({"frappe", "vati_api", "docling", "openaleph", "jube", "eoi_channel"})
SOURCE_STATUSES = frozenset({"available", "not_in_v1"})
# Every domain needs one independent fixture of each kind before implementation.
REQUIRED_FIXTURE_KINDS = frozenset(
    {
        "legitimate",
        "missing_evidence",
        "contradictory_evidence",
        "hostile_injection",
        "no_verified_route",
        "cross_tenant",
    }
)
LEGAL_REVIEWER_ROLE = "rsl_legal_reviewer"
MAX_TOOL_CALLS = 8  # create_investigation_state limit (TAX-022)

_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_FIXTURE_ID = re.compile(r"TAX080-[A-Z]{3,4}-\d{2}")
_REVIEWER = re.compile(r"SYN-REVIEWER-\d{2}")
_PROPOSER = re.compile(r"[A-Za-z0-9:._\-]{1,64}")


class CatalogError(ValueError):
    """The extension catalog is malformed or violates the governance policy."""


@dataclass(frozen=True)
class Source:
    id: str
    system_of_record: str
    v1_status: str


@dataclass(frozen=True)
class Fixture:
    id: str
    kind: str


@dataclass(frozen=True)
class DomainContract:
    name: str
    proposed_by: str
    finding_category: str
    proposed_contract_category: str | None
    deterministic_checks: tuple[str, ...]
    model_roles: frozenset[str]
    allowed_operations: frozenset[str]
    provenance: frozenset[str]
    finding_codes: tuple[str, ...]
    alternative_explanations: tuple[str, ...]
    sources: tuple[Source, ...]
    legal_questions: tuple[str, ...]
    legal_reviewed_by: str | None
    fixtures: tuple[Fixture, ...]


class _LegalRegistry(Protocol):
    @property
    def entries(self) -> Iterable[object]: ...


class _PlaybookRegistry(Protocol):
    def entries(self) -> Iterable[object]: ...


def _text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{key} must be a non-empty string")
    return value


def _list(raw: Mapping[str, object], key: str, *, min_items: int = 1) -> list[object]:
    value = raw.get(key)
    if not isinstance(value, list) or len(value) < min_items:
        raise CatalogError(f"{key} must list at least {min_items} item(s)")
    return value


def _strings(raw: Mapping[str, object], key: str, *, min_items: int = 1) -> tuple[str, ...]:
    values = _list(raw, key, min_items=min_items)
    if not all(isinstance(v, str) and v.strip() for v in values) or len(set(values)) != len(values):
        raise CatalogError(f"{key} must be distinct non-empty strings")
    return tuple(values)  # type: ignore[arg-type]


def _objects(raw: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    values = _list(raw, key)
    if not all(isinstance(v, Mapping) for v in values):
        raise CatalogError(f"{key} entries must be objects")
    return values  # type: ignore[return-value]


def _domain(raw: object, forbidden_codes: frozenset[str]) -> DomainContract:
    if not isinstance(raw, Mapping):
        raise CatalogError("domain must be an object")
    name = _text(raw, "name")
    if not _NAME.fullmatch(name):
        raise CatalogError("domain name is invalid")
    if raw.get("stage") != "design":
        raise CatalogError(f"{name}: stage must be design; readiness is computed, never declared")
    proposed_by = _text(raw, "proposed_by")
    if not _PROPOSER.fullmatch(proposed_by):
        raise CatalogError(f"{name}: proposed_by is invalid")
    _text(raw, "scope")

    category = raw.get("finding_category")
    if category not in CONTRACT_CATEGORIES:
        raise CatalogError(f"{name}: finding_category must be a contract Finding category")
    proposed_category = raw.get("proposed_contract_category")
    if proposed_category is not None and (
        not isinstance(proposed_category, str) or not _CODE.fullmatch(proposed_category)
    ):
        raise CatalogError(f"{name}: proposed_contract_category is invalid")

    checks = _objects(raw, "deterministic_checks")
    check_ids = tuple(_text(c, "id") for c in checks)
    for check in checks:
        _text(check, "description")
    if len(set(check_ids)) != len(check_ids):
        raise CatalogError(f"{name}: deterministic check ids must be distinct")

    roles = frozenset(_strings(raw, "model_roles", min_items=0))
    if roles - MODEL_ROLES:
        raise CatalogError(f"{name}: model_roles must be get_model roles")
    operations = frozenset(_strings(raw, "allowed_operations"))
    if operations - OPERATIONS:
        raise CatalogError(f"{name}: allowed_operations contains a disallowed operation")
    provenance = frozenset(_strings(raw, "provenance"))
    if provenance - PROVENANCE or not REQUIRED_PROVENANCE <= provenance:
        raise CatalogError(
            f"{name}: provenance must be cited, case-scoped, advisory and deterministic"
        )
    if "interrupt_for_human" in operations and "human_checkpoint" not in provenance:
        raise CatalogError(f"{name}: interrupt_for_human requires a human checkpoint")

    codes = _strings(raw, "finding_codes")
    if not all(_CODE.fullmatch(c) for c in codes):
        raise CatalogError(f"{name}: finding codes are invalid")
    if set(codes) & forbidden_codes:
        raise CatalogError(f"{name}: finding codes include a forbidden conclusion")
    alternatives = _strings(raw, "alternative_explanations", min_items=2)

    budgets = raw.get("budgets")
    if not isinstance(budgets, Mapping) or not all(
        isinstance(budgets.get(k), int) and not isinstance(budgets.get(k), bool) and budgets[k] > 0
        for k in ("max_tool_calls", "max_model_calls", "max_input_tokens")
    ):
        raise CatalogError(f"{name}: budgets need positive tool, model and token limits")
    if budgets["max_tool_calls"] > MAX_TOOL_CALLS:
        raise CatalogError(f"{name}: max_tool_calls exceeds the run limit")

    sources: list[Source] = []
    for item in _objects(raw, "required_sources"):
        source = Source(
            _text(item, "id"), str(item.get("system_of_record")), str(item.get("v1_status"))
        )
        _text(item, "description")
        _text(item, "reference")
        if (
            source.system_of_record not in SYSTEMS_OF_RECORD
            or source.v1_status not in SOURCE_STATUSES
        ):
            raise CatalogError(f"{name}: source {source.id} has an unknown system or status")
        sources.append(source)
    if len({s.id for s in sources}) != len(sources):
        raise CatalogError(f"{name}: source ids must be distinct")

    gate = raw.get("legal_review_gate")
    if not isinstance(gate, Mapping) or gate.get("reviewer_role") != LEGAL_REVIEWER_ROLE:
        raise CatalogError(f"{name}: legal_review_gate must name the RSL legal reviewer role")
    questions = _strings(gate, "questions")
    reviewed_by = gate.get("reviewed_by")
    if reviewed_by is None:
        if gate.get("reviewed_on") is not None:
            raise CatalogError(f"{name}: reviewed_on needs reviewed_by")
    else:
        if not isinstance(reviewed_by, str) or not _REVIEWER.fullmatch(reviewed_by):
            raise CatalogError(f"{name}: reviewed_by must be a synthetic reviewer")
        if reviewed_by == proposed_by:
            raise CatalogError(f"{name}: the proposer cannot pass its own legal-review gate")
        try:
            date.fromisoformat(str(gate.get("reviewed_on")))
        except ValueError as error:
            raise CatalogError(f"{name}: reviewed_on must be an ISO date") from error

    fixtures: list[Fixture] = []
    for item in _objects(raw, "evaluation_fixtures"):
        fixture = Fixture(_text(item, "id"), str(item.get("kind")))
        _text(item, "expected_outcome")
        if not _FIXTURE_ID.fullmatch(fixture.id) or fixture.kind not in REQUIRED_FIXTURE_KINDS:
            raise CatalogError(f"{name}: fixture {fixture.id} has an invalid id or kind")
        fixtures.append(fixture)
    if len({f.id for f in fixtures}) != len(fixtures):
        raise CatalogError(f"{name}: fixture ids must be distinct")
    if {f.kind for f in fixtures} != REQUIRED_FIXTURE_KINDS:
        raise CatalogError(f"{name}: evaluation fixtures must cover every required kind")

    return DomainContract(
        name=name,
        proposed_by=proposed_by,
        finding_category=category,
        proposed_contract_category=proposed_category,
        deterministic_checks=check_ids,
        model_roles=roles,
        allowed_operations=operations,
        provenance=provenance,
        finding_codes=codes,
        alternative_explanations=alternatives,
        sources=tuple(sources),
        legal_questions=questions,
        legal_reviewed_by=reviewed_by,
        fixtures=tuple(fixtures),
    )


def load_forbidden_codes(path: Path = FORBIDDEN_CODES_PATH) -> frozenset[str]:
    """TAX-056 forbidden conclusion codes; extension finding codes may not use them."""
    return frozenset(json.loads(path.read_text(encoding="utf-8"))["forbidden_codes"])


def parse_catalog(
    document: object, *, forbidden_codes: frozenset[str] | None = None
) -> dict[str, DomainContract]:
    if not isinstance(document, Mapping) or document.get("synthetic") is not True:
        raise CatalogError("extension catalog must be marked synthetic")
    version = document.get("catalog_version")
    if not isinstance(version, str) or not version.startswith("SYN-PBX-"):
        raise CatalogError("catalog_version must be a synthetic catalog version")
    forbidden = load_forbidden_codes() if forbidden_codes is None else forbidden_codes
    domains: dict[str, DomainContract] = {}
    for raw in _list(document, "domains"):
        contract = _domain(raw, forbidden)
        if contract.name in domains:
            raise CatalogError(f"duplicate domain {contract.name}")
        domains[contract.name] = contract
    fixture_ids = [f.id for c in domains.values() for f in c.fixtures]
    if len(set(fixture_ids)) != len(fixture_ids):
        raise CatalogError("fixture ids must be unique across domains")
    return domains


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> dict[str, DomainContract]:
    return parse_catalog(json.loads(path.read_text(encoding="utf-8")))


def authored_fixture_ids(path: Path = DEFAULT_FIXTURE_PATH) -> frozenset[str]:
    """Fixture ids authored independently (Leseli); empty until that file exists."""
    if not path.exists():
        return frozenset()
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping) or document.get("synthetic") is not True:
        raise CatalogError("domain fixtures must be marked synthetic")
    fixtures = document.get("fixtures", [])
    return frozenset(
        f["id"] for f in fixtures if isinstance(f, Mapping) and isinstance(f.get("id"), str)
    )


def readiness(
    contract: DomainContract,
    *,
    legal_registry: _LegalRegistry,
    authored_fixtures: Iterable[str],
) -> tuple[str, ...]:
    """Stable blocker codes; an empty result means implementation may start."""
    blockers = [f"source_not_in_v1:{s.id}" for s in contract.sources if s.v1_status != "available"]
    if contract.legal_reviewed_by is None:
        blockers.append("legal_review_pending")
    registered = {
        code
        for entry in legal_registry.entries
        for code in getattr(entry, "finding_codes", frozenset())
    }
    blockers += [
        f"finding_code_not_in_legal_registry:{c}"
        for c in contract.finding_codes
        if c not in registered
    ]
    authored = set(authored_fixtures)
    blockers += [f"fixture_not_authored:{f.id}" for f in contract.fixtures if f.id not in authored]
    if (
        contract.proposed_contract_category
        and contract.proposed_contract_category not in CONTRACT_CATEGORIES
    ):
        blockers.append(f"contract_category_pending:{contract.proposed_contract_category}")
    return tuple(blockers)


def check_registry(
    registry: _PlaybookRegistry,
    catalog: Mapping[str, DomainContract],
    *,
    legal_registry: _LegalRegistry,
    authored_fixtures: Iterable[str],
) -> dict[str, tuple[str, ...]]:
    """Registry entries (any status) for a domain that still has blockers."""
    authored = frozenset(authored_fixtures)
    violations: dict[str, tuple[str, ...]] = {}
    for entry in registry.entries():
        name = getattr(entry, "name", None)
        if getattr(entry, "kind", None) == "playbook" and isinstance(name, str) and name in catalog:
            blockers = readiness(
                catalog[name], legal_registry=legal_registry, authored_fixtures=authored
            )
            if blockers:
                violations[name] = blockers
    return violations


if __name__ == "__main__":  # pragma: no cover - maintenance helper
    from ai.workflows.legal_routes import load_registry as load_legal_registry

    legal = load_legal_registry()
    authored = authored_fixture_ids()
    for domain in load_catalog().values():
        found = readiness(domain, legal_registry=legal, authored_fixtures=authored)
        print(f"{domain.name}: {'ready' if not found else 'blocked'}")
        for blocker in found:
            print(f"  - {blocker}")
