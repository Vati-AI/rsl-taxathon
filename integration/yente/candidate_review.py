"""yente match candidates and officer review decisions (TAX-037).

yente proposes candidates. It never decides identity. This adapter:

- asks yente for candidates for mapped Vati FtM parties (TAX-036) and records each one with the
  dataset name and the dataset version that yente reports as indexed;
- normalizes every candidate to the contract ``MatchCandidate`` (score as a 4-decimal string,
  ``review_status`` ``unreviewed`` until an officer decides);
- flags ambiguous candidate sets (more than one candidate above the threshold);
- compares identifiers locally (registration, ID and tax numbers, birth date) as corroboration,
  without sending tax or ID numbers to yente;
- records officer review decisions append-only, bound to the dataset version they were made on.
  A confirmation needs corroboration beyond the score: an agreeing identifier or cited case
  evidence. A conflicting identifier blocks confirmation. Confirming never changes or merges the
  Vati entity or the yente entity; it produces a separate link record.

Routes and fields from the locked yente source (commit 6580a2aae2b662559d75301f06820bb0b8f7a122,
version 5.5.0): ``yente/routers/admin.py`` ``GET /catalog`` returns ``datasets`` (each with
``name``, ``version``, ``index_version``, ``load``), ``current`` and ``outdated``.
``yente/routers/match.py`` ``POST /match/{dataset}`` takes ``{"queries": {key: {"schema",
"properties"}}}`` with query parameters ``threshold`` (default 0.70) and ``limit`` (default 5)
and answers ``{"responses": {key: {"status", "results", "total", "query"}}, "limit"}``. Each
result (``yente/data/common.py`` ``ScoredEntityResponse``) has ``id``, ``caption``, ``schema``,
``properties``, ``datasets``, ``score`` and ``match`` (score at or above the threshold). Results
below the threshold are not returned. yente discourages batching, so one query is sent per call.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

# Defaults of the locked yente version (yente/settings.py SCORE_THRESHOLD, MATCH_PAGE).
DEFAULT_THRESHOLD = 0.70
DEFAULT_LIMIT = 5

# Roles allowed to review match candidates. Kept equal to FINDING_REVIEW in
# backend/vati_tax/vati_tax/identity/policy.py by a test. A service identity never decides.
REVIEWER_ROLES = frozenset({"auditor", "specialist", "supervisor"})

OUTCOMES = ("confirmed", "rejected", "ambiguous")
# Contract MatchCandidate.review_status for each outcome. An officer who cannot tell keeps the
# candidate unreviewed; the "ambiguous" decision is still recorded.
CONTRACT_STATUS = {"confirmed": "confirmed", "rejected": "rejected", "ambiguous": "unreviewed"}

# Properties sent to yente per FtM schema, following the locked match route's guidance on which
# properties help. Tax and ID numbers are not sent; they are compared locally instead.
QUERY_PROPERTIES = {
    "Company": {
        "name": "name",
        "country": "jurisdiction",
        "registrationNumber": "registrationNumber",
        "incorporationDate": "incorporationDate",
    },
    "Organization": {
        "name": "name",
        "country": "country",
        "registrationNumber": "registrationNumber",
    },
    "LegalEntity": {
        "name": "name",
        "country": "country",
        "registrationNumber": "registrationNumber",
    },
    "Person": {"name": "name", "country": "nationality", "birthDate": "birthDate"},
}

# Identifier properties that corroborate a candidate when they agree and block confirmation
# when they disagree. Names are what the score is built on, so they never corroborate.
IDENTIFIERS = ("registrationNumber", "idNumber", "taxNumber", "birthDate")
# Country/jurisdiction agreement is recorded but is not enough to confirm.
COUNTRY_PROPERTIES = ("country", "jurisdiction", "nationality")

_NAMESPACE = uuid.UUID("6f1b2c9e-7d3a-5e40-9b1c-0a3e7d2f4c11")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes = b""


Transport = Callable[[str, str, Any], Response]


class ReviewError(Exception):
    """``code`` is an ``ErrorCode`` from integration/api/openapi.json; ``reason`` is specific."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        reason: str | None = None,
        upstream_status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason
        self.upstream_status = upstream_status
        self.retryable = retryable


@dataclass(frozen=True)
class DatasetVersion:
    name: str
    version: str
    index_current: bool


@dataclass(frozen=True)
class Candidate:
    """One yente proposal for one Vati entity. ``properties`` is yente's copy, unchanged."""

    query_entity_id: str
    candidate_id: str
    caption: str
    schema: str
    dataset: str
    dataset_version: str
    score: str
    above_threshold: bool
    threshold: str
    properties: Mapping[str, tuple[str, ...]]
    corroboration: Mapping[str, str]
    retrieved_at: str

    def identifier_agreements(self) -> list[str]:
        return sorted(
            k for k, v in self.corroboration.items() if k in IDENTIFIERS and v == "agrees"
        )

    def identifier_conflicts(self) -> list[str]:
        return sorted(
            k for k, v in self.corroboration.items() if k in IDENTIFIERS and v == "conflicts"
        )


@dataclass(frozen=True)
class CandidateSet:
    query_entity_id: str
    dataset: str
    dataset_version: str
    candidates: tuple[Candidate, ...]
    ambiguous: bool

    def get(self, candidate_id: str) -> Candidate | None:
        return next((c for c in self.candidates if c.candidate_id == candidate_id), None)


@dataclass(frozen=True)
class Decision:
    decision_id: str
    tenant_id: str
    case_id: str
    entity_id: str
    candidate_id: str
    dataset: str
    dataset_version: str
    outcome: str
    reviewed_by: str
    reviewer_role: str
    rationale: str
    evidence_ids: tuple[str, ...]
    basis: tuple[str, ...]
    score_at_review: str
    decided_at: str
    idempotency_key: str
    digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "tenant_id": self.tenant_id,
            "case_id": self.case_id,
            "entity_id": self.entity_id,
            "candidate_id": self.candidate_id,
            "dataset": self.dataset,
            "dataset_version": self.dataset_version,
            "outcome": self.outcome,
            "reviewed_by": self.reviewed_by,
            "reviewer_role": self.reviewer_role,
            "rationale": self.rationale,
            "evidence_ids": list(self.evidence_ids),
            "basis": list(self.basis),
            "score_at_review": self.score_at_review,
            "decided_at": self.decided_at,
        }


@dataclass(frozen=True)
class MatchLink:
    """A confirmed link between a Vati entity and a yente entity. Neither entity is changed."""

    entity_id: str
    candidate_id: str
    dataset: str
    dataset_version: str
    decision_id: str
    basis: tuple[str, ...]


# --- yente calls -------------------------------------------------------------------------------


def _upstream_error(status: int, what: str) -> ReviewError:
    if status == 429:
        code = "rate_limited"
    elif status >= 500:
        code = "upstream_unavailable"
    elif status == 404:
        code = "not_found"
    elif status in (401, 403):
        code = "forbidden"
    else:
        code = "invalid_request"
    return ReviewError(
        code,
        f"yente {what} answered {status}",
        upstream_status=status,
        retryable=status >= 500 or status == 429,
    )


def _json(response: Response, what: str) -> Any:
    try:
        return json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError(
            "upstream_unavailable", f"yente {what} returned invalid JSON", retryable=True
        ) from exc


def dataset_version(transport: Transport, dataset: str) -> DatasetVersion:
    """Return the indexed version of ``dataset`` from ``GET /catalog``.

    A dataset that isn't loaded is ``not_found``. One whose index is behind its manifest version
    is refused as ``upstream_unavailable`` (retry after re-indexing), so no candidate is ever
    recorded against a version yente is not actually serving.
    """
    response = transport("GET", "/catalog", None)
    if response.status != 200:
        raise _upstream_error(response.status, "catalog")
    body = _json(response, "catalog")
    if not isinstance(body, dict) or not isinstance(body.get("datasets"), list):
        raise ReviewError("upstream_unavailable", "yente catalog has no datasets list")
    for item in body["datasets"]:
        if isinstance(item, dict) and item.get("name") == dataset:
            version = item.get("version")
            indexed = item.get("index_version")
            if not isinstance(version, str) or not version:
                raise ReviewError("upstream_unavailable", f"yente dataset {dataset} has no version")
            if item.get("load") is False:
                break
            current = dataset in (body.get("current") or []) and indexed == version
            if not current:
                raise ReviewError(
                    "upstream_unavailable",
                    f"yente index for {dataset} is not current (index {indexed}, manifest {version})",
                    reason="index_stale",
                    retryable=True,
                )
            return DatasetVersion(dataset, version, True)
    raise ReviewError(
        "not_found", f"yente has no loaded dataset {dataset}", reason="dataset_not_loaded"
    )


def build_query(entity: Mapping[str, Any]) -> dict[str, Any]:
    """A yente query example for a mapped Vati FtM party, with only the allowed properties."""
    schema = entity.get("schema")
    allowed = QUERY_PROPERTIES.get(schema) if isinstance(schema, str) else None
    if allowed is None:
        raise ReviewError(
            "invalid_domain_input",
            f"Entity schema {schema!r} is not matched against yente",
            reason="unsupported_schema",
        )
    props = entity.get("properties")
    if not isinstance(props, Mapping):
        raise ReviewError("invalid_domain_input", "Entity has no properties", reason="invalid")
    query: dict[str, list[str]] = {}
    for vati_prop, yente_prop in allowed.items():
        values = [v for v in _values(props.get(vati_prop)) if v.strip()]
        if values:
            query.setdefault(yente_prop, []).extend(values)
    if not query.get("name"):
        raise ReviewError(
            "invalid_domain_input", "Entity has no name to match", reason="missing_name"
        )
    return {"schema": schema, "properties": query}


def match(
    transport: Transport,
    dataset: str,
    entities: Iterable[Mapping[str, Any]],
    *,
    retrieved_at: str,
    threshold: float = DEFAULT_THRESHOLD,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, CandidateSet]:
    """Ask yente for candidates for each entity and return them keyed by Vati entity ID."""
    if not (0.0 < threshold <= 1.0):
        raise ReviewError("invalid_request", "threshold must be in (0, 1]")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ReviewError("invalid_request", "limit must be an integer from 1 to 50")
    version = dataset_version(transport, dataset)
    query_string = urllib.parse.urlencode({"threshold": f"{threshold:.4f}", "limit": limit})
    path = f"/match/{urllib.parse.quote(dataset, safe='')}?{query_string}"
    out: dict[str, CandidateSet] = {}
    for entity in entities:
        entity_id = entity.get("id")
        if not isinstance(entity_id, str) or not entity_id:
            raise ReviewError("invalid_domain_input", "Entity has no id", reason="invalid")
        query = build_query(entity)
        response = transport("POST", path, {"queries": {"q": query}})
        if response.status != 200:
            raise _upstream_error(response.status, "match")
        body = _json(response, "match")
        result = (body.get("responses") or {}).get("q") if isinstance(body, dict) else None
        if not isinstance(result, dict) or not isinstance(result.get("results"), list):
            raise ReviewError("upstream_unavailable", "yente match response has no results for q")
        candidates = [
            _candidate(entity, item, version, threshold, retrieved_at) for item in result["results"]
        ]
        candidates.sort(key=lambda c: (-float(c.score), c.candidate_id))
        above = [c for c in candidates if c.above_threshold]
        out[entity_id] = CandidateSet(
            query_entity_id=entity_id,
            dataset=dataset,
            dataset_version=version.version,
            candidates=tuple(candidates),
            ambiguous=len(above) > 1,
        )
    return out


def _candidate(
    entity: Mapping[str, Any],
    item: Any,
    version: DatasetVersion,
    threshold: float,
    retrieved_at: str,
) -> Candidate:
    if not isinstance(item, dict):
        raise ReviewError("upstream_unavailable", "yente returned a non-object result")
    cid = item.get("id")
    if not isinstance(cid, str) or not 1 <= len(cid) <= 255:
        raise ReviewError("upstream_unavailable", "yente result has no usable id")
    datasets = item.get("datasets") or []
    if version.name not in datasets:
        # The route is scoped to one dataset; anything else is not a candidate we can version.
        raise ReviewError(
            "upstream_unavailable",
            f"yente result {cid} is not from dataset {version.name}",
            reason="foreign_dataset",
        )
    raw = item.get("score")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        raise ReviewError("upstream_unavailable", f"yente result {cid} has no numeric score")
    if not 0.0 <= raw <= 1.0:
        raise ReviewError("upstream_unavailable", f"yente result {cid} score is outside [0, 1]")
    raw_props = item.get("properties")
    props: dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}
    frozen = {str(k): tuple(_values(v)) for k, v in sorted(props.items())}
    return Candidate(
        query_entity_id=str(entity["id"]),
        candidate_id=cid,
        caption=str(item.get("caption") or ""),
        schema=str(item.get("schema") or ""),
        dataset=version.name,
        dataset_version=version.version,
        score=format_score(float(raw)),
        above_threshold=bool(item.get("match", float(raw) >= threshold)),
        threshold=format_score(threshold),
        properties=frozen,
        corroboration=corroborate(entity.get("properties") or {}, frozen),
        retrieved_at=retrieved_at,
    )


def format_score(score: float) -> str:
    """The contract's decimal string, 4 places, in [0, 1]."""
    return f"{min(1.0, max(0.0, score)):.4f}"


def _values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None and not isinstance(v, (dict, list))]
    return [str(value)]


def _norm(value: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", value.upper())


def corroborate(
    vati_props: Mapping[str, Any], candidate_props: Mapping[str, Iterable[str]]
) -> dict[str, str]:
    """Compare identifiers and country locally: ``agrees``, ``conflicts`` or ``absent``."""
    out: dict[str, str] = {}
    for prop in IDENTIFIERS:
        ours = {_norm(v) for v in _values(vati_props.get(prop)) if _norm(v)}
        theirs = {_norm(v) for v in candidate_props.get(prop, ()) if _norm(v)}
        if not ours or not theirs:
            out[prop] = "absent"
        else:
            out[prop] = "agrees" if ours & theirs else "conflicts"
    ours_c = {v.lower() for p in COUNTRY_PROPERTIES for v in _values(vati_props.get(p))}
    theirs_c = {v.lower() for p in COUNTRY_PROPERTIES for v in candidate_props.get(p, ())}
    if not ours_c or not theirs_c:
        out["country"] = "absent"
    else:
        out["country"] = "agrees" if ours_c & theirs_c else "conflicts"
    return out


def to_contract(candidate: Candidate, decision: Decision | None) -> dict[str, Any]:
    """The contract ``MatchCandidate`` (additionalProperties: false) for a candidate."""
    status = CONTRACT_STATUS[decision.outcome] if decision else "unreviewed"
    return {
        "candidate_id": candidate.candidate_id,
        "dataset": candidate.dataset,
        "dataset_version": candidate.dataset_version,
        "score": candidate.score,
        "review_status": status,
        "reviewed_by": decision.reviewed_by if decision and status != "unreviewed" else None,
    }


# --- officer review ----------------------------------------------------------------------------


@dataclass
class ReviewLedger:
    """Append-only review decisions for one tenant. The latest decision per candidate and
    dataset version is current; earlier ones stay in ``decisions`` as history."""

    tenant_id: str
    decisions: list[Decision] = field(default_factory=list)

    def current(self, entity_id: str, candidate_id: str, dataset_version: str) -> Decision | None:
        found = None
        for d in self.decisions:
            if (d.entity_id, d.candidate_id, d.dataset_version) == (
                entity_id,
                candidate_id,
                dataset_version,
            ):
                found = d
        return found

    def contract_candidates(self, candidate_set: CandidateSet) -> list[dict[str, Any]]:
        return [
            to_contract(
                c, self.current(candidate_set.query_entity_id, c.candidate_id, c.dataset_version)
            )
            for c in candidate_set.candidates
        ]

    def links(self) -> list[MatchLink]:
        latest: dict[tuple[str, str, str], Decision] = {}
        for d in self.decisions:
            latest[(d.entity_id, d.candidate_id, d.dataset_version)] = d
        return [
            MatchLink(
                d.entity_id, d.candidate_id, d.dataset, d.dataset_version, d.decision_id, d.basis
            )
            for d in latest.values()
            if d.outcome == "confirmed"
        ]

    def record(
        self,
        candidate_set: CandidateSet,
        *,
        case_id: str,
        candidate_id: str,
        outcome: str,
        reviewed_by: str,
        reviewer_role: str,
        rationale: str,
        decided_at: str,
        idempotency_key: str,
        current_version: str,
        evidence_ids: Iterable[str] = (),
        case_evidence_ids: Iterable[str] = (),
    ) -> Decision:
        """Record an officer decision and return it (or the earlier one on an idempotent replay).

        ``current_version`` is the dataset version yente serves now (``dataset_version``). A
        candidate retrieved for another version must be matched again before it is reviewed.
        ``case_evidence_ids`` are the evidence records of this case the reviewer may read; cited
        ``evidence_ids`` must be among them.
        """
        evidence = tuple(sorted(set(evidence_ids)))
        request = {
            "case_id": case_id,
            "entity_id": candidate_set.query_entity_id,
            "candidate_id": candidate_id,
            "dataset": candidate_set.dataset,
            "dataset_version": candidate_set.dataset_version,
            "outcome": outcome,
            "reviewed_by": reviewed_by,
            "reviewer_role": reviewer_role,
            "rationale": rationale,
            "evidence_ids": list(evidence),
        }
        digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode("utf-8")).hexdigest()
        for d in self.decisions:
            if d.idempotency_key == idempotency_key:
                if d.digest == digest:
                    return d
                raise ReviewError(
                    "idempotency_conflict",
                    "This idempotency key was used for a different decision",
                    reason="key_reused",
                )

        if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 128:
            raise _invalid("idempotency_key must be 8 to 128 characters", "invalid")
        if reviewer_role not in REVIEWER_ROLES:
            raise ReviewError(
                "forbidden",
                "Only an auditor, specialist or supervisor may review match candidates",
                reason="reviewer_role",
            )
        if not _UUID.match(reviewed_by or ""):
            raise _invalid("reviewed_by must be an officer UUID", "invalid")
        if outcome not in OUTCOMES:
            raise _invalid(f"outcome must be one of {list(OUTCOMES)}", "invalid")
        if not isinstance(rationale, str) or not rationale.strip():
            raise _invalid("A rationale is required for every review decision", "missing_rationale")
        if candidate_set.dataset_version != current_version:
            raise ReviewError(
                "version_conflict",
                f"Candidates were retrieved for dataset version {candidate_set.dataset_version}; "
                f"yente now serves {current_version}. Match again before reviewing.",
                reason="dataset_version_changed",
            )
        candidate = candidate_set.get(candidate_id)
        if candidate is None:
            raise ReviewError("not_found", f"No candidate {candidate_id} for this entity")
        allowed_evidence = set(case_evidence_ids)
        unknown = [e for e in evidence if e not in allowed_evidence]
        if unknown:
            raise _invalid(
                "Cited evidence is not evidence of this case you may read: " + ", ".join(unknown),
                "unknown_evidence",
            )

        basis: tuple[str, ...] = ()
        if outcome == "confirmed":
            basis = self._confirmation_basis(candidate_set, candidate, evidence)

        decision = Decision(
            decision_id=str(uuid.uuid5(_NAMESPACE, f"{self.tenant_id}|{idempotency_key}")),
            tenant_id=self.tenant_id,
            case_id=case_id,
            entity_id=candidate_set.query_entity_id,
            candidate_id=candidate_id,
            dataset=candidate_set.dataset,
            dataset_version=candidate_set.dataset_version,
            outcome=outcome,
            reviewed_by=reviewed_by,
            reviewer_role=reviewer_role,
            rationale=rationale.strip(),
            evidence_ids=evidence,
            basis=basis,
            score_at_review=candidate.score,
            decided_at=decided_at,
            idempotency_key=idempotency_key,
            digest=digest,
        )
        self.decisions.append(decision)
        return decision

    def _confirmation_basis(
        self, candidate_set: CandidateSet, candidate: Candidate, evidence: tuple[str, ...]
    ) -> tuple[str, ...]:
        conflicts = candidate.identifier_conflicts()
        if conflicts:
            raise _invalid(
                "Identifiers disagree with this candidate (" + ", ".join(conflicts) + "); "
                "it cannot be confirmed",
                "identifier_conflict",
            )
        agreements = candidate.identifier_agreements()
        if not agreements and not evidence:
            raise _invalid(
                "A score is not identity: confirm only with an agreeing identifier or cited "
                "case evidence",
                "score_only",
            )
        if candidate_set.ambiguous and not evidence:
            # Identifiers must single this candidate out among the others above the threshold.
            others = [
                c
                for c in candidate_set.candidates
                if c.above_threshold and c.candidate_id != candidate.candidate_id
            ]
            shared = [a for a in agreements if any(a in o.identifier_agreements() for o in others)]
            if shared == agreements:
                raise _invalid(
                    "Several candidates are above the threshold and nothing distinguishes this "
                    "one; cite case evidence",
                    "ambiguous_candidates",
                )
        for (entity_id, other_id, version), d in self._latest().items():
            if (
                entity_id == candidate_set.query_entity_id
                and version == candidate_set.dataset_version
                and other_id != candidate.candidate_id
                and d.outcome == "confirmed"
            ):
                raise _invalid(
                    f"Candidate {other_id} is already confirmed for this entity; reject it first",
                    "already_confirmed",
                )
        return tuple(agreements) + (("evidence",) if evidence else ())

    def _latest(self) -> dict[tuple[str, str, str], Decision]:
        latest: dict[tuple[str, str, str], Decision] = {}
        for d in self.decisions:
            latest[(d.entity_id, d.candidate_id, d.dataset_version)] = d
        return latest


def _invalid(message: str, reason: str) -> ReviewError:
    return ReviewError("invalid_domain_input", message, reason=reason)


def http_transport(base_url: str, timeout_s: float = 15.0) -> Transport:
    """Transport for a real yente. yente has no authentication, so it must only be reachable on
    a private network or loopback (integration/yente/README.md)."""
    if not base_url.startswith(
        ("http://127.0.0.1", "http://localhost", "https://", "http://yente")
    ):
        raise ValueError("YENTE_BASE_URL must be loopback, the private yente host or https")

    def call(method: str, path: str, body: Any = None) -> Response:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            base_url.rstrip("/") + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as resp:
                return Response(resp.status, resp.read())
        except urllib.error.HTTPError as exc:
            return Response(exc.code, exc.read())

    return call
