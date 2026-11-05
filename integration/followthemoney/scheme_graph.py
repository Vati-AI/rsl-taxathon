"""Scheme, beneficial ownership and intermediary graph for one tax case (TAX-074).

Input is a ``scheme_graph_input.v1``: a TAX-036 ``tax_entity_bundle.v1`` for one tenant and case,
plus the records the bundle has no list for (directorships, advisers and intermediaries,
schemes, scheme participants and evidence-backed case-to-scheme links) and the extraction
revision each cited evidence was read from. The input is mapped to FollowTheMoney entities
first (``tax_mapping.map_bundle`` for the bundle; ``Directorship``, ``Representation``,
``Project`` and ``ProjectParticipant`` here, validated against the same locked schema subset).
The graph is then built from those FtM entities and their ``vati_tax_extension.v1`` records.

Rules the graph keeps:

- Legal owner, beneficial owner, director, adviser and intermediary are separate edge types.
  ``Ownership.ownershipType`` ``direct`` is legal ownership; ``beneficial`` and ``ultimate``
  are beneficial ownership; ``indirect`` is its own type; a record that states no type stays
  ``ownership_type_unstated`` instead of being guessed.
- Every edge comes from one source record and keeps its provenance: the source record, the
  FtM entity ID and digest, and each cited evidence ID with its SHA-256, locator and
  extraction revision. Records that disagree stay separate edges. Contradictions are listed
  next to them and never resolved, merged or overwritten.
- No guilt by association. Nodes and edges carry no risk, fraud or abuse labels, and the
  input cannot add one: unknown fields are refused and free text with labelling vocabulary is
  refused (``risk_label_not_allowed``). Connection results are descriptive (path plus
  evidence); their interpretation is always ``None`` and belongs to an officer through the
  existing ``finding.review`` route.
- Access is tenant- and case-scoped. Each (tenant, case) graph is stored and queried on its
  own, so a query never sees a node or edge that only another case or tenant recorded. Only
  roles holding ``case.read`` may query. Traversal is bounded (depth, nodes, paths and search
  steps) and reports truncation explicitly.
- Deterministic and replay-safe: the same input gives byte-identical output, a replay is a
  no-op, and a changed record under an existing ID is refused (``idempotency_conflict``)
  without changing the stored graph. Only inputs marked synthetic are accepted.

Standard library only, like ``tax_mapping``, so ``scripts/dev.py check`` runs the tests.
Callers put ``integration/followthemoney`` on ``sys.path`` (as the tests do).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import tax_mapping as tm

INPUT_VERSION = "scheme_graph_input.v1"
GRAPH_VERSION = "scheme_graph.v1"

# Roles holding ``case.read`` in backend/vati_tax/vati_tax/identity/policy.py (kept equal by a
# test). A taxpayer never reads the graph.
GRAPH_READ_ROLES = frozenset({"auditor", "specialist", "supervisor", "service"})

DIRECTOR_ROLES = frozenset({"director", "alternate_director", "managing_director", "secretary"})
ADVISER_ROLES = frozenset({"tax_adviser", "accountant", "legal_adviser", "statutory_auditor"})
INTERMEDIARY_ROLES = frozenset(
    {"agent", "nominee_shareholder", "nominee_director", "corporate_service_provider", "trustee"}
)
PARTICIPANT_ROLES = frozenset(
    {"participant", "counterparty", "adviser", "intermediary", "owner", "funder"}
)

OWNERSHIP_EDGE = {
    "direct": "legal_ownership",
    "beneficial": "beneficial_ownership",
    "ultimate": "beneficial_ownership",
    "indirect": "indirect_ownership",
    None: "ownership_type_unstated",
}
EDGE_TYPES = frozenset(
    {
        *OWNERSHIP_EDGE.values(),
        "directorship",
        "adviser_to",
        "intermediary_for",
        "scheme_participation",
        "case_subject",
        "case_scheme_link",
    }
)
NODE_TYPES = frozenset({"case", "party", "scheme"})

# Words that label a person or entity instead of describing a recorded relationship. They are
# refused in free text and field names of the graph input and never appear in graph output.
FORBIDDEN_TERMS = frozenset(
    {
        "abuse",
        "abusive",
        "aggressive",
        "blacklist",
        "blacklisted",
        "criminal",
        "dodgy",
        "evader",
        "evasion",
        "flag",
        "flagged",
        "fraud",
        "fraudster",
        "fraudulent",
        "guilt",
        "guilty",
        "illegal",
        "illicit",
        "launderer",
        "laundering",
        "redflag",
        "risk",
        "risky",
        "sham",
        "shell",
        "suspect",
        "suspicious",
        "tainted",
        "watchlist",
    }
)
# Field names that would carry a label or score onto a node or edge.
LABEL_FIELDS = frozenset({"label", "labels", "flags", "score", "scores", "tags", "rating"})

DEFAULT_DEPTH, MAX_DEPTH = 2, 4
DEFAULT_NODES, MAX_NODES = 50, 200
DEFAULT_PATHS, MAX_PATHS = 10, 25
SEARCH_STEP_BUDGET = 5_000
MAX_TEXT = 500
REVIEW_ROUTE = {"action": "finding.review", "decided_by": "officer", "interpretation": None}

_RECORD_KEYS = {"id", "source", "evidence", "start_date", "end_date"}
_KEYS = {
    "directorships": _RECORD_KEYS | {"director_id", "organization_id", "role"},
    "representations": _RECORD_KEYS | {"agent_id", "client_id", "role"},
    "schemes": _RECORD_KEYS | {"name", "description"},
    "scheme_participants": _RECORD_KEYS | {"scheme_id", "party_id", "role"},
    "case_scheme_links": {"id", "scheme_id", "source", "evidence"},
    "extraction_revisions": {"evidence_id", "document_sha256", "extraction_id", "revision"},
}
_INPUT_KEYS = {"version", "synthetic", "tenant_id", "case_id", "case_taxpayer_id", "entity_bundle"}
_INPUT_KEYS |= set(_KEYS)
_WORD = re.compile(r"[a-z]+")


class GraphInputError(ValueError):
    """The graph input is refused. ``code`` is the ADR-003 error code; ``field`` and ``reason``
    say what and why (``invalid_domain_input`` for bad input, ``idempotency_conflict`` for a
    changed record under an existing ID)."""

    def __init__(self, message: str, *, field: str, reason: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code or "invalid_domain_input"
        self.field = field
        self.reason = reason


class GraphAccessError(PermissionError):
    """A query is refused: ``forbidden`` (role) or ``not_found`` (tenant, case or node outside
    the caller's scope; the two are not told apart)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GraphScope:
    """Resolved by the caller's boundary from the authenticated membership (ADR-007): the
    tenant comes from the session, the case from a checked ``case.read``."""

    tenant_id: str
    case_id: str
    role: str


# ---------------------------------------------------------------- input validation


def _fail(message: str, field: str, reason: str) -> GraphInputError:
    return GraphInputError(message, field=field, reason=reason)


def _uuid4(value: Any, field: str) -> str:
    try:
        if not isinstance(value, str) or uuid.UUID(value).version != 4 or value != value.lower():
            raise ValueError
    except ValueError:
        raise _fail(f"{field} must be a lower-case UUIDv4", field, "invalid")
    return value


def labelling_terms(text: str) -> list[str]:
    """Labelling words in ``text`` (whole words, case-insensitive; ``red_flag`` and
    ``high-risk`` are caught by their parts)."""
    words = _WORD.findall(text.lower())
    joined = {a + b for a, b in zip(words, words[1:])}
    return sorted((set(words) | joined) & FORBIDDEN_TERMS)


def _neutral(text: str, field: str) -> str:
    if labelling_terms(text):
        raise _fail(
            f"{field} labels a party; describe the recorded relationship and send any "
            "assessment to officer review",
            field,
            "risk_label_not_allowed",
        )
    return text


def _keys(record: Mapping[str, Any], allowed: set[str], where: str) -> None:
    for key in sorted(set(record) - allowed):
        words = set(_WORD.findall(str(key).lower()))
        reason = (
            "risk_label_not_allowed"
            if words & (FORBIDDEN_TERMS | LABEL_FIELDS) or labelling_terms(str(key))
            else "unexpected_field"
        )
        raise _fail(f"{where}.{key} is not accepted", f"{where}.{key}", reason)


def _role(record: Mapping[str, Any], allowed: frozenset[str], where: str) -> str:
    role = record.get("role")
    if role not in allowed:
        raise _fail(f"{where}.role must be one of {sorted(allowed)}", f"{where}.role", "invalid")
    return str(role)


def _dates(record: Mapping[str, Any], where: str) -> tuple[str | None, str | None]:
    start, end = tm._date(record, "start_date", where), tm._date(record, "end_date", where)
    if start and end and end < start:
        raise _fail(f"{where}.end_date is before start_date", f"{where}.end_date", "invalid_date")
    return start, end


def _records(data: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    items = data.get(key, [])
    if not isinstance(items, list) or not all(isinstance(x, Mapping) for x in items):
        raise _fail(f"{key} must be a list of objects", key, "invalid")
    for i, item in enumerate(items):
        _keys(item, _KEYS[key], f"{key}[{i}]")
    tm._unique(items, "evidence_id" if key == "extraction_revisions" else "id", key)
    return items


def canonical_digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- mapping to FtM


class _GraphMapper:
    """Maps the TAX-074 records to FtM entities, reusing the TAX-036 mapper's helpers."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        bundle = data.get("entity_bundle")
        if not isinstance(bundle, Mapping):
            raise _fail("entity_bundle must be a tax_entity_bundle.v1", "entity_bundle", "invalid")
        if bundle.get("tenant_id") != data["tenant_id"]:
            raise _fail(
                "entity_bundle.tenant_id must equal tenant_id",
                "entity_bundle.tenant_id",
                "tenant_mismatch",
            )
        try:
            self.base = tm.map_bundle(bundle)
        except tm.MappingError as exc:
            raise GraphInputError(
                str(exc), field=f"entity_bundle.{exc.field}", reason=exc.reason
            ) from exc
        self.tenant_id: str = data["tenant_id"]
        self.entities: list[dict[str, Any]] = list(self.base.entities)
        self.extensions: list[dict[str, Any]] = list(self.base.extensions)
        self.evidence: dict[str, str] = {}
        self.parties: dict[str, tuple[str, str]] = {}
        for ext in self.base.extensions:
            if ext["kind"] == "evidence":
                self.evidence[ext["source"]["record_id"]] = ext["tax"]["document_sha256"]
            elif ext["kind"] == "party":
                self.parties[ext["source"]["record_id"]] = (ext["entity_id"], ext["schema"])
        self.schemes: dict[str, str] = {}

    def proofs(self, citations: list[dict[str, Any]]) -> list[str]:
        return [tm.entity_id(self.tenant_id, "evidence", c["evidence_id"]) for c in citations]

    def party(
        self, record: Mapping[str, Any], key: str, where: str, *, organization: bool = False
    ) -> str:
        ref = tm._record_id(record, key, where)
        if ref not in self.parties:
            raise _fail(
                f"{where}.{key} refers to an unknown party", f"{where}.{key}", "unknown_party"
            )
        eid, schema = self.parties[ref]
        if organization and "Organization" not in tm.SUBSET["schemata"][schema]["is_a"]:
            raise _fail(
                f"{where}.{key} must be a company: FtM Directorship.organization is an Organization",
                f"{where}.{key}",
                "invalid_directorship_organization",
            )
        return eid

    def add(
        self,
        kind: str,
        record: Mapping[str, Any],
        where: str,
        schema: str,
        props: Mapping[str, Any],
        *,
        evidence_required: bool = False,
        tax: dict[str, Any] | None = None,
    ) -> str:
        rid = tm._record_id(record, "id", where)
        citations = tm._citations(record, where, self.evidence)
        if evidence_required and not citations:
            raise _fail(f"{where} must cite evidence", f"{where}.evidence", "missing_evidence")
        eid = tm.entity_id(self.tenant_id, kind, rid)
        properties: dict[str, list[str]] = {}
        for key, value in {**props, "proof": self.proofs(citations)}.items():
            values = sorted({str(v) for v in (value if isinstance(value, list) else [value]) if v})
            if values:
                properties[key] = values
        self.entities.append({"id": eid, "schema": schema, "properties": properties})
        self.extensions.append(
            {
                "version": tm.EXTENSION_VERSION,
                "entity_id": eid,
                "schema": schema,
                "kind": kind,
                "tenant_id": self.tenant_id,
                "source": tm._source(record, where),
                "evidence": citations,
                "tax": tax or {},
                "conflict_ids": [],
            }
        )
        return eid

    def directorship(self, d: Mapping[str, Any], where: str) -> None:
        director = self.party(d, "director_id", where)
        organization = self.party(d, "organization_id", where, organization=True)
        if director == organization:
            raise _fail(f"{where} says an entity directs itself", where, "self_relationship")
        start, end = _dates(d, where)
        role = _role(d, DIRECTOR_ROLES, where)
        self.add(
            "directorship",
            d,
            where,
            "Directorship",
            {
                "director": director,
                "organization": organization,
                "role": role,
                "startDate": start,
                "endDate": end,
                "recordId": d["id"],
            },
        )

    def representation(self, r: Mapping[str, Any], where: str) -> None:
        agent = self.party(r, "agent_id", where)
        client = self.party(r, "client_id", where)
        if agent == client:
            raise _fail(f"{where} says an entity represents itself", where, "self_relationship")
        start, end = _dates(r, where)
        role = _role(r, ADVISER_ROLES | INTERMEDIARY_ROLES, where)
        self.add(
            "representation",
            r,
            where,
            "Representation",
            {
                "agent": agent,
                "client": client,
                "role": role,
                "startDate": start,
                "endDate": end,
                "recordId": r["id"],
            },
        )

    def scheme(self, s: Mapping[str, Any], where: str) -> None:
        name = _neutral(tm._str(s, "name", where), f"{where}.name")
        description = tm._opt_str(s, "description", where)
        if description is not None:
            _neutral(description, f"{where}.description")
        start, end = _dates(s, where)
        self.schemes[s["id"]] = self.add(
            "scheme",
            s,
            where,
            "Project",
            {
                "name": name,
                "projectId": s["id"],
                "description": description,
                "startDate": start,
                "endDate": end,
            },
            evidence_required=True,
        )

    def scheme_ref(self, record: Mapping[str, Any], where: str) -> str:
        ref = tm._record_id(record, "scheme_id", where)
        if ref not in self.schemes:
            raise _fail(f"{where}.scheme_id is unknown", f"{where}.scheme_id", "unknown_scheme")
        return self.schemes[ref]

    def participant(self, p: Mapping[str, Any], where: str) -> None:
        scheme = self.scheme_ref(p, where)
        party = self.party(p, "party_id", where)
        start, end = _dates(p, where)
        self.add(
            "scheme_participant",
            p,
            where,
            "ProjectParticipant",
            {
                "project": scheme,
                "participant": party,
                "role": _role(p, PARTICIPANT_ROLES, where),
                "startDate": start,
                "endDate": end,
                "recordId": p["id"],
            },
            evidence_required=True,
        )

    def extraction_revisions(self, items: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for i, x in enumerate(items):
            where = f"extraction_revisions[{i}]"
            ev = x.get("evidence_id")
            if ev not in self.evidence:
                raise _fail(
                    f"{where} names unknown evidence", f"{where}.evidence_id", "unknown_evidence"
                )
            if x.get("document_sha256") != self.evidence[ev]:
                raise _fail(
                    f"{where} names a hash that differs from the stored original",
                    f"{where}.document_sha256",
                    "provenance_hash_mismatch",
                )
            rev = x.get("revision")
            if not isinstance(rev, int) or isinstance(rev, bool) or rev < 1:
                raise _fail(f"{where}.revision must be >= 1", f"{where}.revision", "invalid")
            out[str(ev)] = {
                "extraction_id": tm._record_id(x, "extraction_id", where),
                "revision": rev,
            }
        return out


def map_graph_input(data: Mapping[str, Any]) -> tuple[_GraphMapper, dict[str, dict[str, Any]]]:
    """Validate a ``scheme_graph_input.v1`` and map it to FtM entities plus extensions."""
    try:
        return _map_graph_input(data)
    except tm.MappingError as exc:  # raised by the shared TAX-036 field validators
        raise GraphInputError(str(exc), field=exc.field, reason=exc.reason) from exc


def _map_graph_input(data: Mapping[str, Any]) -> tuple[_GraphMapper, dict[str, dict[str, Any]]]:
    if not isinstance(data, Mapping) or data.get("version") != INPUT_VERSION:
        raise _fail(f"version must be {INPUT_VERSION}", "version", "unsupported_version")
    _keys(data, _INPUT_KEYS, "input")
    if data.get("synthetic") is not True:
        raise _fail("Only synthetic graph inputs are accepted", "synthetic", "not_synthetic")
    _uuid4(data.get("tenant_id"), "tenant_id")
    _uuid4(data.get("case_id"), "case_id")
    m = _GraphMapper(data)
    taxpayer = tm._record_id(data, "case_taxpayer_id", "input")
    if taxpayer not in m.parties:
        raise _fail("case_taxpayer_id is not a bundle party", "case_taxpayer_id", "unknown_party")
    for i, d in enumerate(_records(data, "directorships")):
        m.directorship(d, f"directorships[{i}]")
    for i, r in enumerate(_records(data, "representations")):
        m.representation(r, f"representations[{i}]")
    for i, s in enumerate(_records(data, "schemes")):
        m.scheme(s, f"schemes[{i}]")
    for i, p in enumerate(_records(data, "scheme_participants")):
        m.participant(p, f"scheme_participants[{i}]")
    links = _records(data, "case_scheme_links")
    for i, link in enumerate(links):
        where = f"case_scheme_links[{i}]"
        m.scheme_ref(link, where)
        tm._source(link, where)
        tm._record_id(link, "id", where)
        if not tm._citations(link, where, m.evidence):
            raise _fail(f"{where} must cite evidence", f"{where}.evidence", "missing_evidence")
    revisions = m.extraction_revisions(_records(data, "extraction_revisions"))
    m.entities.sort(key=lambda e: e["id"])
    m.extensions.sort(key=lambda x: x["entity_id"])
    try:
        tm.validate_entities(m.entities)
    except tm.EntityValidationError as exc:  # a mapping bug, not caller input
        raise GraphInputError(str(exc), field="entities", reason="ftm_validation_failed") from exc
    return m, revisions


# ---------------------------------------------------------------- graph construction


def _node_id(tenant_id: str, kind: str, ref: str) -> str:
    return "gn-" + hashlib.sha256(f"{tenant_id}\x1f{kind}\x1f{ref}".encode()).hexdigest()[:40]


def _edge_id(tenant_id: str, case_id: str, kind: str, ref: str) -> str:
    raw = f"{tenant_id}\x1f{case_id}\x1f{kind}\x1f{ref}"
    return "ge-" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def _first(props: Mapping[str, list[str]], key: str) -> str | None:
    values = props.get(key)
    return values[0] if values else None


def build_case_graph(data: Mapping[str, Any]) -> dict[str, Any]:
    """Build the deterministic graph of one (tenant, case) from a ``scheme_graph_input.v1``."""
    m, revisions = map_graph_input(data)
    tenant, case = m.tenant_id, data["case_id"]
    entities = {e["id"]: e for e in m.entities}
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}

    def cite(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**c, "extraction": revisions.get(c["evidence_id"])} for c in citations]

    case_node = _node_id(tenant, "case", case)
    nodes[case_node] = {"node_id": case_node, "type": "case", "case_id": case}
    for ext in m.extensions:
        props = entities[ext["entity_id"]]["properties"]
        if ext["kind"] == "party":
            nodes[ext["entity_id"]] = {
                "node_id": ext["entity_id"],
                "type": "party",
                "ftm_schema": ext["schema"],
                "name": _first(props, "name"),
                "party_role": ext["tax"]["role"],
                "record_id": ext["source"]["record_id"],
            }
        elif ext["kind"] == "scheme":
            nodes[ext["entity_id"]] = {
                "node_id": ext["entity_id"],
                "type": "scheme",
                "ftm_schema": "Project",
                "name": _first(props, "name"),
                "description": _first(props, "description"),
                "record_id": ext["source"]["record_id"],
                "start_date": _first(props, "startDate"),
                "end_date": _first(props, "endDate"),
            }

    def edge(kind: str, ref: str, source: str, target: str, **detail: Any) -> None:
        eid = _edge_id(tenant, case, kind, ref)
        edges[eid] = {"edge_id": eid, "type": kind, "source": source, "target": target, **detail}

    ends = {
        "Ownership": ("owner", "asset"),
        "Directorship": ("director", "organization"),
        "Representation": ("agent", "client"),
        "ProjectParticipant": ("participant", "project"),
    }
    for ext in m.extensions:
        entity = entities[ext["entity_id"]]
        if entity["schema"] not in ends:
            continue
        props = entity["properties"]
        start_key, end_key = ends[entity["schema"]]
        role = _first(props, "role")
        if entity["schema"] == "Ownership":
            kind = OWNERSHIP_EDGE[_first(props, "ownershipType")]
        elif entity["schema"] == "Directorship":
            kind = "directorship"
        elif entity["schema"] == "Representation":
            kind = "adviser_to" if role in ADVISER_ROLES else "intermediary_for"
        else:
            kind = "scheme_participation"
        edge(
            kind,
            entity["id"],
            str(_first(props, start_key)),
            str(_first(props, end_key)),
            role=role,
            ownership_type=_first(props, "ownershipType"),
            percentage=_first(props, "percentage"),
            start_date=_first(props, "startDate"),
            end_date=_first(props, "endDate"),
            provenance={
                "source": ext["source"],
                "ftm_entity_id": entity["id"],
                "ftm_schema": entity["schema"],
                "entity_digest": tm.entity_digest(entity),
                "evidence": cite(ext["evidence"]),
            },
        )

    taxpayer_eid = m.parties[data["case_taxpayer_id"]][0]
    taxpayer_ext = next(x for x in m.extensions if x["entity_id"] == taxpayer_eid)
    edge(
        "case_subject",
        taxpayer_eid,
        case_node,
        taxpayer_eid,
        provenance={
            "source": taxpayer_ext["source"],
            "ftm_entity_id": taxpayer_eid,
            "ftm_schema": taxpayer_ext["schema"],
            "entity_digest": tm.entity_digest(entities[taxpayer_eid]),
            "evidence": cite(taxpayer_ext["evidence"]),
        },
    )
    for i, link in enumerate(data.get("case_scheme_links", [])):
        scheme = m.schemes[link["scheme_id"]]
        edge(
            "case_scheme_link",
            "link:" + link["id"],
            case_node,
            scheme,
            provenance={
                "source": tm._source(link, f"case_scheme_links[{i}]"),
                "ftm_entity_id": None,
                "ftm_schema": None,
                "entity_digest": canonical_digest(link),
                "evidence": cite(tm._citations(link, f"case_scheme_links[{i}]", m.evidence)),
            },
        )
    for e in edges.values():
        for key in ("role", "ownership_type", "percentage", "start_date", "end_date"):
            e.setdefault(key, None)
        e["evidence_status"] = "cited" if e["provenance"]["evidence"] else "source_record_only"
    return {
        "version": GRAPH_VERSION,
        "tenant_id": tenant,
        "case_id": case,
        "case_node_id": case_node,
        "synthetic": True,
        "nodes": dict(sorted(nodes.items())),
        "edges": dict(sorted(edges.items())),
    }


# ---------------------------------------------------------------- contradictions


def _stakes(edges: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for e in edges:
        if not e["type"].endswith("ownership") and e["type"] != "ownership_type_unstated":
            continue
        out.append(
            {
                "record_id": e["provenance"]["source"]["record_id"],
                "entity_id": e["edge_id"],
                "owner": e["source"],
                "asset": e["target"],
                "pct": Decimal(e["percentage"]),
                "type": e["ownership_type"],
                "start": e["start_date"],
                "end": e["end_date"],
            }
        )
    return sorted(out, key=lambda s: s["entity_id"])


def _beneficial_over_100(stakes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Beneficial (or ultimate) stakes in one asset that exceed 100% on some date even when
    each owner is counted at the lowest percentage any record gives them. Counting an owner
    once avoids reporting a single disagreement twice."""
    found = []
    for kind in ("beneficial", "ultimate"):
        typed = [s for s in stakes if s["type"] == kind]
        for asset in sorted({s["asset"] for s in typed}):
            held = [s for s in typed if s["asset"] == asset]
            seen: set[tuple[str, ...]] = set()
            for point in sorted({s["start"] or tm.OPEN_START for s in held}):
                active = [s for s in held if tm._overlaps(s, {"start": point, "end": point})]
                lowest: dict[str, Decimal] = {}
                for s in active:
                    lowest[s["owner"]] = min(lowest.get(s["owner"], s["pct"]), s["pct"])
                total = sum(lowest.values(), Decimal(0))
                ids = tuple(sorted(s["entity_id"] for s in active))
                if total > tm.HUNDRED and ids not in seen:
                    seen.add(ids)
                    found.append(
                        {
                            "reason": "beneficial_ownership_exceeds_100_percent",
                            "ownership_type": kind,
                            "asset_entity_id": asset,
                            "record_ids": sorted(s["record_id"] for s in active),
                            "entity_ids": list(ids),
                            "on_date": None if point == tm.OPEN_START else point,
                            "total_percentage": str(total),
                        }
                    )
    return found


def contradictions(edges: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Ownership records among ``edges`` that cannot all be true. Nothing is resolved.

    Reuses the TAX-036 checks (``disagreeing_ownership_records``,
    ``ownership_exceeds_100_percent`` for direct stakes) and adds
    ``beneficial_ownership_exceeds_100_percent`` (see ``_beneficial_over_100``). ``edge_ids``
    name the graph edges.
    """
    stakes = _stakes(edges)
    found = tm.ownership_conflicts(stakes)
    found += _beneficial_over_100(stakes)
    out = []
    for c in found:
        detail = {k: v for k, v in c.items() if k not in {"id", "entity_ids", "asset_entity_id"}}
        out.append(
            {
                **detail,
                "contradiction_id": "gc-" + canonical_digest([c["reason"], c["entity_ids"]])[:24],
                "asset_node_id": c["asset_entity_id"],
                "edge_ids": c["entity_ids"],
                "status": "unresolved",
            }
        )
    return sorted(out, key=lambda c: c["contradiction_id"])


def _touching(graph: Mapping[str, Any], returned: set[str]) -> list[dict[str, Any]]:
    """Contradictions of the whole case graph that involve a returned edge. The other side
    is named even when a limit kept it out of the result, so a contradiction is never hidden
    by truncation. Edges of other cases are not part of the graph and never appear."""
    out = []
    for c in contradictions(graph["edges"].values()):
        if returned & set(c["edge_ids"]):
            out.append({**c, "edge_ids_not_returned": sorted(set(c["edge_ids"]) - returned)})
    return out


# ---------------------------------------------------------------- store and queries


def _limit(value: Any, default: int, maximum: int, field: str) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise _fail(f"{field} must be 1..{maximum}", field, "invalid_limit")
    return value


_HUMAN = {
    "legal_ownership": "is recorded as legal owner of {pct}% of",
    "beneficial_ownership": "is recorded as {btype} owner of {pct}% of",
    "indirect_ownership": "is recorded as holding an indirect interest of {pct}% in",
    "ownership_type_unstated": "is recorded as owner of {pct}% (legal or beneficial not stated) of",
    "directorship": "is recorded as {role} of",
    "adviser_to": "is recorded as {role} to",
    "intermediary_for": "is recorded as {role} for",
    "scheme_participation": "is recorded as {role} in scheme",
    "case_subject": "has as its taxpayer",
    "case_scheme_link": "is linked to scheme",
}


class SchemeGraphStore:
    """In-memory (tenant, case) graph store. A future Vati API persists the same records."""

    def __init__(self) -> None:
        self._graphs: dict[tuple[str, str], dict[str, Any]] = {}
        self._applied: dict[tuple[str, str], set[str]] = {}

    # -- writes

    def ingest(self, data: Mapping[str, Any]) -> dict[str, Any]:
        """Add one input to its case graph. Same input again: ``replayed``, nothing changes.
        New records are added; existing ones are never removed. A record whose content
        changed under the same ID is refused as ``idempotency_conflict`` and nothing is
        applied."""
        built = build_case_graph(data)
        key = (built["tenant_id"], built["case_id"])
        digest = canonical_digest(data)
        if digest in self._applied.get(key, set()):
            return {"status": "replayed", "input_digest": digest, **self._counts(key)}
        current = self._graphs.get(key, {"nodes": {}, "edges": {}})
        for kind in ("nodes", "edges"):
            for item_id, item in built[kind].items():
                old = current[kind].get(item_id)
                if old is not None and old != item:
                    raise GraphInputError(
                        f"{kind[:-1]} {item_id} already holds different content for this case",
                        field=kind,
                        reason="changed_record",
                        code="idempotency_conflict",
                    )
        merged = {
            **built,
            "nodes": dict(sorted({**current["nodes"], **built["nodes"]}.items())),
            "edges": dict(sorted({**current["edges"], **built["edges"]}.items())),
        }
        self._graphs[key] = merged
        self._applied.setdefault(key, set()).add(digest)
        return {"status": "applied", "input_digest": digest, **self._counts(key)}

    def _counts(self, key: tuple[str, str]) -> dict[str, int]:
        g = self._graphs[key]
        return {"node_count": len(g["nodes"]), "edge_count": len(g["edges"])}

    # -- reads

    def _graph(self, scope: GraphScope) -> dict[str, Any]:
        if scope.role not in GRAPH_READ_ROLES:
            raise GraphAccessError("forbidden", "This role cannot read case graphs")
        graph = self._graphs.get((scope.tenant_id, scope.case_id))
        if graph is None:
            raise GraphAccessError("not_found", "Case graph not found")
        return graph

    @staticmethod
    def _node(graph: Mapping[str, Any], node_id: str | None) -> str:
        node = graph["case_node_id"] if node_id is None else node_id
        if node not in graph["nodes"]:
            raise GraphAccessError("not_found", "Node not found in this case")
        return str(node)

    @staticmethod
    def _adjacency(graph: Mapping[str, Any]) -> dict[str, list[tuple[str, str]]]:
        adj: dict[str, list[tuple[str, str]]] = {n: [] for n in graph["nodes"]}
        for eid, e in graph["edges"].items():
            adj[e["source"]].append((eid, e["target"]))
            adj[e["target"]].append((eid, e["source"]))
        for n in adj:
            adj[n].sort()
        return adj

    def neighbourhood(
        self,
        scope: GraphScope,
        start_node_id: str | None = None,
        *,
        max_depth: int | None = None,
        max_nodes: int | None = None,
    ) -> dict[str, Any]:
        """Nodes and edges within ``max_depth`` hops of a node (default: the case node),
        breadth-first in edge-ID order. Ownership contradictions among the returned edges are
        listed, unresolved."""
        graph = self._graph(scope)
        depth_limit = _limit(max_depth, DEFAULT_DEPTH, MAX_DEPTH, "max_depth")
        node_limit = _limit(max_nodes, DEFAULT_NODES, MAX_NODES, "max_nodes")
        start = self._node(graph, start_node_id)
        adj = self._adjacency(graph)
        depth = {start: 0}
        queue = deque([start])
        truncated = {"depth": False, "nodes": False}
        while queue:
            node = queue.popleft()
            for _, other in adj[node]:
                if other in depth:
                    continue
                if depth[node] >= depth_limit:
                    truncated["depth"] = True
                    continue
                if len(depth) >= node_limit:
                    truncated["nodes"] = True
                    continue
                depth[other] = depth[node] + 1
                queue.append(other)
        edges = [
            e for e in graph["edges"].values() if e["source"] in depth and e["target"] in depth
        ]
        return self._envelope(
            graph,
            {
                "start_node_id": start,
                "nodes": [{**graph["nodes"][n], "depth": depth[n]} for n in sorted(depth)],
                "edges": [self._describe(graph, e) for e in edges],
                "contradictions": _touching(graph, {e["edge_id"] for e in edges}),
            },
            limits={"max_depth": depth_limit, "max_nodes": node_limit},
            truncated=truncated,
        )

    def connections(
        self,
        scope: GraphScope,
        from_node_id: str,
        to_node_id: str,
        *,
        max_depth: int | None = None,
        max_paths: int | None = None,
    ) -> dict[str, Any]:
        """Simple paths between two nodes of the case graph, shortest first, then by edge IDs.
        Each step says which record states the link and cites its evidence. A path describes
        recorded relationships only; ``interpretation`` is always ``None`` and any assessment
        goes to officer review (``finding.review``)."""
        graph = self._graph(scope)
        depth_limit = _limit(max_depth, MAX_DEPTH, MAX_DEPTH, "max_depth")
        path_limit = _limit(max_paths, DEFAULT_PATHS, MAX_PATHS, "max_paths")
        start, goal = self._node(graph, from_node_id), self._node(graph, to_node_id)
        adj = self._adjacency(graph)
        found: list[list[str]] = []
        truncated = {"depth": False, "paths": False, "search_budget": False}
        steps = 0
        # Breadth-first over simple paths, so shorter paths come first and the order is fixed.
        queue: deque[tuple[str, list[str], frozenset[str]]] = deque(
            [(start, [], frozenset({start}))]
        )
        while queue and start != goal:
            node, path, seen = queue.popleft()
            for eid, other in adj[node]:
                steps += 1
                if steps > SEARCH_STEP_BUDGET:
                    truncated["search_budget"] = True
                    queue.clear()
                    break
                if other in seen:
                    continue
                if other == goal:
                    if len(found) >= path_limit:
                        truncated["paths"] = True
                        queue.clear()
                        break
                    found.append(path + [eid])
                elif len(path) + 1 >= depth_limit:
                    truncated["depth"] = True
                else:
                    queue.append((other, path + [eid], seen | {other}))
        paths = []
        for edge_ids in found:
            described = [self._describe(graph, graph["edges"][eid]) for eid in edge_ids]
            paths.append(
                {
                    "length": len(edge_ids),
                    "steps": described,
                    "evidence_ids": sorted(
                        {c["evidence_id"] for s in described for c in s["provenance"]["evidence"]}
                    ),
                    "interpretation": None,
                }
            )
        path_edges = {eid for p in found for eid in p}
        return self._envelope(
            graph,
            {
                "from_node_id": start,
                "to_node_id": goal,
                "paths": paths,
                "contradictions": _touching(graph, path_edges),
            },
            limits={"max_depth": depth_limit, "max_paths": path_limit},
            truncated=truncated,
        )

    def scheme_cases(
        self, tenant_id: str, role: str, scheme_node_id: str, readable_case_ids: Iterable[str]
    ) -> list[str]:
        """Cases (among those the caller may read, in ``tenant_id``) whose graph links the
        scheme. Returns case IDs only; each case is then queried in its own scope."""
        if role not in GRAPH_READ_ROLES:
            raise GraphAccessError("forbidden", "This role cannot read case graphs")
        out = []
        for case_id in sorted(set(readable_case_ids)):
            graph = self._graphs.get((tenant_id, case_id))
            if graph and any(
                e["type"] == "case_scheme_link" and e["target"] == scheme_node_id
                for e in graph["edges"].values()
            ):
                out.append(case_id)
        return out

    # -- output

    @staticmethod
    def _label(graph: Mapping[str, Any], node_id: str) -> str:
        node = graph["nodes"][node_id]
        if node["type"] == "case":
            return f"case {node['case_id']}"
        return str(node["name"] or node["record_id"])

    def _describe(self, graph: Mapping[str, Any], e: Mapping[str, Any]) -> dict[str, Any]:
        role = (e["role"] or "").replace("_", " ")
        btype = "ultimate beneficial" if e["ownership_type"] == "ultimate" else "beneficial"
        phrase = _HUMAN[e["type"]].format(pct=e["percentage"], role=role, btype=btype)
        src = e["provenance"]["source"]
        text = (
            f"{self._label(graph, e['source'])} {phrase} {self._label(graph, e['target'])} "
            f"(source {src['record_type']} {src['record_id']})"
        )
        return {**e, "description": text}

    @staticmethod
    def _envelope(
        graph: Mapping[str, Any],
        data: dict[str, Any],
        *,
        limits: dict[str, int],
        truncated: dict[str, bool],
    ) -> dict[str, Any]:
        return {
            "data": {
                "version": GRAPH_VERSION,
                "tenant_id": graph["tenant_id"],
                "case_id": graph["case_id"],
                "synthetic": True,
                **data,
            },
            "meta": {
                "limits": limits,
                "truncated": truncated,
                "is_truncated": any(truncated.values()),
                "review_route": dict(REVIEW_ROUTE),
            },
        }
