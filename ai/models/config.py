"""Router settings from the environment and the ignored ``.env`` file (ADR-031).

Only this module reads model configuration or credentials. Values come from the
process environment first, then from the repository's ignored ``.env`` file (only
the keys listed in ``KNOWN_KEYS``; nothing else in that file is read). Keys are
wrapped in :class:`Secret`, whose ``repr``/``str`` never show the value, so a
settings object, a model or a provenance record can be printed or logged safely.

``VATI_MODEL_PROVIDER``:

- ``mock`` (default, also used for unset or unknown values): deterministic
  fixtures, no network, no keys.
- ``hosted``: each role uses its hosted provider (Groq for ``reasoning`` and
  ``fast``, Gemini for ``document``) when that provider's key and the role's
  model ID are set, then the optional local model, then the template. A role
  whose key is missing uses ``mock`` instead; a role with a key but no model ID
  (the ``document`` role when ``VATI_MODEL_DOCUMENT`` is unset, ADR-031) is left
  unconfigured and falls back down the chain.
- ``local``: optional Ollama model, then the template.
- ``template``: deterministic template only.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

ROLES = ("reasoning", "fast", "document")
PROVIDER_MODES = ("mock", "hosted", "local", "template")
HOSTED_PROVIDER = {"reasoning": "groq", "fast": "groq", "document": "gemini"}
# ADR-031 demo defaults. The document role deliberately has no built-in default:
# gemini-2.5-flash is deprecated, and the confirmed gemini-3.6-flash is set through
# VATI_MODEL_DOCUMENT (.env.example) until the live smoke verifies its limits.
DEFAULT_MODEL_IDS = {
    "reasoning": "openai/gpt-oss-120b",
    "fast": "openai/gpt-oss-20b",
    "document": None,
}
ROLE_ENV = {
    "reasoning": "VATI_MODEL_REASONING",
    "fast": "VATI_MODEL_FAST",
    "document": "VATI_MODEL_DOCUMENT",
}
KNOWN_KEYS = frozenset(
    {
        "VATI_MODEL_PROVIDER",
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "VATI_MODEL_REASONING",
        "VATI_MODEL_FAST",
        "VATI_MODEL_DOCUMENT",
        "OLLAMA_BASE_URL",
        "VATI_MODEL_LOCAL",
        "VATI_MODEL_TIMEOUT_S",
        "VATI_MODEL_MAX_CALLS",
        "VATI_MODEL_MAX_OUTPUT_TOKENS",
        "VATI_MODEL_MAX_TOTAL_TOKENS",
        "VATI_MODEL_MAX_INPUT_CHARS",
    }
)
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = ROOT / ".env"

# Finite budgets (AI_STANDARD). Prototype choices sized for Groq's published
# 8,000 tokens/min developer limit; record observed limits with the live smoke.
DEFAULT_TIMEOUT_S = 30.0
MAX_TIMEOUT_S = 120.0
DEFAULT_MAX_CALLS = 50
DEFAULT_MAX_OUTPUT_TOKENS = 2048
MAX_OUTPUT_TOKENS = 8192
DEFAULT_MAX_TOTAL_TOKENS = 100_000
DEFAULT_MAX_INPUT_CHARS = 24_000


class Secret:
    """A credential that never appears in ``repr``, ``str`` or formatting."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return "Secret('***')" if self._value else "Secret('')"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return repr(self)

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("Secret values cannot be pickled or copied into records")


@dataclass(frozen=True)
class Budget:
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_calls: int = DEFAULT_MAX_CALLS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS


@dataclass(frozen=True)
class Settings:
    provider_mode: str = "mock"
    groq_api_key: Secret = field(default_factory=lambda: Secret(""))
    gemini_api_key: Secret = field(default_factory=lambda: Secret(""))
    model_ids: Mapping[str, str | None] = field(default_factory=lambda: dict(DEFAULT_MODEL_IDS))
    ollama_base_url: str | None = None
    local_model_id: str | None = None
    budget: Budget = field(default_factory=Budget)
    notes: tuple[str, ...] = ()

    def key_for(self, provider: str) -> Secret:
        return self.groq_api_key if provider == "groq" else self.gemini_api_key

    def public_view(self) -> dict[str, object]:
        """Settings safe to record: key presence only, never values."""
        return {
            "provider_mode": self.provider_mode,
            "keys_present": {"groq": bool(self.groq_api_key), "gemini": bool(self.gemini_api_key)},
            "model_ids": dict(self.model_ids),
            "local": {
                "configured": bool(self.ollama_base_url and self.local_model_id),
                "model_id": self.local_model_id,
            },
            "budget": {
                "timeout_s": self.budget.timeout_s,
                "max_calls": self.budget.max_calls,
                "max_output_tokens": self.budget.max_output_tokens,
                "max_total_tokens": self.budget.max_total_tokens,
                "max_input_chars": self.budget.max_input_chars,
            },
            "notes": list(self.notes),
        }


def read_env_file(path: Path) -> dict[str, str]:
    """Parse ``KEY=value`` lines for known keys only; missing file gives ``{}``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if key not in KNOWN_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def _number(raw: str | None, default: float, low: float, high: float, notes: list[str], name: str):
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        notes.append(f"{name}_invalid_using_default")
        return default
    if not low <= value <= high:
        notes.append(f"{name}_out_of_range_clamped")
        value = min(max(value, low), high)
    return value


def load_settings(
    environ: Mapping[str, str] | None = None, *, env_file: Path | None = DEFAULT_ENV_FILE
) -> Settings:
    """Settings from ``environ`` (default ``os.environ``) over the ignored ``.env`` file.

    Pass ``env_file=None`` to ignore the file (tests do).
    """
    merged: dict[str, str] = read_env_file(env_file) if env_file is not None else {}
    source = os.environ if environ is None else environ
    merged.update({k: v for k, v in source.items() if k in KNOWN_KEYS})
    notes: list[str] = []

    mode = (merged.get("VATI_MODEL_PROVIDER") or "mock").strip().lower()
    if mode == "live":
        mode = "hosted"
    if mode not in PROVIDER_MODES:
        notes.append("unknown_provider_mode_using_mock")
        mode = "mock"

    model_ids: dict[str, str | None] = {}
    for role in ROLES:
        raw = merged.get(ROLE_ENV[role])
        model_ids[role] = raw.strip() if raw and raw.strip() else DEFAULT_MODEL_IDS[role]

    base_url = (merged.get("OLLAMA_BASE_URL") or "").strip() or None
    if base_url and not base_url.startswith(("http://", "https://")):
        notes.append("ollama_base_url_not_http_ignored")
        base_url = None

    budget = Budget(
        timeout_s=_number(
            merged.get("VATI_MODEL_TIMEOUT_S"),
            DEFAULT_TIMEOUT_S,
            1.0,
            MAX_TIMEOUT_S,
            notes,
            "timeout",
        ),
        max_calls=int(
            _number(merged.get("VATI_MODEL_MAX_CALLS"), DEFAULT_MAX_CALLS, 0, 1000, notes, "calls")
        ),
        max_output_tokens=int(
            _number(
                merged.get("VATI_MODEL_MAX_OUTPUT_TOKENS"),
                DEFAULT_MAX_OUTPUT_TOKENS,
                16,
                MAX_OUTPUT_TOKENS,
                notes,
                "output_tokens",
            )
        ),
        max_total_tokens=int(
            _number(
                merged.get("VATI_MODEL_MAX_TOTAL_TOKENS"),
                DEFAULT_MAX_TOTAL_TOKENS,
                0,
                10_000_000,
                notes,
                "total_tokens",
            )
        ),
        max_input_chars=int(
            _number(
                merged.get("VATI_MODEL_MAX_INPUT_CHARS"),
                DEFAULT_MAX_INPUT_CHARS,
                256,
                1_000_000,
                notes,
                "input_chars",
            )
        ),
    )
    return Settings(
        provider_mode=mode,
        groq_api_key=Secret((merged.get("GROQ_API_KEY") or "").strip()),
        gemini_api_key=Secret((merged.get("GEMINI_API_KEY") or "").strip()),
        model_ids=model_ids,
        ollama_base_url=base_url,
        local_model_id=(merged.get("VATI_MODEL_LOCAL") or "").strip() or None,
        budget=budget,
        notes=tuple(notes),
    )
