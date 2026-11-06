"""Pure input rules for case findings and officer finding feedback (TAX-086).

No Frappe import, so every rule is testable without a bench. ``engine`` runs
them; ``store`` binds the engine to the ``Vati Case Finding`` and
``Vati Finding Feedback`` DocTypes.

- ``FindingInput.build`` validates one finding against the ``Finding`` contract
  (integration/api/openapi.json): kind, category, explanation, evidence
  references, unresolved amount (decimal string and ISO currency), confidence,
  alternative explanations, legal route, proposed action and producer. A
  ``jube_rule`` finding names its rule and ruleset versions, a ``langgraph_run``
  finding its playbook registry stamp and run and must carry a valid
  ``AnalysisProvenance``; rule and officer findings carry none.
- Finding text never labels a person: the ADR-026 labelling words (``FORBIDDEN_TERMS``,
  the same set as integration/followthemoney/scheme_graph.py) are refused.
- ``from_rule_signal`` turns one TAX-035 rule result (integration/jube/tax_risk.py)
  into a finding input; ``from_playbook_result`` turns the TAX-049 related-party
  review or proposal (ai/tools/related_party.py, ai/workflows/related_party_playbook.py)
  into one finding per observation, evidence gap and discrepancy.
- ``FeedbackInput.build`` validates ``FindingFeedbackRequest``. The actor is never
  read from the body.

A legal route is recorded ``unverified`` or ``no_verified_route``: a rule, an agent
or an officer recording a finding cannot verify one. Proposed actions always
require human approval.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from vati_tax.ids.canonical import (
    CaseNotFound,
    InvalidMapping,
    MappingConflict,
    canonical_uuid,
    validate_idempotency_key,
)

# --- contract enums (integration/api/openapi.json components.schemas) ---------------

KINDS: Final = (
    "observed_fact",
    "inference",
    "missing_evidence",
    "unverified_evidence",
    "contradictory_evidence",
)
CATEGORIES: Final = (
    "refund_reconciliation",
    "related_party",
    "worker_classification",
    "imported_services",
    "policy_gap",
    "other",
)
CONFIDENCE: Final = ("low", "medium", "high")
PRODUCERS: Final = ("jube_rule", "langgraph_run", "officer")
# FindingSnapshot.producer: officers do not give feedback on officer findings.
REVIEWABLE_PRODUCERS: Final = ("jube_rule", "langgraph_run")
ACTIONS: Final = (
    "none",
    "human_clearance",
    "request_evidence",
    "specialist_review",
    "policy_gap_review",
)
# A producer cannot verify a legal route; ``verified_by_reviewer`` is a later review.
RECORDABLE_ROUTES: Final = ("unverified", "no_verified_route")
VERDICTS: Final = ("accepted", "modified", "rejected")
REASON_CODES: Final = (
    "evidence_supports",
    "evidence_does_not_support",
    "citation_incorrect",
    "missing_context",
    "legitimate_explanation",
    "wrong_category",
    "wording_unclear",
    "other",
)
PROVIDERS: Final = ("mock", "groq", "gemini", "ollama", "template")
MODEL_ROLES: Final = ("reasoning", "fast", "document")
LEVEL_NAMES: Final = ("mock", "hosted", "local", "template")
VALIDATION_STATUSES: Final = ("passed", "failed", "not_run")

MAX_EXPLANATION = 4000
MAX_ALTERNATIVE = 1000
MAX_ALTERNATIVES = 20
MAX_EVIDENCE_REFS = 100
MIN_REASON, MAX_REASON = 10, 2000
MAX_VERSION = 128

# ADR-026 (TAX-074): words that label a person or entity instead of describing a
# recorded fact. Kept equal to integration/followthemoney/scheme_graph.FORBIDDEN_TERMS
# by tests/integration/test_case_findings.py (the app cannot import integration/).
FORBIDDEN_TERMS: Final = frozenset(
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
_WORD = re.compile(r"[a-z]+")
_AMOUNT = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]{1,4})?$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CODE = re.compile(r"^[a-z][a-z0-9_]*$")
_PROMPT_VERSION = re.compile(r"^[A-Za-z0-9._:@/-]+$")
_IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")


# --- errors -------------------------------------------------------------------------


class InvalidFinding(MappingConflict):
    """422 ``invalid_domain_input``: the finding or feedback breaks a contract rule."""


class FindingNotFound(CaseNotFound):
    """The finding is missing or outside the caller's tenant or case (not told apart)."""


def _fail(message: str, field: str, reason: str) -> InvalidFinding:
    return InvalidFinding(message, field, reason)


# --- helpers ------------------------------------------------------------------------


def sha256_text(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def digest(payload: Any) -> str:
    """Stable SHA-256 of a normalised request, for idempotent replay checks."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def operation_key(operation: str, tenant_id: str, *parts: str) -> str:
    return sha256_text(operation, tenant_id, *parts)


def labelling_terms(text: str) -> list[str]:
    """Labelling words in ``text`` (whole words; ``red_flag`` and ``high-risk`` caught)."""
    words = _WORD.findall(text.lower())
    joined = {a + b for a, b in zip(words, words[1:], strict=False)}
    return sorted((set(words) | joined) & FORBIDDEN_TERMS)


def _neutral(text: str, field: str) -> str:
    if labelling_terms(text):
        raise _fail(
            f"{field} labels a person or entity; describe the recorded fact or gap instead.",
            field,
            "risk_label_not_allowed",
        )
    return text


def _text(value: object, field: str, *, maximum: int, minimum: int = 1) -> str:
    if not isinstance(value, str):
        raise _fail(f"{field} must be text.", field, "invalid_type")
    text = value.strip()
    if len(text) < minimum:
        raise _fail(f"{field} needs at least {minimum} characters.", field, "too_short")
    if len(text) > maximum:
        raise _fail(f"{field} is longer than {maximum} characters.", field, "too_long")
    return text


def _choice(value: object, allowed: Sequence[str], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise _fail(f"{field} must be one of {', '.join(allowed)}.", field, "invalid_choice")
    return value


def _ident(value: object, field: str) -> str:
    if not isinstance(value, str) or not _IDENT.fullmatch(value):
        raise _fail(f"{field} must be a version identifier.", field, "invalid_identifier")
    return value


def _closed(value: object, field: str, allowed: set[str], required: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(f"{field} must be an object.", field, "invalid_type")
    extra = sorted(set(value) - allowed)
    if extra:
        raise _fail(
            f"{field}.{extra[0]} is not accepted.", f"{field}.{extra[0]}", "unexpected_field"
        )
    missing = sorted(required - set(value))
    if missing:
        raise _fail(f"{field}.{missing[0]} is required.", f"{field}.{missing[0]}", "required")
    return value


def _int(value: object, field: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(f"{field} must be an integer.", field, "invalid_type")
    if value < minimum or (maximum is not None and value > maximum):
        raise _fail(f"{field} is out of range.", field, "out_of_range")
    return value


def _optional_int(
    value: object, field: str, *, minimum: int = 0, maximum: int | None = None
) -> int | None:
    return None if value is None else _int(value, field, minimum=minimum, maximum=maximum)


def _optional_text(value: object, field: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise _fail(f"{field} must be null or 1-{maximum} characters.", field, "invalid")
    return value


def _code(value: object, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) > 64 or not _CODE.fullmatch(value):
        raise _fail(f"{field} must be a lower-case code.", field, "invalid_code")
    return value


def money(value: object, field: str = "unresolved_amount") -> dict[str, str] | None:
    """Contract ``Money`` (decimal string, never a float) or ``None``."""
    if value is None:
        return None
    raw = _closed(value, field, {"amount", "currency"}, {"amount", "currency"})
    amount, currency = raw["amount"], raw["currency"]
    if not isinstance(amount, str) or not _AMOUNT.fullmatch(amount):
        raise _fail(
            f"{field}.amount must be a decimal string.", f"{field}.amount", "invalid_amount"
        )
    try:
        Decimal(amount)
    except InvalidOperation:  # pragma: no cover - the pattern already guarantees it
        raise _fail("Invalid amount.", f"{field}.amount", "invalid_amount") from None
    if not isinstance(currency, str) or not _CURRENCY.fullmatch(currency):
        raise _fail(
            f"{field}.currency must be an ISO code.", f"{field}.currency", "invalid_currency"
        )
    return {"amount": amount, "currency": currency}


# --- analysis provenance (AnalysisProvenance, TAX-096) -------------------------------

_PROVENANCE_KEYS = {
    "provider",
    "model_id",
    "configured_model_id",
    "role",
    "task",
    "prompt_version",
    "settings",
    "fallback_level",
    "fallback_level_name",
    "fell_back",
    "attempts",
    "latency_ms",
    "token_usage",
    "validation",
    "router_version",
}
_ATTEMPT_REQUIRED = {"level", "name", "provider", "model_id", "outcome"}
_ATTEMPT_KEYS = _ATTEMPT_REQUIRED | {"http_status", "latency_ms"}


def validate_provenance(value: object, field: str = "analysis_provenance") -> dict[str, Any]:
    """A copy of ``value`` if it satisfies ``AnalysisProvenance``; else ``InvalidFinding``.

    Mirrors the contract schema exactly (closed objects, enums, patterns, scalar
    settings with no floats, 1-4 attempts). tests/integration/test_case_findings.py
    checks it agrees with integration/api/contract.py on valid and broken records.
    """
    raw = _closed(value, field, _PROVENANCE_KEYS, _PROVENANCE_KEYS)
    _choice(raw["provider"], PROVIDERS, f"{field}.provider")
    _text(raw["model_id"], f"{field}.model_id", maximum=128)
    if raw["model_id"] != raw["model_id"].strip():
        raise _fail("model_id has surrounding spaces.", f"{field}.model_id", "invalid")
    _optional_text(raw["configured_model_id"], f"{field}.configured_model_id", maximum=128)
    _choice(raw["role"], MODEL_ROLES, f"{field}.role")
    _code(raw["task"], f"{field}.task")
    prompt = raw["prompt_version"]
    if (
        not isinstance(prompt, str)
        or not 1 <= len(prompt) <= 128
        or not _PROMPT_VERSION.fullmatch(prompt)
    ):
        raise _fail("prompt_version is not a version.", f"{field}.prompt_version", "invalid")
    settings = raw["settings"]
    if not isinstance(settings, Mapping):
        raise _fail("settings must be an object.", f"{field}.settings", "invalid_type")
    for name, setting in settings.items():
        where = f"{field}.settings.{name}"
        if setting is None or isinstance(setting, bool):
            continue
        if isinstance(setting, int):
            continue
        if isinstance(setting, str) and len(setting) <= 128:
            continue
        raise _fail("Settings are strings, integers, booleans or null.", where, "invalid_setting")
    _int(raw["fallback_level"], f"{field}.fallback_level", maximum=3)
    _choice(raw["fallback_level_name"], LEVEL_NAMES, f"{field}.fallback_level_name")
    if not isinstance(raw["fell_back"], bool):
        raise _fail("fell_back must be a boolean.", f"{field}.fell_back", "invalid_type")
    attempts = raw["attempts"]
    if not isinstance(attempts, list) or not 1 <= len(attempts) <= 4:
        raise _fail("attempts lists 1-4 levels.", f"{field}.attempts", "invalid")
    for index, attempt in enumerate(attempts):
        where = f"{field}.attempts[{index}]"
        item = _closed(attempt, where, _ATTEMPT_KEYS, _ATTEMPT_REQUIRED)
        _int(item["level"], f"{where}.level", maximum=3)
        _choice(item["name"], LEVEL_NAMES, f"{where}.name")
        if item["provider"] is not None:
            _choice(item["provider"], PROVIDERS, f"{where}.provider")
        _optional_text(item["model_id"], f"{where}.model_id", maximum=128)
        _code(item["outcome"], f"{where}.outcome")
        if "http_status" in item:
            _optional_int(item["http_status"], f"{where}.http_status", minimum=100, maximum=599)
        if "latency_ms" in item:
            _optional_int(item["latency_ms"], f"{where}.latency_ms")
    _int(raw["latency_ms"], f"{field}.latency_ms")
    tokens = _closed(
        raw["token_usage"], f"{field}.token_usage", {"input", "output", "total"},
        {"input", "output", "total"},
    )  # fmt: skip
    for name in ("input", "output", "total"):
        _optional_int(tokens[name], f"{field}.token_usage.{name}")
    validation = _closed(
        raw["validation"], f"{field}.validation", {"status", "reason", "rejected_statements"},
        {"status", "reason"},
    )  # fmt: skip
    _choice(validation["status"], VALIDATION_STATUSES, f"{field}.validation.status")
    _code(validation["reason"], f"{field}.validation.reason", nullable=True)
    if "rejected_statements" in validation:
        _int(validation["rejected_statements"], f"{field}.validation.rejected_statements")
    _optional_text(raw["router_version"], f"{field}.router_version", maximum=64)
    result: dict[str, Any] = json.loads(json.dumps(raw))
    return result


# --- findings -----------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceCitation:
    evidence_id: str
    page: int | None
    record: str | None
    document_sha256: str | None  # None: the store fills it from the evidence record


def _citations(value: object) -> tuple[EvidenceCitation, ...]:
    if not isinstance(value, list):
        raise _fail("evidence_refs must be a list.", "evidence_refs", "invalid_type")
    if len(value) > MAX_EVIDENCE_REFS:
        raise _fail("Too many evidence references.", "evidence_refs", "too_many")
    out: list[EvidenceCitation] = []
    seen: set[tuple[str, int | None, str | None]] = set()
    for index, item in enumerate(value):
        where = f"evidence_refs[{index}]"
        raw = _closed(
            item, where, {"evidence_id", "document_sha256", "locator"}, {"evidence_id", "locator"}
        )
        evidence_id = canonical_uuid(raw["evidence_id"], f"{where}.evidence_id")
        sha = raw.get("document_sha256")
        if sha is not None and (not isinstance(sha, str) or not _SHA256.fullmatch(sha)):
            raise _fail(
                "document_sha256 must be a SHA-256 hex digest.",
                f"{where}.document_sha256",
                "invalid",
            )
        locator = _closed(
            raw["locator"], f"{where}.locator", {"page", "record"}, {"page", "record"}
        )
        page = _optional_int(locator["page"], f"{where}.locator.page", minimum=1)
        record = _optional_text(locator["record"], f"{where}.locator.record", maximum=255)
        key = (evidence_id, page, record)
        if key in seen:
            raise _fail("Duplicate evidence reference.", where, "duplicate")
        seen.add(key)
        out.append(EvidenceCitation(evidence_id, page, record, sha))
    return tuple(out)


@dataclass(frozen=True)
class FindingInput:
    """A validated ``Finding`` body (contract fields plus Vati provenance fields)."""

    kind: str
    category: str
    code: str | None
    explanation: str
    evidence: tuple[EvidenceCitation, ...]
    unresolved_amount: dict[str, str] | None
    confidence: str
    alternative_explanations: tuple[str, ...]
    legal_route_status: str
    proposed_action: str
    producer: str
    producer_version: str
    subject: str | None
    rule_id: str | None = None
    rule_version: str | None = None
    ruleset_id: str | None = None
    ruleset_version: str | None = None
    input_digest: str | None = None
    playbook_id: str | None = None
    playbook_version: str | None = None
    playbook_content_digest: str | None = None
    registry_version: str | None = None
    run_id: str | None = None
    analysis_provenance: dict[str, Any] | None = None

    _BODY = {
        "kind", "category", "code", "explanation", "evidence_refs", "unresolved_amount",
        "confidence", "alternative_explanations", "legal_route", "proposed_action",
        "producer", "producer_version", "subject", "rule", "playbook", "analysis_provenance",
    }  # fmt: skip

    @classmethod
    def build(cls, body: object) -> FindingInput:
        if not isinstance(body, Mapping):
            raise _fail("The finding must be an object.", "body", "invalid_type")
        for key in sorted(set(body) - cls._BODY):
            words = set(_WORD.findall(str(key).lower()))
            reason = "risk_label_not_allowed" if words & FORBIDDEN_TERMS else "unexpected_field"
            raise _fail(f"{key} is not accepted.", str(key), reason)
        for key in (
            "kind",
            "category",
            "explanation",
            "confidence",
            "producer",
            "producer_version",
        ):
            if key not in body:
                raise _fail(f"{key} is required.", key, "required")
        producer = _choice(body["producer"], PRODUCERS, "producer")
        explanation = _neutral(
            _text(body["explanation"], "explanation", maximum=MAX_EXPLANATION), "explanation"
        )
        alternatives_raw = body.get("alternative_explanations", [])
        if not isinstance(alternatives_raw, list) or len(alternatives_raw) > MAX_ALTERNATIVES:
            raise _fail(
                "alternative_explanations must be a short list.",
                "alternative_explanations",
                "invalid",
            )
        alternatives = tuple(
            _neutral(_text(a, f"alternative_explanations[{i}]", maximum=MAX_ALTERNATIVE),
                     f"alternative_explanations[{i}]")
            for i, a in enumerate(alternatives_raw)
        )  # fmt: skip
        route = body.get(
            "legal_route", {"status": "unverified", "citation": None, "verified_by": None}
        )
        route = _closed(route, "legal_route", {"status", "citation", "verified_by"}, {"status"})
        route_status = _choice(route["status"], RECORDABLE_ROUTES, "legal_route.status")
        if route.get("citation") is not None or route.get("verified_by") is not None:
            raise _fail(
                "Only a qualified reviewer verifies a legal route; record it unverified.",
                "legal_route",
                "route_not_verifiable_here",
            )
        action = body.get("proposed_action", {"type": "none", "requires_human_approval": True})
        action = _closed(action, "proposed_action", {"type", "requires_human_approval"}, {"type"})
        if action.get("requires_human_approval", True) is not True:
            raise _fail(
                "Every proposed action needs human approval.",
                "proposed_action",
                "human_approval_required",
            )
        code = body.get("code")
        subject = body.get("subject")
        if subject is not None:
            subject = _text(subject, "subject", maximum=255)
        values: dict[str, Any] = {
            "kind": _choice(body["kind"], KINDS, "kind"),
            "category": _choice(body["category"], CATEGORIES, "category"),
            "code": None if code is None else _code(code, "code"),
            "explanation": explanation,
            "evidence": _citations(body.get("evidence_refs", [])),
            "unresolved_amount": money(body.get("unresolved_amount")),
            "confidence": _choice(body["confidence"], CONFIDENCE, "confidence"),
            "alternative_explanations": alternatives,
            "legal_route_status": route_status,
            "proposed_action": _choice(action["type"], ACTIONS, "proposed_action.type"),
            "producer": producer,
            "producer_version": _ident(body["producer_version"], "producer_version"),
            "subject": subject,
        }
        provenance = body.get("analysis_provenance")
        if producer == "jube_rule":
            rule = _closed(
                body.get("rule"), "rule", {"rule_id", "rule_version", "ruleset_id", "ruleset_version", "input_digest"},
                {"rule_id", "rule_version", "ruleset_id", "ruleset_version"},
            )  # fmt: skip
            values.update(
                rule_id=_ident(rule["rule_id"], "rule.rule_id"),
                rule_version=_ident(rule["rule_version"], "rule.rule_version"),
                ruleset_id=_ident(rule["ruleset_id"], "rule.ruleset_id"),
                ruleset_version=_ident(rule["ruleset_version"], "rule.ruleset_version"),
                input_digest=_ident(rule.get("input_digest"), "rule.input_digest")
                if rule.get("input_digest") is not None
                else None,
            )
        elif "rule" in body:
            raise _fail("Only a jube_rule finding names a rule.", "rule", "unexpected_field")
        if producer == "langgraph_run":
            playbook = _closed(
                body.get("playbook"), "playbook",
                {"name", "version", "content_digest", "registry_version", "run_id"},
                {"name", "version", "content_digest", "registry_version", "run_id"},
            )  # fmt: skip
            sha = playbook["content_digest"]
            if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
                raise _fail(
                    "content_digest must be a SHA-256 hex digest.",
                    "playbook.content_digest",
                    "invalid",
                )
            if provenance is None:
                raise _fail(
                    "An agent finding records how it was produced (AnalysisProvenance).",
                    "analysis_provenance",
                    "provenance_required",
                )
            values.update(
                playbook_id=_ident(playbook["name"], "playbook.name"),
                playbook_version=_ident(playbook["version"], "playbook.version"),
                playbook_content_digest=sha,
                registry_version=_ident(playbook["registry_version"], "playbook.registry_version"),
                run_id=_ident(playbook["run_id"], "playbook.run_id"),
                analysis_provenance=validate_provenance(provenance),
            )
        elif "playbook" in body:
            raise _fail(
                "Only a langgraph_run finding names a playbook.", "playbook", "unexpected_field"
            )
        elif provenance is not None:
            raise _fail(
                "Rule and officer findings have no analysis provenance.",
                "analysis_provenance",
                "provenance_not_allowed",
            )
        return cls(**values)

    def lineage_key(self, tenant_id: str, case_id: str, fallback: str) -> str:
        """Revisions of one finding share a lineage: same producer component and subject."""
        component = self.rule_id or self.playbook_id or "officer"
        return operation_key("finding.lineage", tenant_id, case_id, self.producer, component,
                             self.subject or fallback)  # fmt: skip

    def digest(self) -> str:
        return digest(
            {
                "kind": self.kind,
                "category": self.category,
                "code": self.code,
                "explanation": self.explanation,
                "evidence": [[c.evidence_id, c.page, c.record] for c in self.evidence],
                "unresolved_amount": self.unresolved_amount,
                "confidence": self.confidence,
                "alternative_explanations": list(self.alternative_explanations),
                "legal_route_status": self.legal_route_status,
                "proposed_action": self.proposed_action,
                "producer": self.producer,
                "producer_version": self.producer_version,
                "subject": self.subject,
                "rule": [self.rule_id, self.rule_version, self.ruleset_id, self.ruleset_version, self.input_digest],
                "playbook": [self.playbook_id, self.playbook_version, self.playbook_content_digest,
                             self.registry_version, self.run_id],
                "analysis_provenance": self.analysis_provenance,
            }
        )  # fmt: skip


def finding_key(tenant_id: str, case_id: str, idempotency_key: object) -> str:
    """Per tenant and case: a derived rule or playbook key repeats across cases."""
    return operation_key(
        "finding.record", tenant_id, case_id, validate_idempotency_key(idempotency_key)
    )


def revision_key(lineage_key: str, revision: int) -> str:
    """Unique per lineage and revision: two writers cannot both take revision n."""
    return sha256_text("finding.revision", lineage_key, str(revision))


# --- translators: TAX-035 rule signals ----------------------------------------------

# Rule family -> (category, kind, proposed action) for a ``signal`` outcome. Unknown
# families are recorded as ``other`` / ``inference`` and go to officer review.
RULE_FAMILIES: Final[dict[str, tuple[str, str, str]]] = {
    "vat_refund_mismatch": ("refund_reconciliation", "missing_evidence", "request_evidence"),
    "related_party_fees": ("related_party", "observed_fact", "request_evidence"),
    "persistent_losses": ("other", "observed_fact", "request_evidence"),
    "imported_service_gap": ("imported_services", "unverified_evidence", "request_evidence"),
}
# Families whose signal leaves an amount unresolved, and the rule input that holds it.
RULE_AMOUNTS: Final[dict[str, str]] = {"vat_refund_mismatch": "unsupported_input_vat"}


def _resolve_evidence(
    source_ids: object, evidence: Mapping[str, str], field: str
) -> list[dict[str, Any]]:
    """Source record IDs (``INV-SYN-002-01``) -> evidence references with that locator.

    ``evidence`` maps each source record to its ``Vati Evidence`` ID. An unmapped
    citation is refused, never dropped, so a finding cannot lose its support.
    """
    if not isinstance(source_ids, list):
        raise _fail(f"{field} must be a list.", field, "invalid_type")
    refs = []
    for source in dict.fromkeys(str(s) for s in source_ids):
        evidence_id = evidence.get(source)
        if evidence_id is None:
            raise _fail(f"{source} has no Vati evidence record.", field, "evidence_unmapped")
        refs.append({"evidence_id": evidence_id, "locator": {"page": None, "record": source}})
    return refs


def from_rule_signal(
    signal: Mapping[str, Any], *, evidence: Mapping[str, str], currency: str = "LSL"
) -> tuple[dict[str, Any], str] | None:
    """One TAX-035 ``evaluate_record`` row as a finding body and its idempotency key.

    ``no_signal`` and ``not_applicable`` rows record nothing (``None``). ``cleared`` rows record the
    arithmetic match as an observed fact for human clearance. ``currency`` is the
    ruleset currency (``tax_risk_rules.v1.json``: LSL). The key is derived
    from the rule, versions, case and input digest, so re-evaluating the same
    inputs is a replay and changed inputs are a new revision.
    """
    outcome = signal.get("outcome")
    if outcome in ("no_signal", "not_applicable"):
        return None
    if outcome not in ("signal", "cleared"):
        raise _fail("Unknown rule outcome.", "outcome", "invalid_choice")
    family = str(signal.get("family"))
    category, kind, action = RULE_FAMILIES.get(family, ("other", "inference", "none"))
    if outcome == "cleared":
        kind, action = "observed_fact", "human_clearance"
    amount = None
    if outcome == "signal" and family in RULE_AMOUNTS:
        inputs = signal.get("inputs")
        value = inputs.get(RULE_AMOUNTS[family]) if isinstance(inputs, Mapping) else None
        amount = {"amount": value, "currency": currency}
    rule = {
        "rule_id": signal.get("rule_id"),
        "rule_version": signal.get("rule_version"),
        "ruleset_id": signal.get("ruleset_id"),
        "ruleset_version": signal.get("ruleset_version"),
        "input_digest": signal.get("input_digest"),
    }
    body = {
        "kind": kind,
        "category": category,
        "code": signal.get("code"),
        "explanation": signal.get("explanation"),
        "evidence_refs": _resolve_evidence(signal.get("evidence_ids"), evidence, "evidence_ids"),
        "unresolved_amount": amount,
        "confidence": "high",  # deterministic arithmetic on the cited records
        "alternative_explanations": [],
        "legal_route": {"status": "unverified", "citation": None, "verified_by": None},
        "proposed_action": {"type": action, "requires_human_approval": True},
        "producer": "jube_rule",
        "producer_version": f"{rule['ruleset_id']}@{rule['ruleset_version']}",
        "subject": str(rule["rule_id"]),
        "rule": rule,
    }
    key_parts = [str(rule[k]) for k in ("ruleset_id", "ruleset_version", "rule_id", "rule_version")]
    key = "rule:" + sha256_text(*key_parts, str(outcome), str(signal.get("input_digest")))
    return body, key


# --- translators: TAX-049 related-party playbook ------------------------------------

# Neutral explanations for TAX-049 result codes. They describe the records and the
# gap; none presumes intent, pricing or treatment (ai/tools/related_party.py).
PLAYBOOK_TEXT: Final[dict[str, str]] = {
    "related_party_transaction": "Purchases were made under a contract the taxpayer marked "
    "related-party. Pricing, benefit and intent are not established by this record.",
    "consecutive_loss_periods": "The return history shows {periods} consecutive loss periods "
    "ending in the period under review. Losses can have ordinary business reasons.",
    "possible_service_duplication": "Two related-party contracts appear to cover the same "
    "service category; the records do not show whether the services differ.",
    "service_delivery_evidence_missing": "No record showing delivery of the related-party "
    "services was supplied.",
    "service_benefit_evidence_missing": "No record showing how the business used the "
    "related-party services was supplied.",
    "loss_explanation_not_documented": "No documented explanation for the loss periods was "
    "supplied.",
    "relationship_evidence_missing": "No record documenting the stated relationship was supplied.",
    "payment_trail_missing": "No payment record was supplied for the related-party invoices.",
    "payment_amount_differs": "The recorded payment differs from the invoiced amount.",
    "cited_evidence_not_supplied": "A cited supporting document was not supplied with the case.",
}
_PLAYBOOK_BUCKETS: Final = (
    ("observations", "observed_fact", "high"),
    ("evidence_gaps", "missing_evidence", "high"),
    ("discrepancies", "contradictory_evidence", "medium"),
)
_ALTERNATIVE_TEXT: Final[dict[str, str]] = {
    "documented_alternative_explanation": "The taxpayer documented an alternative explanation "
    "({explanation}).",
    "payment_trail_consistent": "The payment trail matches the related-party invoice.",
    "no_duplication_observed": "No duplication with other contracts was observed.",
}


def _playbook_text(entry: Mapping[str, Any], table: Mapping[str, str]) -> str:
    code = str(entry.get("code"))
    template = table.get(code)
    if template is None:
        return f"The related-party review recorded '{code}' for the cited records."
    try:
        return template.format(**{k: v for k, v in entry.items() if isinstance(v, str | int)})
    except (KeyError, IndexError):
        return template.split(" {")[0]


def from_playbook_result(
    result: Mapping[str, Any],
    *,
    stamp: Mapping[str, Any],
    run_id: str,
    evidence: Mapping[str, str],
    analysis_provenance: Mapping[str, Any] | None = None,
) -> list[tuple[dict[str, Any], str]]:
    """TAX-049 related-party review/proposal -> one finding body and key per result item.

    ``stamp`` is the TAX-061 registry stamp of the playbook that ran (``kind``
    ``playbook``, ``name``, ``version``, ``content_digest``, ``registry_version``).
    ``analysis_provenance`` defaults to the proposal's ``interpretation`` provenance
    (TAX-097); an agent finding without one is refused. Counter-evidence becomes the
    alternative explanations of every finding of the result. Prohibited conclusions
    (``legal_conclusion``, ``enforcement_conclusion``, ``decision``) must be empty.
    """
    for field in ("legal_conclusion", "enforcement_conclusion", "decision"):
        if result.get(field) is not None:
            raise _fail(
                "A playbook result cannot carry a conclusion.", field, "conclusion_not_allowed"
            )
    if stamp.get("kind") != "playbook":
        raise _fail("The stamp must be a playbook registry stamp.", "stamp.kind", "invalid_choice")
    if analysis_provenance is None:
        interpretation = result.get("interpretation")
        if isinstance(interpretation, Mapping):
            analysis_provenance = interpretation.get("analysis_provenance")
    counter = result.get("counter_evidence") or []
    if not isinstance(counter, list):
        raise _fail("counter_evidence must be a list.", "counter_evidence", "invalid_type")
    alternatives = [_playbook_text(c, _ALTERNATIVE_TEXT) for c in counter if isinstance(c, Mapping)]
    next_step = result.get("proposed_next_step")
    playbook = {
        "name": stamp.get("name"),
        "version": stamp.get("version"),
        "content_digest": stamp.get("content_digest"),
        "registry_version": stamp.get("registry_version"),
        "run_id": run_id,
    }
    out = []
    for bucket, kind, confidence in _PLAYBOOK_BUCKETS:
        entries = result.get(bucket) or []
        if not isinstance(entries, list):
            raise _fail(f"{bucket} must be a list.", bucket, "invalid_type")
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise _fail(f"{bucket} entries must be objects.", bucket, "invalid_type")
            code = str(entry.get("code"))
            cited = entry.get("evidence_ids")
            refs = _resolve_evidence(cited, evidence, f"{bucket}.evidence_ids")
            if kind == "observed_fact":
                action = "request_evidence" if next_step == "targeted_evidence_request" else "none"
            else:
                action = "request_evidence"
            subject = f"{bucket}:{code}:{refs[0]['locator']['record'] if refs else '-'}"
            body = {
                "kind": kind,
                "category": "related_party",
                "code": code,
                "explanation": _playbook_text(entry, PLAYBOOK_TEXT),
                "evidence_refs": refs,
                "unresolved_amount": None,
                "confidence": confidence,
                "alternative_explanations": alternatives,
                "legal_route": {"status": "unverified", "citation": None, "verified_by": None},
                "proposed_action": {"type": action, "requires_human_approval": True},
                "producer": "langgraph_run",
                "producer_version": f"{playbook['name']}@{playbook['version']}",
                "subject": subject,
                "playbook": playbook,
                "analysis_provenance": analysis_provenance,
            }
            key = "playbook:" + sha256_text(
                str(playbook["name"]), str(playbook["version"]), str(run_id), subject
            )
            out.append((body, key))
    return out


# --- feedback -----------------------------------------------------------------------


@dataclass(frozen=True)
class FeedbackInput:
    """A validated ``FindingFeedbackRequest``. The actor comes from the session."""

    verdict: str
    reason_code: str
    reason: str | None
    modified_explanation: str | None

    @classmethod
    def build(cls, body: object) -> FeedbackInput:
        if not isinstance(body, Mapping):
            raise _fail("The feedback must be an object.", "body", "invalid_type")
        for actor_field in ("recorded_by", "actor", "officer_id", "user", "role"):
            if actor_field in body:
                # 400: the actor is the authenticated session, never the body.
                raise InvalidMapping(
                    "The actor comes from your session; remove it from the body.",
                    actor_field,
                    "actor_not_allowed",
                )
        allowed = {"verdict", "reason_code", "reason", "modified_explanation"}
        extra = sorted(set(body) - allowed)
        if extra:
            raise InvalidMapping(f"{extra[0]} is not accepted.", extra[0], "unexpected_field")
        verdict = _choice(body.get("verdict"), VERDICTS, "verdict")
        reason_code = _choice(body.get("reason_code"), REASON_CODES, "reason_code")
        reason_raw = body.get("reason")
        reason = None
        if reason_raw is not None:
            reason = _text(reason_raw, "reason", minimum=0, maximum=MAX_REASON) or None
        if verdict in ("modified", "rejected") and (reason is None or len(reason) < MIN_REASON):
            raise _fail(
                f"A {verdict} verdict needs a reason of at least {MIN_REASON} characters.",
                "reason",
                "reason_required",
            )
        if reason is not None and len(reason) < MIN_REASON:
            raise _fail(f"reason needs at least {MIN_REASON} characters.", "reason", "too_short")
        modified = body.get("modified_explanation")
        if verdict == "modified":
            if modified is None:
                raise _fail(
                    "A modified verdict gives the corrected wording.",
                    "modified_explanation",
                    "modified_text_required",
                )
            modified = _neutral(
                _text(modified, "modified_explanation", maximum=MAX_EXPLANATION),
                "modified_explanation",
            )
        elif modified is not None:
            raise _fail(
                "Corrected wording is only for a modified verdict.",
                "modified_explanation",
                "modified_text_not_allowed",
            )
        return cls(verdict, reason_code, reason, modified)

    def digest(self, finding_id: str) -> str:
        return digest(
            {
                "finding_id": finding_id,
                "verdict": self.verdict,
                "reason_code": self.reason_code,
                "reason": self.reason,
                "modified_explanation": self.modified_explanation,
            }
        )


def feedback_key(tenant_id: str, user: str, idempotency_key: object) -> str:
    """Per tenant and officer: another officer's key never replays this one's feedback."""
    return operation_key(
        "finding.feedback", tenant_id, user.lower(), validate_idempotency_key(idempotency_key)
    )


def feedback_chain_key(finding_id: str, user: str, previous_feedback_id: str | None) -> str:
    """One successor per earlier feedback of the same officer (no forked history)."""
    return sha256_text(
        "finding.feedback.chain", finding_id, user.lower(), previous_feedback_id or "-"
    )


def component_stamp(snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
    """The TAX-070 provenance entry (ai/workflows/outcome_feedback.py) for a judged finding."""
    if snapshot.get("producer") == "jube_rule" and snapshot.get("rule_id"):
        return {
            "kind": "rule",
            "rule_id": snapshot["rule_id"],
            "ruleset_version": snapshot["ruleset_version"],
        }
    if snapshot.get("producer") == "langgraph_run" and snapshot.get("playbook_id"):
        return {
            "kind": "playbook",
            "name": snapshot["playbook_id"],
            "version": snapshot["playbook_version"],
            "content_digest": snapshot["playbook_content_digest"],
            "registry_version": snapshot["registry_version"],
        }
    return None


def iso(value: datetime.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
