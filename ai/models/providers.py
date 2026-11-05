"""Provider adapters behind the router (ADR-031). Graphs never import this module.

Each adapter turns one structured completion (instructions + JSON payload) into
one HTTP request and returns the parsed JSON object, observed model ID, token use
and rate-limit headers, or raises :class:`ProviderError` with a reason code the
router uses to fall back. HTTP goes through an injected :class:`Transport`
(stdlib ``urllib`` by default, fakes in tests); there is no SDK dependency.
Exception text and response bodies are never copied into errors, so neither a
key nor provider-echoed content can leak into a record.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from ai.models.config import Secret

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
JSON_ONLY = (
    "Respond with exactly one JSON object and nothing else. Do not wrap it in Markdown. "
    "If you cannot comply, respond with a JSON object containing only a 'refusal' field."
)
RATE_LIMIT_HEADERS = (
    "retry-after",
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
)
_GEMINI_REFUSALS = frozenset(
    {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}
)

# --- transport ----------------------------------------------------------------------


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class TransportTimeout(Exception):
    """The request did not complete within its timeout."""


class TransportError(Exception):
    """The request could not be sent or the connection failed."""


class Transport(Protocol):
    def post(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout: float
    ) -> HttpResponse: ...


class UrllibTransport:
    """Stdlib HTTPS POST with a finite timeout; HTTP errors are returned, not raised."""

    def post(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout: float
    ) -> HttpResponse:
        if not url.startswith(("https://", "http://")):
            raise TransportError("unsupported_scheme")
        request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return HttpResponse(
                    response.status,
                    {k.lower(): v for k, v in response.headers.items()},
                    response.read(),
                )
        except urllib.error.HTTPError as error:
            return HttpResponse(
                error.code,
                {k.lower(): v for k, v in (error.headers or {}).items()},
                error.read() if error.fp else b"",
            )
        except (TimeoutError, socket.timeout) as error:
            raise TransportTimeout("timeout") from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise TransportTimeout("timeout") from error
            raise TransportError("connection_failed") from error
        except OSError as error:
            raise TransportError("connection_failed") from error


# --- results and errors -------------------------------------------------------------


class ProviderError(Exception):
    """A failed attempt. ``reason`` is a fixed code; no provider text is kept."""

    def __init__(
        self,
        reason: str,
        *,
        http_status: int | None = None,
        rate_limit: Mapping[str, str] | None = None,
        tokens: Mapping[str, int | None] | None = None,
        observed_model_id: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status
        self.rate_limit = dict(rate_limit or {})
        self.tokens = dict(tokens) if tokens else None
        self.observed_model_id = observed_model_id


@dataclass
class Completion:
    output: object
    observed_model_id: str | None
    tokens: dict[str, int | None] | None = None
    rate_limit: dict[str, str] = field(default_factory=dict)
    http_status: int | None = None


@dataclass(frozen=True)
class Prompt:
    """One structured completion: trusted instructions and a JSON payload."""

    instructions: str
    payload: Mapping[str, object]
    max_output_tokens: int
    timeout_s: float

    def user_text(self) -> str:
        return json.dumps(self.payload, sort_keys=True, ensure_ascii=False, default=str)


class Provider(Protocol):
    name: str
    model_id: str

    def settings(self) -> dict[str, object]: ...

    def complete(self, prompt: Prompt) -> Completion: ...


def _rate_limit(headers: Mapping[str, str]) -> dict[str, str]:
    lowered = {k.lower(): v for k, v in headers.items()}
    return {k: str(lowered[k])[:64] for k in RATE_LIMIT_HEADERS if k in lowered}


def _post_json(
    transport: Transport, url: str, headers: Mapping[str, str], body: Mapping[str, object], timeout
) -> tuple[HttpResponse, dict[str, str]]:
    data = json.dumps(body).encode("utf-8")
    try:
        response = transport.post(url, headers, data, timeout)
    except TransportTimeout:
        raise ProviderError("timeout") from None
    except TransportError:
        raise ProviderError("unavailable") from None
    except Exception:
        raise ProviderError("unavailable") from None
    limits = _rate_limit(response.headers)
    if response.status == 429:
        raise ProviderError("rate_limited", http_status=429, rate_limit=limits)
    if response.status in (408, 504):
        raise ProviderError("timeout", http_status=response.status, rate_limit=limits)
    if response.status in (401, 403):
        raise ProviderError("unauthorized", http_status=response.status, rate_limit=limits)
    if not 200 <= response.status < 300:
        raise ProviderError("http_error", http_status=response.status, rate_limit=limits)
    return response, limits


def _decode(response: HttpResponse, limits: Mapping[str, str]) -> Mapping[str, object]:
    try:
        decoded = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        decoded = None
    if not isinstance(decoded, Mapping):
        raise ProviderError("invalid_response", http_status=response.status, rate_limit=limits)
    return decoded


def parse_json_object(text: object) -> dict[str, object] | None:
    """The model's text as one JSON object, tolerating a single Markdown code fence."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    try:
        value = json.loads(stripped)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _str(value: object) -> str | None:
    return value[:128] if isinstance(value, str) else None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _finish(
    text: object,
    *,
    model: str | None,
    tokens: dict[str, int | None] | None,
    limits: dict[str, str],
    status: int,
) -> Completion:
    output = parse_json_object(text)
    if output is None:
        raise ProviderError(
            "invalid_output",
            http_status=status,
            rate_limit=limits,
            tokens=tokens,
            observed_model_id=model,
        )
    if set(output) == {"refusal"}:
        raise ProviderError(
            "refusal", http_status=status, rate_limit=limits, tokens=tokens, observed_model_id=model
        )
    return Completion(output, model, tokens, limits, status)


# --- adapters -----------------------------------------------------------------------


class _OpenAICompatible:
    """Chat completions in JSON mode (Groq; the same shape serves other compatible hosts)."""

    name = "openai_compatible"
    url = ""

    def __init__(self, model_id: str, api_key: Secret, transport: Transport) -> None:
        self.model_id = model_id
        self._key = api_key
        self._transport = transport

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model_id={self.model_id!r}, api_key={self._key!r})"

    def settings(self) -> dict[str, object]:
        return {"temperature": 0, "response_format": "json_object"}

    def complete(self, prompt: Prompt) -> Completion:
        body = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": f"{prompt.instructions}\n\n{JSON_ONLY}"},
                {"role": "user", "content": prompt.user_text()},
            ],
            "temperature": 0,
            "max_completion_tokens": prompt.max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self._key.reveal()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        response, limits = _post_json(self._transport, self.url, headers, body, prompt.timeout_s)
        data = _decode(response, limits)
        usage = data.get("usage") if isinstance(data.get("usage"), Mapping) else {}
        assert isinstance(usage, Mapping)
        tokens = {
            "input": _int(usage.get("prompt_tokens")),
            "output": _int(usage.get("completion_tokens")),
            "total": _int(usage.get("total_tokens")),
        }
        model = _str(data.get("model"))
        choices = data.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, Mapping) else None
        if not isinstance(first, Mapping) or not isinstance(message, Mapping):
            raise ProviderError(
                "invalid_response", http_status=response.status, rate_limit=limits, tokens=tokens
            )
        finish = first.get("finish_reason")
        if message.get("refusal") or finish == "content_filter":
            raise ProviderError(
                "refusal",
                http_status=response.status,
                rate_limit=limits,
                tokens=tokens,
                observed_model_id=model,
            )
        if finish == "length":
            raise ProviderError(
                "truncated_output",
                http_status=response.status,
                rate_limit=limits,
                tokens=tokens,
                observed_model_id=model,
            )
        return _finish(
            message.get("content"),
            model=model,
            tokens=tokens,
            limits=limits,
            status=response.status,
        )


class GroqProvider(_OpenAICompatible):
    name = "groq"
    url = GROQ_CHAT_URL


class GeminiProvider:
    """Gemini ``generateContent`` with a JSON response type; the key goes in a header."""

    name = "gemini"

    def __init__(self, model_id: str, api_key: Secret, transport: Transport) -> None:
        self.model_id = model_id
        self._key = api_key
        self._transport = transport

    def __repr__(self) -> str:
        return f"GeminiProvider(model_id={self.model_id!r}, api_key={self._key!r})"

    def settings(self) -> dict[str, object]:
        return {"temperature": 0, "response_mime_type": "application/json"}

    def complete(self, prompt: Prompt) -> Completion:
        body = {
            "systemInstruction": {"parts": [{"text": f"{prompt.instructions}\n\n{JSON_ONLY}"}]},
            "contents": [{"role": "user", "parts": [{"text": prompt.user_text()}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": prompt.max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        headers = {
            "x-goog-api-key": self._key.reveal(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = GEMINI_URL.format(model=self.model_id)
        response, limits = _post_json(self._transport, url, headers, body, prompt.timeout_s)
        data = _decode(response, limits)
        usage = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), Mapping) else {}
        assert isinstance(usage, Mapping)
        tokens = {
            "input": _int(usage.get("promptTokenCount")),
            "output": _int(usage.get("candidatesTokenCount")),
            "total": _int(usage.get("totalTokenCount")),
        }
        model = _str(data.get("modelVersion"))
        feedback = data.get("promptFeedback")
        if isinstance(feedback, Mapping) and feedback.get("blockReason"):
            raise ProviderError(
                "refusal",
                http_status=response.status,
                rate_limit=limits,
                tokens=tokens,
                observed_model_id=model,
            )
        candidates = data.get("candidates")
        first = candidates[0] if isinstance(candidates, list) and candidates else None
        if not isinstance(first, Mapping):
            raise ProviderError(
                "invalid_response", http_status=response.status, rate_limit=limits, tokens=tokens
            )
        finish = first.get("finishReason")
        if finish in _GEMINI_REFUSALS:
            raise ProviderError(
                "refusal",
                http_status=response.status,
                rate_limit=limits,
                tokens=tokens,
                observed_model_id=model,
            )
        if finish == "MAX_TOKENS":
            raise ProviderError(
                "truncated_output",
                http_status=response.status,
                rate_limit=limits,
                tokens=tokens,
                observed_model_id=model,
            )
        content = first.get("content")
        parts = content.get("parts") if isinstance(content, Mapping) else None
        text = (
            "".join(
                str(p.get("text", ""))
                for p in parts
                if isinstance(p, Mapping) and not p.get("thought")
            )
            if isinstance(parts, list)
            else None
        )
        return _finish(text, model=model, tokens=tokens, limits=limits, status=response.status)


class OllamaProvider:
    """Optional local open-weights model through Ollama's ``/api/chat`` in JSON format."""

    name = "ollama"

    def __init__(self, model_id: str, base_url: str, transport: Transport) -> None:
        self.model_id = model_id
        self._url = base_url.rstrip("/") + "/api/chat"
        self._transport = transport

    def __repr__(self) -> str:
        return f"OllamaProvider(model_id={self.model_id!r})"

    def settings(self) -> dict[str, object]:
        return {"temperature": 0, "format": "json"}

    def complete(self, prompt: Prompt) -> Completion:
        body = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": f"{prompt.instructions}\n\n{JSON_ONLY}"},
                {"role": "user", "content": prompt.user_text()},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "num_predict": prompt.max_output_tokens},
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        response, limits = _post_json(self._transport, self._url, headers, body, prompt.timeout_s)
        data = _decode(response, limits)
        input_tokens, output_tokens = (
            _int(data.get("prompt_eval_count")),
            _int(data.get("eval_count")),
        )
        tokens = {
            "input": input_tokens,
            "output": output_tokens,
            "total": (
                input_tokens + output_tokens
                if input_tokens is not None and output_tokens is not None
                else None
            ),
        }
        model = _str(data.get("model"))
        message = data.get("message")
        if data.get("done_reason") == "length":
            raise ProviderError(
                "truncated_output",
                http_status=response.status,
                tokens=tokens,
                observed_model_id=model,
            )
        text = message.get("content") if isinstance(message, Mapping) else None
        return _finish(text, model=model, tokens=tokens, limits=limits, status=response.status)
