"""``get_model(role)``: role-based model routing with per-call fallback (ADR-031).

Agents ask for a role (``reasoning``, ``fast`` or ``document``) and receive a
:class:`RoutedModel`. It satisfies the existing model ports (``BriefingModel``:
``model_id``, ``config``, ``draft(request)``) and offers ``complete(task)``, a
general structured completion for later agents. Each call walks the role's chain:

    level 0  hosted model (Groq / Gemini), or the ``mock`` fixture provider
    level 1  optional local open-weights model (Ollama)
    level 2  deterministic template (always available, never invents a finding)

A rate limit (HTTP 429), timeout, refusal, invalid or truncated structured
output, any other provider failure, an exhausted call/token/input budget or a
failed validation moves the call to the next level. Every output carries a
provenance record (provider, model ID, role, prompt version, settings, fallback
level, every attempt with its reason, latency, token use, rate-limit headers and
the validation result). Model output is still untrusted: callers validate it
again (for example ``case_briefing.validate_draft``).

``RoutedModel`` keeps per-instance budgets and the last call's provenance in
``model_id``/``config`` so existing records (``{"id", "config"}``) pick it up
unchanged. Use one instance per run; it is not safe to share across threads.
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ai.models.config import HOSTED_PROVIDER, ROLES, Settings, load_settings
from ai.models.providers import (
    Completion,
    GeminiProvider,
    GroqProvider,
    OllamaProvider,
    Prompt,
    Provider,
    ProviderError,
    Transport,
    UrllibTransport,
)

ROUTER_VERSION = "tax110-router-v2"
TEMPLATE_MODEL_ID = "deterministic-template-v1"
LEVELS = {"hosted": 0, "mock": 0, "local": 1, "template": 2}

Validator = Callable[[object], str | None]


@dataclass(frozen=True)
class CompletionTask:
    """One structured completion.

    ``instructions`` are trusted; ``payload`` is JSON data (callers fence untrusted
    document text inside it). ``template`` is the deterministic last fallback and
    must not invent findings. ``validate`` returns ``None`` when output is
    acceptable, else a short reason code. ``fixture`` is the ``mock`` provider's
    response (defaults to the template).
    """

    name: str
    prompt_version: str
    instructions: str
    payload: Mapping[str, object]
    template: Callable[[Mapping[str, object]], object]
    validate: Validator | None = None
    fixture: Callable[[Mapping[str, object]], object] | None = None
    max_output_tokens: int | None = None
    template_id: str = TEMPLATE_MODEL_ID


@dataclass(frozen=True)
class ModelOutput:
    output: object
    provenance: dict[str, object]


class MockProvider:
    """Deterministic fixture responses; no network and no keys."""

    name = "mock"

    def __init__(self, role: str) -> None:
        self.model_id = f"mock-{role}-fixture-v1"
        self.task: CompletionTask | None = None

    def __repr__(self) -> str:
        return f"MockProvider(model_id={self.model_id!r})"

    def settings(self) -> dict[str, object]:
        return {"kind": "fixture", "temperature": None}

    def complete(self, prompt: Prompt) -> Completion:
        task = self.task
        if task is None:  # pragma: no cover - set by the router before each call
            raise ProviderError("unavailable")
        produce = task.fixture or task.template
        try:
            # JSON round trip: the fixture behaves like a provider response (no aliasing).
            output = json.loads(json.dumps(produce(prompt.payload)))
        except Exception:
            raise ProviderError("invalid_output") from None
        if not isinstance(output, dict):
            raise ProviderError("invalid_output")
        return Completion(output, self.model_id, None, {}, None)


@dataclass
class _Level:
    name: str  # hosted | mock | local | template
    provider: Provider | None
    note: str | None = None  # why a level is present but unusable

    @property
    def index(self) -> int:
        return LEVELS[self.name]


def _chain(role: str, settings: Settings, transport: Transport) -> tuple[list[_Level], list[str]]:
    notes: list[str] = list(settings.notes)
    levels: list[_Level] = []
    local: _Level | None = None
    if settings.ollama_base_url and settings.local_model_id:
        local = _Level(
            "local", OllamaProvider(settings.local_model_id, settings.ollama_base_url, transport)
        )
    mode = settings.provider_mode
    if mode == "mock":
        levels.append(_Level("mock", MockProvider(role)))
    elif mode == "hosted":
        provider = HOSTED_PROVIDER[role]
        key = settings.key_for(provider)
        model_id = settings.model_ids.get(role)
        if not key:
            notes.append(f"{provider}_key_missing_using_mock")
            levels.append(_Level("mock", MockProvider(role)))
        elif not model_id:
            # ADR-031: no default document model ID; the role falls down the chain.
            notes.append(f"{role}_model_unconfigured")
            levels.append(_Level("hosted", None, note="unconfigured"))
            if local:
                levels.append(local)
        else:
            cls = GroqProvider if provider == "groq" else GeminiProvider
            levels.append(_Level("hosted", cls(model_id, key, transport)))
            if local:
                levels.append(local)
    elif mode == "local":
        if local:
            levels.append(local)
        else:
            notes.append("local_model_unconfigured")
            levels.append(_Level("local", None, note="unconfigured"))
    levels.append(_Level("template", None))
    return levels, notes


def _ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


class RoutedModel:
    """A role's model chain. Satisfies ``BriefingModel`` and offers ``complete``."""

    def __init__(
        self,
        role: str,
        settings: Settings,
        *,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if role not in ROLES:
            raise ValueError(f"unknown model role: {role!r}")
        self.role = role
        self._settings = settings
        self._clock = clock
        self._levels, self._notes = _chain(role, settings, transport or UrllibTransport())
        self.calls_used = 0
        self.tokens_used = 0
        self.last_provenance: dict[str, object] | None = None
        primary = self._levels[0]
        self.model_id: str = primary.provider.model_id if primary.provider else TEMPLATE_MODEL_ID
        self.config: dict[str, object] = self._config()

    def __repr__(self) -> str:
        return (
            f"RoutedModel(role={self.role!r}, mode={self._settings.provider_mode!r}, "
            f"chain={self.chain!r})"
        )

    @property
    def chain(self) -> list[dict[str, object]]:
        return [
            {
                "level": level.index,
                "name": level.name,
                "provider": level.provider.name
                if level.provider
                else ("template" if level.name == "template" else None),
                "model_id": level.provider.model_id
                if level.provider
                else (TEMPLATE_MODEL_ID if level.name == "template" else None),
                **({"note": level.note} if level.note else {}),
            }
            for level in self._levels
        ]

    def _config(self) -> dict[str, object]:
        return {
            "kind": "routed",
            "router_version": ROUTER_VERSION,
            "role": self.role,
            "provider_mode": self._settings.provider_mode,
            "chain": self.chain,
            "notes": list(self._notes),
            "provenance": copy.deepcopy(self.last_provenance),
        }

    # --- ports --------------------------------------------------------------------

    def draft(
        self,
        request: Mapping[str, Any],
        *,
        complete: Callable[[CompletionTask], ModelOutput] | None = None,
    ) -> object:
        """``BriefingModel`` port (TAX-053): a briefing drafted section by section.

        Each section is one bounded ``complete`` call that is validated and falls
        back to the template on its own (TAX-110); the merged provenance, with a
        record per section, becomes ``last_provenance``. ``complete`` lets a caller
        (the paced live harness) route each section call through itself.
        """
        from ai.models.briefing import draft_by_section

        draft, provenance = draft_by_section(complete or self.complete, request, role=self.role)
        self.last_provenance = provenance
        self.model_id = str(provenance["model_id"])
        self.config = self._config()
        return draft

    def estimate_tokens(self, task: CompletionTask) -> int:
        """Tokens a provider may charge for ``task``: input (about 4 chars/token) plus
        the output budget. Used to pace live runs before the call (TAX-110)."""
        budget = self._settings.budget
        max_tokens = min(
            task.max_output_tokens or budget.max_output_tokens, budget.max_output_tokens
        )
        try:
            chars = len(task.instructions) + len(
                Prompt(task.instructions, task.payload, max_tokens, budget.timeout_s).user_text()
            )
        except (TypeError, ValueError):
            chars = 0
        return chars // 4 + max_tokens

    def complete(self, task: CompletionTask) -> ModelOutput:
        budget = self._settings.budget
        max_tokens = min(
            task.max_output_tokens or budget.max_output_tokens, budget.max_output_tokens
        )
        prompt = Prompt(task.instructions, task.payload, max_tokens, budget.timeout_s)
        try:
            payload_chars = len(prompt.user_text()) + len(task.instructions)
        except (TypeError, ValueError):
            payload_chars = None
        attempts: list[dict[str, object]] = []
        rate_limit: dict[str, str] = {}
        started = self._clock()

        for level in self._levels:
            if level.name == "template":
                break
            provider = level.provider
            entry: dict[str, object] = {
                "level": level.index,
                "name": level.name,
                "provider": provider.name if provider else None,
                "model_id": provider.model_id if provider else None,
            }
            if provider is None:
                attempts.append({**entry, "outcome": level.note or "unconfigured"})
                continue
            networked = level.name in ("hosted", "local")
            if payload_chars is None:
                attempts.append({**entry, "outcome": "invalid_input"})
                continue
            if networked:
                # What a rate limiter may charge even when no usage comes back (TAX-110).
                entry["request_chars"] = payload_chars
                entry["max_output_tokens"] = max_tokens
                if payload_chars > budget.max_input_chars:
                    attempts.append({**entry, "outcome": "input_budget_exceeded"})
                    continue
                if self.calls_used >= budget.max_calls:
                    attempts.append({**entry, "outcome": "call_budget_exhausted"})
                    continue
                if self.tokens_used >= budget.max_total_tokens:
                    attempts.append({**entry, "outcome": "token_budget_exhausted"})
                    continue
                self.calls_used += 1
            if isinstance(provider, MockProvider):
                provider.task = task
            t0 = self._clock()
            try:
                completion = provider.complete(prompt)
            except ProviderError as error:
                self._charge(error.tokens)
                rate_limit = error.rate_limit or rate_limit
                attempts.append(
                    {
                        **entry,
                        "outcome": error.reason,
                        "http_status": error.http_status,
                        "error_code": error.error_code,
                        "observed_model_id": error.observed_model_id,
                        "latency_ms": _ms(self._clock() - t0),
                        "tokens": error.tokens,
                    }
                )
                continue
            except Exception:  # an adapter bug must not stop the chain
                attempts.append(
                    {
                        **entry,
                        "outcome": "provider_error",
                        "http_status": None,
                        "error_code": "adapter_exception",
                        "latency_ms": _ms(self._clock() - t0),
                    }
                )
                continue
            finally:
                if isinstance(provider, MockProvider):
                    provider.task = None
            latency = _ms(self._clock() - t0)
            self._charge(completion.tokens)
            rate_limit = completion.rate_limit or rate_limit
            reason = self._validate(task, completion.output)
            attempt = {
                **entry,
                "observed_model_id": completion.observed_model_id,
                "http_status": completion.http_status,
                "latency_ms": latency,
                "tokens": completion.tokens,
            }
            if reason is not None:
                attempts.append({**attempt, "outcome": "validation_failed", "validation": reason})
                continue
            attempts.append({**attempt, "outcome": "ok"})
            return self._result(
                task,
                level,
                completion.output,
                completion.observed_model_id or provider.model_id,
                {**provider.settings(), **self._limits(prompt)},
                attempts,
                started,
                completion.tokens,
                rate_limit,
                {"status": "passed" if task.validate else "not_run", "reason": None},
            )

        # Level 2: deterministic template. Its output is validated and recorded too.
        t0 = self._clock()
        output = task.template(task.payload)
        reason = self._validate(task, output)
        template = next(lv for lv in self._levels if lv.name == "template")
        attempts.append(
            {
                "level": template.index,
                "name": "template",
                "provider": "template",
                "model_id": task.template_id,
                "latency_ms": _ms(self._clock() - t0),
                "outcome": "ok" if reason is None else "validation_failed",
                **({"validation": reason} if reason else {}),
            }
        )
        validation: dict[str, object] = {
            "status": ("passed" if reason is None else "failed") if task.validate else "not_run",
            "reason": reason,
        }
        return self._result(
            task,
            template,
            output,
            task.template_id,
            {"kind": "template", "temperature": None, "sampling": None},
            attempts,
            started,
            None,
            rate_limit,
            validation,
        )

    # --- internals ----------------------------------------------------------------

    def _limits(self, prompt: Prompt) -> dict[str, object]:
        return {"max_output_tokens": prompt.max_output_tokens, "timeout_s": prompt.timeout_s}

    def _charge(self, tokens: Mapping[str, int | None] | None) -> None:
        if tokens:
            total = tokens.get("total")
            if total is None:
                total = (tokens.get("input") or 0) + (tokens.get("output") or 0)
            self.tokens_used += total

    @staticmethod
    def _validate(task: CompletionTask, output: object) -> str | None:
        if task.validate is None:
            return None
        try:
            reason = task.validate(output)
        except Exception:
            return "validator_error"
        return None if reason is None else str(reason)[:64]

    def _result(
        self,
        task: CompletionTask,
        level: _Level,
        output: object,
        model_id: str,
        settings: dict[str, object],
        attempts: list[dict[str, object]],
        started: float,
        tokens: Mapping[str, int | None] | None,
        rate_limit: Mapping[str, str],
        validation: dict[str, object],
    ) -> ModelOutput:
        provider = level.provider.name if level.provider else "template"
        provenance: dict[str, object] = {
            "router_version": ROUTER_VERSION,
            "provider": provider,
            "model_id": model_id,
            "configured_model_id": level.provider.model_id if level.provider else task.template_id,
            "role": self.role,
            "task": task.name,
            "prompt_version": task.prompt_version,
            "settings": settings,
            "fallback_level": level.index,
            "fallback_level_name": level.name,
            "fell_back": any(a["outcome"] != "ok" for a in attempts),
            "attempts": attempts,
            "latency_ms": _ms(self._clock() - started),
            "tokens": dict(tokens) if tokens else None,
            "rate_limit": dict(rate_limit),
            "validation": validation,
            "notes": list(self._notes),
        }
        self.last_provenance = provenance
        self.model_id = model_id
        self.config = self._config()
        return ModelOutput(output, copy.deepcopy(provenance))


def get_model(
    role: str,
    *,
    settings: Settings | None = None,
    transport: Transport | None = None,
    clock: Callable[[], float] = time.monotonic,
    offline: bool = False,
) -> RoutedModel:
    """The model for ``role``. Configuration comes from the environment / ignored ``.env``.

    ``offline=True`` ignores the environment and ``.env`` and returns the keyless
    ``mock`` chain (no network), so a gate such as ``agent_eval --check`` stays
    offline and deterministic even on a machine whose ``.env`` enables live models.
    """
    if settings is None:
        settings = Settings() if offline else load_settings()
    return RoutedModel(
        role,
        settings,
        transport=transport,
        clock=clock,
    )
