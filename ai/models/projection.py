"""Contract projections of router provenance (TAX-097, TAX-096 contract, ADR-031).

The router keeps a full operational record per call (``RoutedModel`` provenance:
observed model IDs, per-attempt token use, rate-limit headers, notes). The API
contract's ``AnalysisProvenance`` and ``AiDisclosure`` are closed schemas with a
smaller, audit-facing shape. These pure functions project the full record onto
the contract shape without changing the contract:

- ``tokens`` becomes ``token_usage`` (``input``/``output``/``total``, int or null);
- ``rate_limit`` and ``notes`` stay internal (operational logs, ADR-034);
- attempts keep only ``level``, ``name``, ``provider``, ``model_id``, ``outcome``,
  ``http_status`` and ``latency_ms``;
- settings keep scalar values only; a fractional number becomes a decimal string
  (``30.0`` -> ``"30"``, ``0.2`` -> ``"0.2"``);
- reason and outcome codes are reduced to ``[a-z][a-z0-9_]*``.

``template_provenance`` describes output that never reached the router (the
deterministic template used directly, or a scripted test model), so every agent
output carries provenance and a disclosure that agree.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

PROVIDERS = frozenset({"mock", "groq", "gemini", "ollama", "template"})
LEVEL_NAMES = frozenset({"mock", "hosted", "local", "template"})
ROLES = frozenset({"reasoning", "fast", "document"})
DISCLOSURE_SOURCE = {
    "hosted": "hosted_model",
    "local": "local_model",
    "template": "template",
    "mock": "mock_fixture",
}
_ATTEMPT_KEYS = ("level", "name", "provider", "model_id", "outcome", "http_status", "latency_ms")
_CODE_CHARS = re.compile(r"[^a-z0-9_]+")
_PROMPT_VERSION = re.compile(r"[A-Za-z0-9._:@/-]{1,128}")
TEMPLATE_ID = "deterministic-template-v1"


def _code(value: object, default: str) -> str:
    """A ``[a-z][a-z0-9_]*`` code of at most 64 characters."""
    text = _CODE_CHARS.sub("_", str(value or "").lower()).strip("_")
    if not text or not text[0].isalpha():
        text = default
    return text[:64]


def _int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _text(value: object, limit: int = 128) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _scalar(value: object) -> str | int | bool | None:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return format(Decimal(repr(value)).normalize(), "f")
    if isinstance(value, str):
        return value[:128]
    return None


def _settings(raw: object) -> dict[str, str | int | bool | None]:
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, str | int | bool | None] = {}
    for key, value in raw.items():
        if isinstance(value, (Mapping, list, tuple)):
            continue  # nested settings are operational detail, not contract fields
        out[str(key)[:64]] = _scalar(value)
    return out


def _provider(value: object) -> str | None:
    return value if isinstance(value, str) and value in PROVIDERS else None


def _level_name(value: object) -> str:
    return value if isinstance(value, str) and value in LEVEL_NAMES else "template"


def _tokens(raw: object) -> dict[str, int | None]:
    raw = raw if isinstance(raw, Mapping) else {}
    return {key: _int(raw.get(key)) for key in ("input", "output", "total")}


def _attempt(raw: Mapping[str, Any]) -> dict[str, object]:
    level = _int(raw.get("level"))
    out: dict[str, object] = {
        "level": min(level, 3) if level is not None else 2,
        "name": _level_name(raw.get("name")),
        "provider": _provider(raw.get("provider")),
        "model_id": _text(raw.get("model_id")),
        "outcome": _code(raw.get("outcome"), "provider_error"),
    }
    status = _int(raw.get("http_status"))
    if status is not None and 100 <= status <= 599:
        out["http_status"] = status
    elif "http_status" in raw:
        out["http_status"] = None
    if "latency_ms" in raw:
        out["latency_ms"] = _int(raw.get("latency_ms"))
    return {k: out[k] for k in _ATTEMPT_KEYS if k in out}


def to_contract_provenance(
    provenance: Mapping[str, Any], *, rejected_statements: int | None = None
) -> dict[str, object]:
    """Project a router provenance record onto ``#/components/schemas/AnalysisProvenance``."""
    validation_raw = provenance.get("validation")
    validation_raw = validation_raw if isinstance(validation_raw, Mapping) else {}
    status = validation_raw.get("status")
    validation: dict[str, object] = {
        "status": status if status in ("passed", "failed", "not_run") else "not_run",
        "reason": (
            _code(validation_raw.get("reason"), "validation_failed")
            if validation_raw.get("reason")
            else None
        ),
    }
    if rejected_statements is not None:
        validation["rejected_statements"] = max(0, int(rejected_statements))
    attempts = [_attempt(a) for a in provenance.get("attempts") or [] if isinstance(a, Mapping)][
        -4:
    ]
    level_name = _level_name(provenance.get("fallback_level_name"))
    provider = _provider(provenance.get("provider")) or "template"
    model_id = _text(provenance.get("model_id")) or TEMPLATE_ID
    if not attempts:
        attempts = [
            {
                "level": 2,
                "name": "template",
                "provider": "template",
                "model_id": model_id,
                "outcome": "ok",
            }
        ]
    role = provenance.get("role")
    prompt_version = provenance.get("prompt_version")
    level = _int(provenance.get("fallback_level"))
    return {
        "provider": provider,
        "model_id": model_id,
        "configured_model_id": _text(provenance.get("configured_model_id")),
        "role": role if role in ROLES else "reasoning",
        "task": _code(provenance.get("task"), "analysis"),
        "prompt_version": (
            prompt_version
            if isinstance(prompt_version, str) and _PROMPT_VERSION.fullmatch(prompt_version)
            else "unversioned"
        ),
        "settings": _settings(provenance.get("settings")),
        "fallback_level": min(level, 3) if level is not None else 2,
        "fallback_level_name": level_name,
        "fell_back": bool(provenance.get("fell_back")),
        "attempts": attempts,
        "latency_ms": _int(provenance.get("latency_ms")) or 0,
        "token_usage": _tokens(provenance.get("tokens")),
        "validation": validation,
        "router_version": _text(provenance.get("router_version"), 64),
    }


def to_disclosure(provenance: Mapping[str, Any]) -> dict[str, object]:
    """``AiDisclosure`` that agrees with the projected provenance (contract check)."""
    projected = (
        provenance
        if "token_usage" in provenance and "attempts" in provenance
        else to_contract_provenance(provenance)
    )
    return {
        "source": DISCLOSURE_SOURCE[str(projected["fallback_level_name"])],
        "provider": projected["provider"],
        "model_id": projected["model_id"],
        "fell_back": projected["fell_back"],
        "advisory_only": True,
    }


def template_provenance(
    *,
    task: str,
    prompt_version: str,
    role: str = "reasoning",
    model_id: str = TEMPLATE_ID,
    source: str = "template",
    validation: Mapping[str, object] | None = None,
    attempts_before: list[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Router-shaped provenance for output produced without a router call.

    ``source`` is ``template`` (the deterministic template) or ``mock`` (a keyless
    fixture or scripted test model). ``attempts_before`` lists levels that failed
    before the template was used (for example a model port that raised).
    """
    level_name = "template" if source == "template" else "mock"
    attempts = [dict(a) for a in attempts_before or []]
    attempts.append(
        {
            "level": 2 if level_name == "template" else 0,
            "name": level_name,
            "provider": level_name,
            "model_id": model_id,
            "outcome": "ok",
            "latency_ms": 0,
        }
    )
    return {
        "router_version": None,
        "provider": level_name,
        "model_id": model_id,
        "configured_model_id": model_id,
        "role": role,
        "task": task,
        "prompt_version": prompt_version,
        "settings": {"kind": level_name, "temperature": None},
        "fallback_level": 2 if level_name == "template" else 0,
        "fallback_level_name": level_name,
        "fell_back": bool(attempts_before),
        "attempts": attempts,
        "latency_ms": 0,
        "tokens": None,
        "validation": dict(validation or {"status": "not_run", "reason": None}),
    }
