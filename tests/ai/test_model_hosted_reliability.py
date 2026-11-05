"""TAX-110 reliable hosted briefing and evidence reading within free-tier limits.

Covers section-by-section briefing drafting (bounded per call, per-section
validation and fallback), HTTP status and provider error code on every failed
attempt (never key material), live pacing that counts failed calls, and role
selection by document length. Every provider call goes through a fake transport;
nothing here touches the network or reads the local ``.env``.
"""

import copy
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("LANGSMITH_TRACING", "false")

from ai.models import get_model  # noqa: E402
from ai.models.config import load_settings  # noqa: E402
from ai.models.providers import HttpResponse, TransportError, error_code  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None
RECORDS = {
    r["case_id"]: r for r in json.loads((ROOT / "data/synthetic/tax024_records.json").read_text())
}
FIXTURES = json.loads((ROOT / "data/synthetic/tax056_evaluation_fixtures.json").read_text())
TENANT = "tenant-synthetic"
GROQ_KEY = "gsk_test_SECRET_groq_0123456789"
GEMINI_KEY = "AIza_test_SECRET_gemini_0123456789"
HOSTED = {"VATI_MODEL_PROVIDER": "hosted", "GROQ_API_KEY": GROQ_KEY, "GEMINI_API_KEY": GEMINI_KEY}
GROQ_TOKENS_PER_MINUTE = 8000


def settings(env=None):
    return load_settings(env or {}, env_file=None)


def _load_contract():
    spec = importlib.util.spec_from_file_location(
        "tax110_contract", ROOT / "integration/api/contract.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_contract()
SPEC = CONTRACT.load_spec()


def contract_errors(value, name):
    return CONTRACT.validate(value, {"$ref": f"#/components/schemas/{name}"}, SPEC)


def groq_ok(content, *, total=400, finish="stop"):
    body = {
        "model": "openai/gpt-oss-120b",
        "choices": [{"index": 0, "message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": total - 100, "completion_tokens": 100, "total_tokens": total},
    }
    return HttpResponse(200, {}, json.dumps(body).encode())


def gemini_ok(content):
    body = {
        "candidates": [{"content": {"parts": [{"text": content}]}, "finishReason": "STOP"}],
        "usageMetadata": {
            "promptTokenCount": 80,
            "candidatesTokenCount": 20,
            "totalTokenCount": 100,
        },
        "modelVersion": "gemini-3.6-flash",
    }
    return HttpResponse(200, {}, json.dumps(body).encode())


def groq_error(status, *, code=None, type_="invalid_request_error", message="details"):
    error = {"message": message, "type": type_}
    if code is not None:
        error["code"] = code
    return HttpResponse(status, {}, json.dumps({"error": error}).encode())


class Recording:
    """Answers each request through ``answer(url, body)``; records every request."""

    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    def post(self, url, headers, body, timeout):
        decoded = json.loads(body)
        self.requests.append({"url": url, "headers": dict(headers), "body": decoded})
        return self.answer(url, decoded)


def user_payload(body):
    if "messages" in body:
        return json.loads(body["messages"][1]["content"])
    return json.loads(body["contents"][0]["parts"][0]["text"])


def search_for(case_id, corpus):
    from ai.tools.evidence_search import search_case_evidence
    from ai.workflows import case_briefing as cb

    return search_case_evidence(
        corpus,
        actor={
            "actor_id": "auditor-1",
            "tenant_id": TENANT,
            "roles": ["auditor"],
            "authenticated": True,
        },
        tenant_id=TENANT,
        case_id=case_id,
        questions=list(cb.DEFAULT_QUESTIONS),
    )


@unittest.skipUnless(HAS_LANGGRAPH, "LangGraph not installed; runs in the CI ai-smoke job")
class SectionDraftingTests(unittest.TestCase):
    def setUp(self):
        from ai.models import briefing
        from ai.tools.evidence_search import synthetic_corpus_from_records
        from ai.workflows import case_briefing as cb

        self.cb = cb
        self.briefing = briefing
        self.corpus = synthetic_corpus_from_records(list(RECORDS.values()), TENANT)
        self.request = cb.build_request(search_for("SYN-002", self.corpus))
        self.template = cb.DeterministicBriefingModel().draft(self.request)["sections"]

    def section_echo(self, overrides=None):
        """Answers each section with the template's section unless overridden."""
        overrides = overrides or {}

        def answer(url, body):
            section = user_payload(body)["section"]
            if section in overrides:
                item = overrides[section]
                return item(body) if callable(item) else item
            return groq_ok(json.dumps({"sections": {section: self.template[section]}}))

        return Recording(answer)

    def draft(self, transport):
        model = get_model("reasoning", settings=settings(HOSTED), transport=transport)
        return model, model.draft(self.request), model.last_provenance

    def test_each_section_is_one_bounded_call_within_groq_per_minute_limits(self):
        transport = self.section_echo()
        model, draft, provenance = self.draft(transport)
        called = [s for s in self.cb.SECTIONS if self.template[s]]
        self.assertEqual([user_payload(r["body"])["section"] for r in transport.requests], called)
        for request in transport.requests:
            body = request["body"]
            with self.subTest(section=user_payload(body)["section"]):
                budget = body["max_completion_tokens"]
                self.assertLessEqual(budget, self.briefing.SECTION_MAX_OUTPUT_TOKENS)
                self.assertEqual(body["reasoning_effort"], "low")
                chars = sum(len(m["content"]) for m in body["messages"])
                # One section call (input + output budget) fits well inside 8,000 tokens/min.
                self.assertLess(chars // 4 + budget, GROQ_TOKENS_PER_MINUTE // 2)
        self.assertEqual(draft, {"sections": self.template})
        self.assertEqual(provenance["fallback_level_name"], "hosted")
        self.assertFalse(provenance["fell_back"])
        self.assertEqual(provenance["section_fallbacks"], [])
        self.assertEqual(provenance["prompt_version"], self.cb.PROMPT_VERSION)

    def test_truncated_section_falls_back_alone_and_is_recorded(self):
        transport = self.section_echo(
            {"what_happened": groq_ok('{"sections": {"what_happ', finish="length")}
        )
        _, draft, provenance = self.draft(transport)
        self.assertEqual(draft["sections"], self.template)  # template text for that section
        self.assertEqual(provenance["section_fallbacks"], ["what_happened"])
        record = provenance["sections"]["what_happened"]
        self.assertEqual(record["fallback_level_name"], "template")
        first = record["attempts"][0]
        self.assertEqual(
            (first["outcome"], first["http_status"], first["error_code"]),
            ("truncated_output", 200, "length"),
        )
        for section in ("what_was_checked", "next_action"):
            self.assertEqual(provenance["sections"][section]["fallback_level_name"], "hosted")
        # The disclosure never hides model-written text: hosted, but fell back somewhere.
        self.assertEqual(provenance["fallback_level_name"], "hosted")
        self.assertTrue(provenance["fell_back"])

    def test_each_rejected_section_falls_back_individually_with_its_reason(self):
        citation = self.template["what_happened"][0]["refs"][0]["citation"]
        checked = copy.deepcopy(self.template["what_was_checked"][:-1])  # one search dropped
        next_action = [
            {**copy.deepcopy(s), "action": "specialist_review"}
            for s in self.template["next_action"]
        ]
        cases = {
            "what_happened": groq_ok(
                json.dumps(
                    {
                        "sections": {
                            "what_happened": [
                                {
                                    "kind": "observed_fact",
                                    "text": "The invoices show fraud by the supplier.",
                                    "refs": [{"citation": citation}],
                                }
                            ]
                        }
                    }
                )
            ),
            "what_was_checked": groq_ok(json.dumps({"sections": {"what_was_checked": checked}})),
            "what_is_explained": groq_ok(json.dumps({"sections": {"what_is_explained": []}})),
            "what_is_unresolved": groq_ok(json.dumps({"sections": self.template})),
            "next_action": groq_ok(json.dumps({"sections": {"next_action": next_action}})),
        }
        _, draft, provenance = self.draft(self.section_echo(cases))
        self.assertEqual(draft["sections"], self.template)
        reasons = {
            name: record["attempts"][0].get("validation")
            for name, record in provenance["sections"].items()
            if record["called"]
        }
        self.assertEqual(
            reasons,
            {
                "what_happened": "rejected:conclusion_not_allowed",
                "what_was_checked": "required_not_covered",
                "what_is_explained": "section_empty",
                "what_is_unresolved": "wrong_section",
                "next_action": "actions_changed",
            },
        )
        self.assertEqual(provenance["fallback_level_name"], "template")
        self.assertEqual(provenance["section_fallbacks"], [s for s in reasons])

    def test_flagged_document_text_is_never_sent_to_the_briefing_model(self):
        from ai.evaluation.agent_eval import _fill

        marker = FIXTURES["injection_marker"]
        base = next(d for d in self.corpus if d["case_id"] == "SYN-002")
        items = _fill(
            FIXTURES["case_briefing"]["document_injection"]["items"],
            {"injection": FIXTURES["injection_text"]},
        )
        hostile = {
            **copy.deepcopy(base),
            "evidence_id": "00000110-0000-4000-8000-000000000001",
            "document_sha256": "b" * 64,
            "document_type": "financial_statement",
            "items": items,
        }
        self.request = self.cb.build_request(search_for("SYN-002", [*self.corpus, hostile]))
        self.template = self.cb.DeterministicBriefingModel().draft(self.request)["sections"]
        self.assertTrue(any(e["instruction_like"] for e in self.request["untrusted_evidence"]))
        transport = self.section_echo()
        _, draft, provenance = self.draft(transport)
        for request in transport.requests:
            self.assertNotIn(marker, json.dumps(request["body"]))
        unresolved = provenance["sections"]["what_is_unresolved"]
        self.assertEqual(unresolved["fallback_level_name"], "hosted")
        self.assertIn(
            "untrusted_content", {s["kind"] for s in draft["sections"]["what_is_unresolved"]}
        )
        self.assertNotIn(marker, json.dumps(draft))

    def test_graph_record_has_contract_provenance_and_per_section_record(self):
        transport = self.section_echo(
            {"what_is_explained": groq_error(400, code="json_validate_failed")}
        )
        model = get_model("reasoning", settings=settings(HOSTED), transport=transport)
        source = lambda tenant_id, case_id: list(self.corpus)  # noqa: E731
        tool = self.cb.make_evidence_search_tool(
            source,
            actor={
                "actor_id": "auditor-1",
                "tenant_id": TENANT,
                "roles": ["auditor"],
                "authenticated": True,
            },
            questions=list(self.cb.DEFAULT_QUESTIONS),
        )
        from ai.workflows.fake_graph import create_investigation_state

        graph = self.cb.build_briefing_graph(tool, model, evidence_source=source)
        state = create_investigation_state(
            tenant_id=TENANT, case_id="SYN-002", run_id="r", max_steps=1
        )
        (briefing,) = graph.invoke(state)["findings"]
        self.assertEqual(briefing["rejected_statements"], [])
        self.assertEqual(contract_errors(briefing["analysis_provenance"], "AnalysisProvenance"), [])
        self.assertEqual(contract_errors(briefing["disclosure"], "AiDisclosure"), [])
        self.assertEqual(CONTRACT._disclosure_mismatches(briefing, "$"), [])
        self.assertEqual(briefing["disclosure"]["source"], "hosted_model")
        self.assertTrue(briefing["disclosure"]["fell_back"])
        provenance = briefing["model"]["config"]["provenance"]
        self.assertEqual(provenance["section_fallbacks"], ["what_is_explained"])
        failed = provenance["sections"]["what_is_explained"]["attempts"][0]
        self.assertEqual(
            (failed["outcome"], failed["http_status"], failed["error_code"]),
            ("http_error", 400, "json_validate_failed"),
        )

    def test_mock_chain_drafts_like_the_template(self):
        model = get_model("reasoning", offline=True)
        draft = model.draft(self.request)
        self.assertEqual(draft, {"sections": self.template})
        self.assertEqual(model.last_provenance["fallback_level_name"], "mock")
        skipped = [s for s, r in model.last_provenance["sections"].items() if not r["called"]]
        self.assertEqual(skipped, [s for s in self.cb.SECTIONS if not self.template[s]])


class ErrorRecordingTests(unittest.TestCase):
    def complete(self, response, *, role="reasoning"):
        from ai.models import CompletionTask

        task = CompletionTask(
            name="t",
            prompt_version="v1",
            instructions='Reply with {"status": "ok"}.',
            payload={"synthetic": True},
            template=lambda _p: {"status": "ok"},
        )

        def answer(url, body):
            if isinstance(response, BaseException):
                raise response
            return response

        transport = Recording(answer)
        model = get_model(role, settings=settings(HOSTED), transport=transport)
        return model.complete(task).provenance

    def test_http_status_and_provider_code_are_recorded_without_the_message(self):
        provenance = self.complete(
            groq_error(400, code="json_validate_failed", message=f"leaked {GROQ_KEY} text")
        )
        attempt = provenance["attempts"][0]
        self.assertEqual(
            (attempt["outcome"], attempt["http_status"], attempt["error_code"]),
            ("http_error", 400, "json_validate_failed"),
        )
        text = json.dumps(provenance)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("leaked", text)

    def test_gemini_status_rate_limit_and_connection_failures_are_coded(self):
        gemini_429 = HttpResponse(
            429,
            {"retry-after": "12"},
            json.dumps(
                {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}
            ).encode(),
        )
        cases = {
            "gemini_429": (
                self.complete(gemini_429, role="document"),
                "rate_limited",
                429,
                "RESOURCE_EXHAUSTED",
            ),
            "groq_type_only": (
                self.complete(groq_error(503, type_="service_unavailable")),
                "http_error",
                503,
                "service_unavailable",
            ),
            "unparseable_body": (
                self.complete(HttpResponse(502, {}, b"<html>bad gateway</html>")),
                "http_error",
                502,
                None,
            ),
            "connection": (
                self.complete(TransportError("connection_failed")),
                "unavailable",
                None,
                "connection_failed",
            ),
        }
        for name, (provenance, outcome, status, code) in cases.items():
            with self.subTest(case=name):
                attempt = provenance["attempts"][0]
                self.assertEqual(
                    (attempt["outcome"], attempt["http_status"], attempt["error_code"]),
                    (outcome, status, code),
                )
                self.assertEqual(provenance["fallback_level_name"], "template")
                self.assertNotIn("SECRET", json.dumps(provenance))

    def test_a_code_that_carries_key_material_is_dropped(self):
        body = lambda code: json.dumps({"error": {"code": code}}).encode()  # noqa: E731
        self.assertIsNone(error_code(body(GROQ_KEY), [f"Bearer {GROQ_KEY}"]))
        self.assertIsNone(error_code(body("gsk_otherlookingkey123"), []))
        self.assertIsNone(error_code(body("SECRET_groq_0123456789"), [f"Bearer {GROQ_KEY}"]))
        self.assertIsNone(error_code(body("has spaces in it"), []))
        self.assertIsNone(error_code(body("x" * 80), []))
        self.assertEqual(
            error_code(body("rate_limit_exceeded"), [f"Bearer {GROQ_KEY}"]), "rate_limit_exceeded"
        )
        provenance = self.complete(groq_error(401, code=GROQ_KEY))
        self.assertIsNone(provenance["attempts"][0]["error_code"])
        self.assertNotIn("SECRET", json.dumps(provenance))

    def test_reasoning_effort_is_sent_only_to_gpt_oss_models(self):
        from ai.models.config import Secret
        from ai.models.providers import GroqProvider

        seen = []
        transport = Recording(lambda url, body: seen.append(body) or groq_ok('{"a": 1}'))
        from ai.models.providers import Prompt

        prompt = Prompt("i", {"x": 1}, 64, 5.0)
        GroqProvider("openai/gpt-oss-20b", Secret("k"), transport).complete(prompt)
        GroqProvider("llama-3.3-70b-versatile", Secret("k"), transport).complete(prompt)
        self.assertEqual(seen[0]["reasoning_effort"], "low")
        self.assertNotIn("reasoning_effort", seen[1])


class PacerTests(unittest.TestCase):
    def setUp(self):
        from ai.evaluation import agent_eval as ae

        self.ae = ae
        self.now = [0.0]
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now[0] += seconds

    def live(self, factory):
        return self.ae.LiveModels(factory, clock=lambda: self.now[0], sleep=self.sleep)

    def test_failed_calls_without_usage_are_charged_their_request_estimate(self):
        failed = {
            "attempts": [
                {
                    "name": "hosted",
                    "outcome": "http_error",
                    "http_status": 400,
                    "latency_ms": 900,
                    "tokens": None,
                    "request_chars": 8000,
                    "max_output_tokens": 1536,
                },
                {"name": "template", "outcome": "ok", "latency_ms": 0},
            ],
            "tokens": None,
        }
        self.assertEqual(self.ae.charged_tokens(failed), 8000 // 4 + 1536)
        refused = {"attempts": [{"name": "hosted", "outcome": "call_budget_exhausted"}]}
        self.assertEqual(self.ae.charged_tokens(refused), 0)
        reported = {"attempts": [{"name": "hosted", "latency_ms": 5, "tokens": {"total": 4726}}]}
        self.assertEqual(self.ae.charged_tokens(reported), 4726)

    def test_a_rate_limited_role_is_held_until_the_reset(self):
        live = self.live(lambda role: get_model(role, offline=True))
        live.wait("reasoning", 100)
        live.record(
            "reasoning",
            {
                "attempts": [{"name": "hosted", "outcome": "rate_limited", "latency_ms": 5}],
                "rate_limit": {"retry-after": "12", "x-ratelimit-reset-tokens": "7.5s"},
            },
        )
        before = self.now[0]
        live.wait("reasoning", 100)
        self.assertGreaterEqual(self.now[0] - before, 12.0)
        self.assertEqual(self.ae.parse_duration("2m59.56s"), 179.56)
        self.assertAlmostEqual(self.ae.parse_duration("120ms"), 0.12)
        self.assertIsNone(self.ae.parse_duration("soon"))

    @unittest.skipUnless(HAS_LANGGRAPH, "LangGraph not installed; runs in the CI ai-smoke job")
    def test_paced_run_with_failing_calls_never_trips_the_providers_minute_limit(self):
        """A fake Groq enforces 8,000 tokens/min, charging every request (failed ones
        too) its input plus requested output. Half the calls fail with HTTP 400 and
        report no usage. The paced harness must never receive an HTTP 429."""
        from ai.tools.evidence_search import synthetic_corpus_from_records
        from ai.workflows import case_briefing as cb
        from ai.workflows.interpretation import interpret_evidence

        clock = lambda: self.now[0]  # noqa: E731
        windows = {}
        statuses = []
        counter = [0]

        def answer(url, body):
            model = body["model"]
            charge = (
                sum(len(m["content"]) for m in body["messages"]) // 4
                + body["max_completion_tokens"]
            )
            window = [(t, n) for t, n in windows.get(model, []) if clock() - t < 60]
            if sum(n for _, n in window) + charge > GROQ_TOKENS_PER_MINUTE:
                statuses.append(429)
                windows[model] = window
                return HttpResponse(
                    429, {"retry-after": "20"}, b'{"error": {"code": "rate_limit_exceeded"}}'
                )
            windows[model] = [*window, (clock(), charge)]
            counter[0] += 1
            if counter[0] % 2:
                statuses.append(400)
                return groq_error(400, code="json_validate_failed")
            statuses.append(200)
            return groq_ok("{}", total=charge)

        transport = Recording(answer)
        live = self.live(
            lambda role: get_model(role, settings=settings(HOSTED), transport=transport)
        )
        corpus = synthetic_corpus_from_records(list(RECORDS.values()), TENANT)
        for _ in range(2):
            for case_id in sorted(RECORDS):
                search = search_for(case_id, corpus)
                live("reasoning").draft(cb.build_request(search))
                interpret_evidence(live, search)
        self.assertGreater(len(statuses), 40)
        self.assertNotIn(429, statuses)
        self.assertIn(400, statuses)
        self.assertGreater(sum(self.slept), 60.0)  # the pacer had to wait
        summary = self.ae.summarize_live_calls(live.calls)
        self.assertEqual(summary["calls"], len(live.calls))
        self.assertTrue(
            any(
                "code=json_validate_failed" in k
                for t in summary["tasks"].values()
                for k in t["first_attempt"]
            )
        )

    def test_section_calls_go_through_the_pacer_one_by_one(self):
        live = self.live(lambda role: get_model(role, offline=True))
        waits = []
        original = live.wait
        live.wait = lambda role, estimate=0: waits.append(estimate) or original(role, estimate)
        if not HAS_LANGGRAPH:
            self.skipTest("LangGraph not installed")
        from ai.tools.evidence_search import synthetic_corpus_from_records
        from ai.workflows import case_briefing as cb

        corpus = synthetic_corpus_from_records(list(RECORDS.values()), TENANT)
        request = cb.build_request(search_for("SYN-001", corpus))
        model = live("reasoning")
        model.draft(request)
        sections = [
            s
            for s, items in cb.DeterministicBriefingModel().draft(request)["sections"].items()
            if items
        ]
        self.assertEqual(len(waits), len(sections))
        self.assertEqual([c["task"] for c in live.calls], [f"case_briefing_{s}" for s in sections])
        self.assertTrue(all(w > 0 for w in waits))


@unittest.skipUnless(HAS_LANGGRAPH, "LangGraph not installed; runs in the CI ai-smoke job")
class EvidenceRoleTests(unittest.TestCase):
    def setUp(self):
        from ai.tools.evidence_search import synthetic_corpus_from_records

        self.corpus = synthetic_corpus_from_records(list(RECORDS.values()), TENANT)

    def test_short_evidence_uses_the_fast_role_with_a_bounded_output(self):
        from ai.workflows.interpretation import (
            EVIDENCE_OUTPUT_TOKENS,
            LONG_DOCUMENT_CHARS,
            evidence_role,
            interpret_evidence,
        )

        transport = Recording(
            lambda url, body: groq_ok("{}") if "groq.com" in url else gemini_ok("{}")
        )
        models = lambda role: get_model(role, settings=settings(HOSTED), transport=transport)  # noqa: E731
        short = search_for("SYN-002", self.corpus)
        reading = interpret_evidence(models, short)
        self.assertEqual(reading["role"], "fast")
        body = transport.requests[-1]["body"]
        self.assertEqual(body["model"], "openai/gpt-oss-20b")
        self.assertEqual(body["max_completion_tokens"], EVIDENCE_OUTPUT_TOKENS["fast"])
        self.assertLess(EVIDENCE_OUTPUT_TOKENS["fast"], EVIDENCE_OUTPUT_TOKENS["document"])
        self.assertIn("at most 5 statements", body["messages"][0]["content"])

        long = copy.deepcopy(short)
        first = next(iter(long["hits"]))
        long["hits"][first]["excerpt"] = "Contract clause text. " * (LONG_DOCUMENT_CHARS // 20)
        reading = interpret_evidence(models, long)
        self.assertEqual(reading["role"], "document")
        body = transport.requests[-1]["body"]
        self.assertIn("generativelanguage", transport.requests[-1]["url"])
        self.assertEqual(
            body["generationConfig"]["maxOutputTokens"], EVIDENCE_OUTPUT_TOKENS["document"]
        )

        exactly = [{"untrusted_text": "x" * LONG_DOCUMENT_CHARS}]
        self.assertEqual(evidence_role(exactly), "fast")
        self.assertEqual(
            evidence_role([{"untrusted_text": "x" * (LONG_DOCUMENT_CHARS + 1)}]), "document"
        )


class DeterminismScoringTests(unittest.TestCase):
    def test_model_written_briefing_text_is_excluded_only_for_hosted_or_local(self):
        from ai.evaluation.agent_eval import _briefing_content

        briefing = {
            "sections": {"what_happened": [{"text": "a"}]},
            "rejected_statements": [],
            "evidence_ids": ["e"],
            "search_digest": "d",
            "analysis_provenance": {"fallback_level_name": "hosted"},
        }
        self.assertEqual(_briefing_content(briefing), {"search_digest": "d"})
        for level in ("mock", "template"):
            briefing["analysis_provenance"] = {"fallback_level_name": level}
            self.assertIn("sections", _briefing_content(briefing))


if __name__ == "__main__":
    unittest.main()
