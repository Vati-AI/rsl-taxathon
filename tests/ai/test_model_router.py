"""TAX-089 model router: mock default, Groq/Gemini/Ollama adapters, fallback and provenance.

Every provider call goes through a fake transport; nothing here touches the
network. The opt-in live smoke at the end runs only with VATI_LIVE_MODEL_SMOKE=1
and keys in the environment or the ignored .env.
"""

import importlib.util
import io
import json
import os
import pickle
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

os.environ.setdefault("LANGSMITH_TRACING", "false")

from ai.models import CompletionTask, get_model  # noqa: E402
from ai.models.config import Secret, load_settings  # noqa: E402
from ai.models.providers import HttpResponse, TransportError, TransportTimeout  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None
GROQ_KEY = "gsk_test_SECRET_groq_0123456789"
GEMINI_KEY = "AIza_test_SECRET_gemini_0123456789"
HOSTED = {
    "VATI_MODEL_PROVIDER": "hosted",
    "GROQ_API_KEY": GROQ_KEY,
    "GEMINI_API_KEY": GEMINI_KEY,
}
LOCAL = {"OLLAMA_BASE_URL": "http://127.0.0.1:11434", "VATI_MODEL_LOCAL": "llama3.2:3b"}
GROQ_LIMITS = {
    "x-ratelimit-limit-requests": "1000",
    "x-ratelimit-remaining-requests": "999",
    "x-ratelimit-limit-tokens": "8000",
    "x-ratelimit-remaining-tokens": "7900",
    "x-unrelated": "ignored",
}


def settings(env=None):
    return load_settings(env or {}, env_file=None)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        self.now += 0.005
        return self.now


class FakeTransport:
    """Returns scripted responses per URL substring, in order; records every request."""

    def __init__(self, script=None) -> None:
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.requests: list[dict] = []

    def post(self, url, headers, body, timeout):
        self.requests.append(
            {"url": url, "headers": dict(headers), "body": json.loads(body), "timeout": timeout}
        )
        for marker, responses in self.script.items():
            if marker in url:
                item = responses.pop(0) if len(responses) > 1 else responses[0]
                if isinstance(item, BaseException):
                    raise item
                return item
        raise AssertionError(f"unexpected request to {url}")


class NoNetwork:
    def post(self, url, headers, body, timeout):  # pragma: no cover - must never run
        raise AssertionError("network used")


def response(status=200, body=None, headers=None):
    raw = body if isinstance(body, bytes) else json.dumps(body or {}).encode()
    return HttpResponse(status, headers or {}, raw)


def groq_ok(content, *, model="openai/gpt-oss-120b", finish="stop", refusal=None):
    message = {"role": "assistant", "content": content}
    if refusal is not None:
        message["refusal"] = refusal
    return response(
        body={
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
        },
        headers=GROQ_LIMITS,
    )


def gemini_ok(content, *, finish="STOP", model="gemini-3.6-flash", block=None):
    body = {
        "candidates": [{"content": {"parts": [{"text": content}]}, "finishReason": finish}],
        "usageMetadata": {
            "promptTokenCount": 80,
            "candidatesTokenCount": 20,
            "totalTokenCount": 100,
        },
        "modelVersion": model,
    }
    if block:
        body["promptFeedback"] = {"blockReason": block}
    return response(body=body)


def ollama_ok(content, *, model="llama3.2:3b"):
    return response(
        body={
            "model": model,
            "message": {"role": "assistant", "content": content},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 50,
            "eval_count": 10,
        }
    )


def status_task(**overrides):
    fields = {
        "name": "status_check",
        "prompt_version": "test-prompt-v3",
        "instructions": 'Reply {"status": "ok"}.',
        "payload": {"case_id": "SYN-001", "synthetic": True},
        "template": lambda payload: {"status": "ok", "source": "template"},
        "validate": lambda out: (
            None if isinstance(out, dict) and out.get("status") == "ok" else "status_not_ok"
        ),
    }
    fields.update(overrides)
    return CompletionTask(**fields)


PROVENANCE_KEYS = {
    "provider",
    "model_id",
    "role",
    "prompt_version",
    "settings",
    "fallback_level",
    "latency_ms",
    "tokens",
    "validation",
    "attempts",
    "rate_limit",
}


class MockDefaultTests(unittest.TestCase):
    def test_unset_or_mock_provider_uses_mock_for_every_role_without_network(self) -> None:
        for env in ({}, {"VATI_MODEL_PROVIDER": "mock"}, {"VATI_MODEL_PROVIDER": "bogus"}):
            for role in ("reasoning", "fast", "document"):
                model = get_model(role, settings=settings(env), transport=NoNetwork())
                result = model.complete(status_task())
                p = result.provenance
                self.assertEqual(p["provider"], "mock")
                self.assertEqual(p["model_id"], f"mock-{role}-fixture-v1")
                self.assertEqual(p["fallback_level"], 0)
                self.assertEqual(p["validation"], {"status": "passed", "reason": None})
                self.assertTrue(PROVENANCE_KEYS <= set(p))

    def test_hosted_mode_without_keys_uses_mock_and_says_why(self) -> None:
        model = get_model(
            "reasoning", settings=settings({"VATI_MODEL_PROVIDER": "hosted"}), transport=NoNetwork()
        )
        p = model.complete(status_task()).provenance
        self.assertEqual(p["provider"], "mock")
        self.assertIn("groq_key_missing_using_mock", p["notes"])

    def test_mock_returns_fixture_when_given(self) -> None:
        model = get_model("fast", settings=settings(), transport=NoNetwork())
        out = model.complete(status_task(fixture=lambda payload: {"status": "ok", "f": 1}))
        self.assertEqual(out.output, {"status": "ok", "f": 1})

    def test_mock_output_failing_validation_falls_back_to_template(self) -> None:
        model = get_model("fast", settings=settings(), transport=NoNetwork())
        out = model.complete(status_task(fixture=lambda payload: {"status": "bad"}))
        self.assertEqual(out.provenance["fallback_level"], 2)
        self.assertEqual(out.output["source"], "template")
        self.assertEqual(out.provenance["attempts"][0]["outcome"], "validation_failed")


class GroqAdapterTests(unittest.TestCase):
    def test_reasoning_and_fast_call_groq_with_json_mode_and_record_provenance(self) -> None:
        for role, model_id in (
            ("reasoning", "openai/gpt-oss-120b"),
            ("fast", "openai/gpt-oss-20b"),
        ):
            transport = FakeTransport({"groq.com": [groq_ok('{"status": "ok"}', model=model_id)]})
            model = get_model(
                role, settings=settings(HOSTED), transport=transport, clock=FakeClock()
            )
            result = model.complete(status_task())
            (request,) = transport.requests
            self.assertEqual(request["url"], "https://api.groq.com/openai/v1/chat/completions")
            self.assertEqual(request["headers"]["Authorization"], f"Bearer {GROQ_KEY}")
            self.assertEqual(request["body"]["model"], model_id)
            self.assertEqual(request["body"]["response_format"], {"type": "json_object"})
            self.assertEqual(request["body"]["temperature"], 0)
            self.assertEqual(request["body"]["max_completion_tokens"], 2048)
            self.assertEqual(request["timeout"], 30.0)
            self.assertEqual(
                json.loads(request["body"]["messages"][1]["content"])["case_id"], "SYN-001"
            )
            p = result.provenance
            self.assertEqual(result.output, {"status": "ok"})
            self.assertEqual(
                {k: p[k] for k in ("provider", "model_id", "role", "prompt_version")},
                {
                    "provider": "groq",
                    "model_id": model_id,
                    "role": role,
                    "prompt_version": "test-prompt-v3",
                },
            )
            self.assertEqual(p["fallback_level"], 0)
            self.assertFalse(p["fell_back"])
            self.assertEqual(p["tokens"], {"input": 120, "output": 30, "total": 150})
            self.assertEqual(p["settings"]["temperature"], 0)
            self.assertEqual(p["settings"]["timeout_s"], 30.0)
            self.assertGreater(p["latency_ms"], 0)
            self.assertEqual(p["rate_limit"]["x-ratelimit-limit-tokens"], "8000")
            self.assertNotIn("x-unrelated", p["rate_limit"])
            self.assertEqual(p["validation"]["status"], "passed")

    def test_json_in_a_markdown_fence_is_accepted(self) -> None:
        transport = FakeTransport({"groq.com": [groq_ok('```json\n{"status": "ok"}\n```')]})
        model = get_model("fast", settings=settings(HOSTED), transport=transport)
        self.assertEqual(model.complete(status_task()).provenance["fallback_level"], 0)


FAILURES = {
    "rate_limited": response(429, {"error": {"message": "rate limit"}}, {"retry-after": "7"}),
    "timeout": TransportTimeout("timed out"),
    "gateway_timeout": response(504, {}),
    "refusal": groq_ok(None, refusal="I can't help with that."),
    "content_filter": groq_ok('{"status": "ok"}', finish="content_filter"),
    "json_refusal": groq_ok('{"refusal": "no"}'),
    "invalid_output": groq_ok("Sure! Here is the answer: ok"),
    "non_object_output": groq_ok('["status", "ok"]'),
    "truncated_output": groq_ok('{"status": "o', finish="length"),
    "validation_failed": groq_ok('{"status": "not ok"}'),
    "server_error": response(503, {}),
    "unauthorized": response(401, {}),
    "unavailable": TransportError("dns failure"),
    "malformed_body": response(200, b"<html>"),
}
EXPECTED_OUTCOME = {
    "gateway_timeout": "timeout",
    "content_filter": "refusal",
    "json_refusal": "refusal",
    "non_object_output": "invalid_output",
    "server_error": "http_error",
    "malformed_body": "invalid_response",
}


class FallbackTests(unittest.TestCase):
    def test_each_hosted_failure_falls_back_to_the_local_model(self) -> None:
        for case, failure in FAILURES.items():
            with self.subTest(case=case):
                transport = FakeTransport(
                    {"groq.com": [failure], "11434": [ollama_ok('{"status": "ok"}')]}
                )
                model = get_model(
                    "reasoning", settings=settings({**HOSTED, **LOCAL}), transport=transport
                )
                p = model.complete(status_task()).provenance
                self.assertEqual(p["provider"], "ollama")
                self.assertEqual(p["model_id"], "llama3.2:3b")
                self.assertEqual(p["fallback_level"], 1)
                self.assertEqual(p["fallback_level_name"], "local")
                self.assertTrue(p["fell_back"])
                first = p["attempts"][0]
                self.assertEqual(first["provider"], "groq")
                self.assertEqual(first["outcome"], EXPECTED_OUTCOME.get(case, case))
                self.assertEqual(p["tokens"], {"input": 50, "output": 10, "total": 60})
                local_request = transport.requests[-1]
                self.assertEqual(local_request["url"], "http://127.0.0.1:11434/api/chat")
                self.assertEqual(local_request["body"]["format"], "json")
                self.assertNotIn("Authorization", local_request["headers"])

    def test_each_hosted_failure_without_local_falls_back_to_the_template(self) -> None:
        for case, failure in FAILURES.items():
            with self.subTest(case=case):
                transport = FakeTransport({"groq.com": [failure]})
                model = get_model("fast", settings=settings(HOSTED), transport=transport)
                result = model.complete(status_task())
                p = result.provenance
                self.assertEqual(result.output, {"status": "ok", "source": "template"})
                self.assertEqual(p["provider"], "template")
                self.assertEqual(p["model_id"], "deterministic-template-v1")
                self.assertEqual(p["fallback_level"], 2)
                self.assertEqual(p["validation"], {"status": "passed", "reason": None})
                self.assertEqual(
                    [a["outcome"] for a in p["attempts"]], [EXPECTED_OUTCOME.get(case, case), "ok"]
                )

    def test_local_failure_also_falls_back_to_the_template(self) -> None:
        for case in ("rate_limited", "timeout", "invalid_output", "validation_failed"):
            with self.subTest(case=case):
                local_failure = {
                    "rate_limited": response(429, {}),
                    "timeout": TransportTimeout("t"),
                    "invalid_output": ollama_ok("not json"),
                    "validation_failed": ollama_ok('{"status": "nope"}'),
                }[case]
                transport = FakeTransport(
                    {"groq.com": [FAILURES["rate_limited"]], "11434": [local_failure]}
                )
                model = get_model(
                    "reasoning", settings=settings({**HOSTED, **LOCAL}), transport=transport
                )
                p = model.complete(status_task()).provenance
                self.assertEqual(p["fallback_level"], 2)
                self.assertEqual(
                    [a["outcome"] for a in p["attempts"]], ["rate_limited", case, "ok"]
                )

    def test_rate_limit_headers_are_kept_when_falling_back(self) -> None:
        transport = FakeTransport({"groq.com": [FAILURES["rate_limited"]]})
        p = (
            get_model("fast", settings=settings(HOSTED), transport=transport)
            .complete(status_task())
            .provenance
        )
        self.assertEqual(p["rate_limit"], {"retry-after": "7"})
        self.assertEqual(p["attempts"][0]["http_status"], 429)

    def test_template_failing_validation_is_recorded_not_hidden(self) -> None:
        model = get_model("fast", settings=settings({"VATI_MODEL_PROVIDER": "template"}))
        out = model.complete(status_task(template=lambda payload: {"status": "unknown"}))
        self.assertEqual(out.provenance["fallback_level"], 2)
        self.assertEqual(
            out.provenance["validation"], {"status": "failed", "reason": "status_not_ok"}
        )

    def test_local_mode_uses_ollama_then_template(self) -> None:
        transport = FakeTransport({"11434": [ollama_ok('{"status": "ok"}')]})
        model = get_model(
            "document",
            settings=settings({"VATI_MODEL_PROVIDER": "local", **LOCAL}),
            transport=transport,
        )
        p = model.complete(status_task()).provenance
        self.assertEqual((p["provider"], p["fallback_level"]), ("ollama", 1))
        unconfigured = get_model(
            "document", settings=settings({"VATI_MODEL_PROVIDER": "local"}), transport=NoNetwork()
        ).complete(status_task())
        self.assertEqual(unconfigured.provenance["attempts"][0]["outcome"], "unconfigured")
        self.assertEqual(unconfigured.provenance["fallback_level"], 2)


class GeminiDocumentRoleTests(unittest.TestCase):
    def test_document_role_defaults_to_the_confirmed_gemini_model(self) -> None:
        self.assertEqual(settings().model_ids["document"], "gemini-3.6-flash")
        transport = FakeTransport({"googleapis.com": [gemini_ok('{"status": "ok"}')]})
        p = (
            get_model("document", settings=settings(HOSTED), transport=transport)
            .complete(status_task())
            .provenance
        )
        self.assertIn("/models/gemini-3.6-flash:generateContent", transport.requests[0]["url"])
        self.assertEqual(
            (p["provider"], p["model_id"], p["fallback_level"]), ("gemini", "gemini-3.6-flash", 0)
        )

    def test_disabled_document_model_is_unconfigured_and_falls_down_the_chain(self) -> None:
        env = {**HOSTED, "VATI_MODEL_DOCUMENT": "none"}
        model = get_model("document", settings=settings(env), transport=NoNetwork())
        p = model.complete(status_task()).provenance
        self.assertEqual(
            p["attempts"][0],
            {
                "level": 0,
                "name": "hosted",
                "provider": None,
                "model_id": None,
                "outcome": "unconfigured",
            },
        )
        self.assertEqual(p["fallback_level"], 2)
        self.assertIn("document_model_unconfigured", p["notes"])

    def test_configured_document_model_calls_gemini_with_key_in_header(self) -> None:
        transport = FakeTransport({"googleapis.com": [gemini_ok('{"status": "ok"}')]})
        env = {**HOSTED, "VATI_MODEL_DOCUMENT": "gemini-3.6-flash"}
        p = (
            get_model("document", settings=settings(env), transport=transport)
            .complete(status_task())
            .provenance
        )
        (request,) = transport.requests
        self.assertEqual(
            request["url"],
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent",
        )
        self.assertNotIn(GEMINI_KEY, request["url"])
        self.assertEqual(request["headers"]["x-goog-api-key"], GEMINI_KEY)
        self.assertEqual(
            request["body"]["generationConfig"]["responseMimeType"], "application/json"
        )
        self.assertEqual(
            (p["provider"], p["model_id"], p["fallback_level"]), ("gemini", "gemini-3.6-flash", 0)
        )
        self.assertEqual(p["tokens"], {"input": 80, "output": 20, "total": 100})

    def test_gemini_refusal_truncation_and_bad_output_fall_back(self) -> None:
        env = {**HOSTED, "VATI_MODEL_DOCUMENT": "gemini-3.6-flash"}
        cases = {
            "refusal": gemini_ok("", block="SAFETY"),
            "safety_finish": gemini_ok('{"status": "ok"}', finish="SAFETY"),
            "truncated_output": gemini_ok('{"status"', finish="MAX_TOKENS"),
            "invalid_output": gemini_ok("plain text"),
            "rate_limited": response(429, {}),
        }
        for case, failure in cases.items():
            with self.subTest(case=case):
                transport = FakeTransport({"googleapis.com": [failure]})
                p = (
                    get_model("document", settings=settings(env), transport=transport)
                    .complete(status_task())
                    .provenance
                )
                self.assertEqual(p["fallback_level"], 2)
                self.assertEqual(
                    p["attempts"][0]["outcome"], "refusal" if case == "safety_finish" else case
                )


class BudgetTests(unittest.TestCase):
    def test_call_budget_stops_hosted_calls(self) -> None:
        transport = FakeTransport({"groq.com": [groq_ok('{"status": "ok"}')]})
        model = get_model(
            "fast", settings=settings({**HOSTED, "VATI_MODEL_MAX_CALLS": "1"}), transport=transport
        )
        self.assertEqual(model.complete(status_task()).provenance["fallback_level"], 0)
        p = model.complete(status_task()).provenance
        self.assertEqual(p["fallback_level"], 2)
        self.assertEqual(p["attempts"][0]["outcome"], "call_budget_exhausted")
        self.assertEqual(len(transport.requests), 1)

    def test_token_budget_stops_hosted_calls(self) -> None:
        transport = FakeTransport({"groq.com": [groq_ok('{"status": "ok"}')]})
        model = get_model(
            "fast",
            settings=settings({**HOSTED, "VATI_MODEL_MAX_TOTAL_TOKENS": "100"}),
            transport=transport,
        )
        model.complete(status_task())  # uses 150 tokens
        p = model.complete(status_task()).provenance
        self.assertEqual(p["attempts"][0]["outcome"], "token_budget_exhausted")
        self.assertEqual(model.tokens_used, 150)

    def test_oversized_input_never_leaves_the_machine(self) -> None:
        model = get_model(
            "reasoning",
            settings=settings({**HOSTED, "VATI_MODEL_MAX_INPUT_CHARS": "300"}),
            transport=NoNetwork(),
        )
        p = model.complete(status_task(payload={"text": "x" * 400})).provenance
        self.assertEqual(p["attempts"][0]["outcome"], "input_budget_exceeded")
        self.assertEqual(p["fallback_level"], 2)

    def test_timeouts_and_output_tokens_are_finite_and_clamped(self) -> None:
        s = settings({"VATI_MODEL_TIMEOUT_S": "100000", "VATI_MODEL_MAX_OUTPUT_TOKENS": "999999"})
        self.assertEqual(s.budget.timeout_s, 120.0)
        self.assertEqual(s.budget.max_output_tokens, 8192)
        self.assertIn("timeout_out_of_range_clamped", s.notes)
        self.assertEqual(settings({"VATI_MODEL_TIMEOUT_S": "abc"}).budget.timeout_s, 30.0)
        transport = FakeTransport({"groq.com": [groq_ok('{"status": "ok"}')]})
        model = get_model(
            "fast", settings=settings({**HOSTED, "VATI_MODEL_TIMEOUT_S": "5"}), transport=transport
        )
        model.complete(status_task(max_output_tokens=64))
        self.assertEqual(transport.requests[0]["timeout"], 5.0)
        self.assertEqual(transport.requests[0]["body"]["max_completion_tokens"], 64)


class UrllibTransportTests(unittest.TestCase):
    """The real transport, with urlopen replaced so nothing reaches the network."""

    def _capture(self, headers):
        from unittest import mock

        from ai.models import providers

        seen = {}

        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout):
            seen["headers"] = {k.lower(): v for k, v in request.header_items()}
            seen["timeout"] = timeout
            return Response()

        with mock.patch.object(providers.urllib.request, "urlopen", fake_urlopen):
            result = providers.UrllibTransport().post(
                "https://api.example.invalid/v1", headers, b"{}", 5.0
            )
        return result, seen

    def test_sends_an_explicit_user_agent(self) -> None:
        # Cloudflare in front of Groq answers urllib's default agent with 403 / 1010.
        from ai.models.providers import USER_AGENT

        result, seen = self._capture({"Authorization": "Bearer x"})
        self.assertEqual(result.status, 200)
        self.assertEqual(seen["headers"]["user-agent"], USER_AGENT)
        self.assertNotIn("python-urllib", seen["headers"]["user-agent"].lower())
        self.assertEqual(seen["timeout"], 5.0)

    def test_keeps_a_caller_supplied_user_agent(self) -> None:
        _, seen = self._capture({"User-Agent": "custom/1"})
        self.assertEqual(seen["headers"]["user-agent"], "custom/1")


class SecretHandlingTests(unittest.TestCase):
    def assert_no_key(self, text: str) -> None:
        self.assertNotIn(GROQ_KEY, text)
        self.assertNotIn(GEMINI_KEY, text)
        self.assertNotIn("SECRET", text)

    def test_keys_never_appear_in_repr_config_or_provenance(self) -> None:
        env = {**HOSTED, **LOCAL, "VATI_MODEL_DOCUMENT": "gemini-3.6-flash"}
        s = settings(env)
        self.assert_no_key(repr(s))
        self.assert_no_key(str(s.groq_api_key) + f"{s.gemini_api_key}")
        self.assert_no_key(json.dumps(s.public_view()))
        self.assertEqual(s.public_view()["keys_present"], {"groq": True, "gemini": True})
        for role in ("reasoning", "document"):
            leaky = FakeTransport(
                {
                    "groq.com": [RuntimeError(f"failed with Bearer {GROQ_KEY}")],
                    "googleapis.com": [response(400, {"error": GEMINI_KEY})],
                    "11434": [TransportError(GROQ_KEY)],
                }
            )
            model = get_model(role, settings=s, transport=leaky)
            self.assert_no_key(repr(model))
            self.assert_no_key(repr(model._levels))
            result = model.complete(status_task())
            self.assert_no_key(json.dumps(result.provenance))
            self.assert_no_key(json.dumps(model.config))
            self.assertEqual(result.provenance["fallback_level"], 2)

    def test_secret_cannot_be_pickled_into_a_record(self) -> None:
        with self.assertRaises(TypeError):
            pickle.dumps(Secret(GROQ_KEY))

    def test_env_file_supplies_known_keys_and_environment_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# comment\nVATI_MODEL_PROVIDER=hosted\nGROQ_API_KEY='from-file'\n"
                "VATI_MODEL_FAST=openai/gpt-oss-20b\nUNRELATED_SECRET=x\nexport VATI_MODEL_LOCAL=m\n",
                encoding="utf-8",
            )
            s = load_settings({"VATI_MODEL_FAST": "override"}, env_file=path)
            self.assertEqual(s.provider_mode, "hosted")
            self.assertEqual(s.groq_api_key.reveal(), "from-file")
            self.assertEqual(s.model_ids["fast"], "override")
            self.assertEqual(s.local_model_id, "m")
            self.assertEqual(
                load_settings({}, env_file=Path(tmp) / "missing").provider_mode, "mock"
            )

    def test_non_http_local_url_is_ignored(self) -> None:
        s = settings({"OLLAMA_BASE_URL": "file:///etc/passwd", "VATI_MODEL_LOCAL": "m"})
        self.assertIsNone(s.ollama_base_url)


class ArchitectureTests(unittest.TestCase):
    """Graphs, tools and evaluation reach models only through get_model(role)."""

    PROVIDER_IMPORT = re.compile(
        r"^\s*(from|import)\s+(ai\.models\.(providers|config|router)|groq|google\.genai|"
        r"google\.generativeai|openai|ollama|anthropic|urllib\.request|httpx|requests)\b",
        re.M,
    )
    KEY_READ = re.compile(r"GROQ_API_KEY|GEMINI_API_KEY|OLLAMA_BASE_URL|VATI_MODEL_")

    def test_no_graph_imports_a_provider_or_reads_a_key(self) -> None:
        offenders = []
        for folder in ("ai/workflows", "ai/tools", "ai/evaluation", "ai/agents"):
            for path in (ROOT / folder).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if self.PROVIDER_IMPORT.search(text) or self.KEY_READ.search(text):
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_no_new_packages_are_needed(self) -> None:
        lock = (ROOT / "ai/requirements.lock").read_text(encoding="utf-8").lower()
        for package in ("groq", "google-genai", "google-generativeai", "openai", "ollama"):
            self.assertNotIn(f"\n{package}==", f"\n{lock}")


class SmokeTests(unittest.TestCase):
    def test_offline_smoke_is_mock_and_passes(self) -> None:
        from ai.models import smoke

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(smoke.run(live=False), 0)
        report = json.loads(buffer.getvalue())
        self.assertEqual({r["provider"] for r in report["roles"].values()}, {"mock"})
        self.assertNotIn("SECRET", buffer.getvalue())

    @unittest.skipUnless(
        os.environ.get("VATI_LIVE_MODEL_SMOKE") == "1",
        "opt-in: set VATI_LIVE_MODEL_SMOKE=1 with keys in the environment or ignored .env",
    )
    def test_live_smoke_against_configured_roles(self) -> None:  # pragma: no cover - live only
        from ai.models import smoke

        self.assertEqual(smoke.run(live=True), 0)


@unittest.skipUnless(HAS_LANGGRAPH, "LangGraph not installed; runs in the CI ai-smoke job")
class BriefingPortTests(unittest.TestCase):
    """get_model(role) satisfies the TAX-053 BriefingModel port; the graph is unchanged."""

    def setUp(self) -> None:
        from ai.tools.evidence_search import synthetic_corpus_from_records
        from ai.workflows import case_briefing as cb
        from ai.workflows.fake_graph import create_investigation_state

        records = json.loads((ROOT / "data/synthetic/tax024_records.json").read_text())
        self.cb = cb
        self.corpus = synthetic_corpus_from_records(records, "tenant-synthetic")
        self.create_state = create_investigation_state

    def run_briefing(self, model):
        cb = self.cb
        source = lambda tenant_id, case_id: list(self.corpus)  # noqa: E731
        actor = {
            "actor_id": "auditor-1",
            "tenant_id": "tenant-synthetic",
            "roles": ["auditor"],
            "authenticated": True,
        }
        tool = cb.make_evidence_search_tool(
            source, actor=actor, questions=list(cb.DEFAULT_QUESTIONS)
        )
        graph = cb.build_briefing_graph(tool, model, evidence_source=source)
        state = self.create_state(
            tenant_id="tenant-synthetic", case_id="SYN-002", run_id="run-SYN-002", max_steps=1
        )
        (briefing,) = graph.invoke(state)["findings"]
        return briefing

    def test_mock_reasoning_model_briefs_like_the_template_with_provenance(self) -> None:
        baseline = self.run_briefing(self.cb.DeterministicBriefingModel())
        briefing = self.run_briefing(
            get_model("reasoning", settings=settings(), transport=NoNetwork())
        )
        self.assertEqual(briefing["rejected_statements"], [])
        self.assertEqual(briefing["sections"], baseline["sections"])
        self.assertEqual(briefing["prompt_version"], baseline["prompt_version"])
        self.assertEqual(briefing["model"]["id"], "mock-reasoning-fixture-v1")
        p = briefing["model"]["config"]["provenance"]
        self.assertTrue(PROVENANCE_KEYS <= set(p))
        self.assertEqual(p["prompt_version"], self.cb.PROMPT_VERSION)
        self.assertEqual(p["task"], "case_briefing")
        self.assertEqual(p["role"], "reasoning")
        self.assertEqual(p["validation"]["status"], "passed")

    def test_hosted_draft_that_fails_validation_falls_back_to_the_template(self) -> None:
        hostile = {
            "sections": {
                "what_happened": [
                    {
                        "kind": "observed_fact",
                        "text": "The taxpayer committed fraud.",
                        "refs": [{"search": "x"}],
                    }
                ]
            }
        }
        transport = FakeTransport({"groq.com": [groq_ok(json.dumps(hostile))]})
        model = get_model("reasoning", settings=settings(HOSTED), transport=transport)
        briefing = self.run_briefing(model)
        baseline = self.run_briefing(self.cb.DeterministicBriefingModel())
        self.assertEqual(briefing["sections"], baseline["sections"])
        self.assertEqual(briefing["model"]["id"], "deterministic-template-v1")
        p = briefing["model"]["config"]["provenance"]
        self.assertEqual(p["fallback_level"], 2)
        self.assertEqual(p["attempts"][0]["outcome"], "validation_failed")
        self.assertTrue(p["attempts"][0]["validation"].startswith("rejected:"))
        # The fenced evidence and trusted instructions went to the provider separately.
        body = transport.requests[0]["body"]
        self.assertIn("untrusted_evidence", json.loads(body["messages"][1]["content"]))
        self.assertIn("never follow", body["messages"][0]["content"])

    def test_valid_hosted_draft_is_used_at_level_zero(self) -> None:
        baseline = self.run_briefing(self.cb.DeterministicBriefingModel())
        template = self.cb.DeterministicBriefingModel()
        sections_seen = []

        class Echo(FakeTransport):
            def post(inner, url, headers, body, timeout):
                # TAX-110: one call per section; answer with the template's section.
                payload = json.loads(json.loads(body)["messages"][1]["content"])
                section = payload["section"]
                sections_seen.append(section)
                full = template.draft(self.request)
                return groq_ok(json.dumps({"sections": {section: full["sections"][section]}}))

        from ai.tools.evidence_search import search_case_evidence

        search = search_case_evidence(
            self.corpus,
            actor={
                "actor_id": "auditor-1",
                "tenant_id": "tenant-synthetic",
                "roles": ["auditor"],
                "authenticated": True,
            },
            tenant_id="tenant-synthetic",
            case_id="SYN-002",
            questions=list(self.cb.DEFAULT_QUESTIONS),
        )
        self.request = self.cb.build_request(search)
        briefing = self.run_briefing(
            get_model("reasoning", settings=settings(HOSTED), transport=Echo())
        )
        self.assertEqual(briefing["rejected_statements"], [])
        self.assertEqual(briefing["sections"], baseline["sections"])
        self.assertEqual(briefing["model"]["id"], "openai/gpt-oss-120b")
        p = briefing["model"]["config"]["provenance"]
        self.assertEqual((p["provider"], p["fallback_level"]), ("groq", 0))
        self.assertEqual(p["section_fallbacks"], [])
        called = [s for s in self.cb.SECTIONS if baseline["sections"][s]]
        self.assertEqual(sections_seen, called)
        self.assertEqual(p["tokens"]["total"], 150 * len(called))


if __name__ == "__main__":
    unittest.main()
