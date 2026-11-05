# TAX-110 evidence: reliable hosted briefing and evidence reading within free-tier limits

Follow-up to the TAX-097 live run (`docs/evidence/TAX-097`), where 0 of 17 hosted briefings and
2 of 29 evidence readings reached the model. Design: ADR-039. Only synthetic data is used.

## What changed

| Problem in the TAX-097 run | Change |
|---|---|
| Whole briefing in one call; truncated at 2,048 output tokens (gpt-oss reasoning tokens count against the budget) | `RoutedModel.draft` drafts one section per call (`ai/models/briefing.py`), at most 1,536 output tokens each, with only the data that section needs. gpt-oss requests send `reasoning_effort: "low"`. A section whose template is empty is not sent. |
| One rejected statement discarded the whole briefing | Each section is validated on its own (`validate_draft` with nothing rejected, only that section, not empty, covering the template's searches, unresolved items and next actions) and falls back to the template's section alone. `model.config.provenance.sections` records each section; `section_fallbacks` lists the ones that fell back. |
| `http_error` attempts without status or code | Every failed attempt records `http_status` and `error_code`: the provider's code or type from a non-2xx body, a finish reason (`length`, `MAX_TOKENS`, refusal), or a transport code (`timeout`, `connection_failed`). Messages are never read; a code that is not a short token, looks like a key or overlaps a request header is dropped. |
| HTTP 429 caused by the harness's own pacing (failed calls charged 0 tokens) | The `--live` pacer charges each hosted attempt its reported tokens, or its input estimate plus requested output when no usage came back; waits for each call's own estimate; holds the role after a 429 until `retry-after` or the reset header; paces briefing sections one by one. |
| Evidence reading truncated or failed on `fast` | `fast` gets at most 1,024 output tokens and 5 statements; `document` (1,536 tokens, 8 statements) only for fenced text longer than `LONG_DOCUMENT_CHARS` (12,000). |
| Live briefing determinism could only pass when every briefing fell back | When a hosted or local model wrote a briefing, determinism is scored on its deterministic part (searches, digest, scope, flags); mock and template briefings are still compared in full. |

Unchanged: `validate_draft`, the interpretation validators, citation rules, the alternatives
rule, fencing of document text and the injection checks. Text flagged as instruction-like is
now not sent to the briefing model at all (only its citation ID and type, for
`what_is_unresolved`); the evidence reading still receives it fenced and flagged. Prompt version
`tax110-briefing-sections-v1`, evidence prompt `tax110-evidence-interpretation-v2`, router
`tax110-router-v2`, playbook registry re-pinned as `SYN-PBR-2026.11.3` (refund_lanes and
related_party, whose sources include `interpretation.py`).

## Tests (offline, fake transports)

`tests/ai/test_model_hosted_reliability.py`, 16 tests:

- Section drafting: each section is one call with `max_completion_tokens` <= 1,536,
  `reasoning_effort` low and input + output well under 8,000 tokens; a truncated section
  (`finish_reason: length`) falls back alone and records `truncated_output`, HTTP 200, code
  `length`; one rejection per section (`rejected:conclusion_not_allowed`,
  `required_not_covered`, `section_empty`, `wrong_section`, `actions_changed`) each falls back
  individually; flagged injection text never appears in any briefing request; the graph record's
  `AnalysisProvenance` and `AiDisclosure` are contract-valid with a mixed hosted/template
  briefing; the mock chain drafts exactly like the template.
- Error recording: Groq HTTP 400 code `json_validate_failed` (fake body) recorded without its
  message; Gemini 429 `RESOURCE_EXHAUSTED`; Groq 503 type-only body; unparseable 502 body (code
  `null`); connection failure (`connection_failed`, status `null`); a code equal to, resembling or
  overlapping the key is dropped; no key text in any provenance.
- Pacer: a failed call without usage is charged `request_chars / 4 + max_output_tokens`; a role
  is held for `retry-after` after a 429; Groq durations (`2m59.56s`, `120ms`) parse; a fake Groq
  that enforces 8,000 tokens/min per model and charges every request (failed ones too) never
  returns 429 over 10 paced briefings plus evidence readings in which half the calls fail with
  HTTP 400. With the TAX-097 accounting (reported tokens only) the same test receives repeated
  429s, so it catches the regression. Briefing sections are paced one call at a time.
- Role selection: short evidence goes to Groq `openai/gpt-oss-20b` with 1,024 output tokens and
  "at most 5 statements"; text longer than `LONG_DOCUMENT_CHARS` goes to Gemini with 1,536;
  exactly `LONG_DOCUMENT_CHARS` stays `fast`.
- Determinism scoring: model-written briefing fields are excluded only for hosted or local
  levels.

`tests/ai/test_model_router.py`: the hosted level-0 briefing test now answers per section and
checks one call per non-empty section and summed tokens.

## Local check results (2026-11-05, Windows, worktree `task/TAX-110`)

| Check | Result |
|---|---|
| `python -m unittest tests.ai.test_model_hosted_reliability` | 16 tests OK |
| `python -m unittest discover -s tests/ai -p "test_model*.py"` | 66 tests OK (1 skipped: opt-in live smoke) |
| `python -m unittest discover -s tests/ai -p "test_agent_eval*.py"` | 18 tests OK |
| `python -m unittest discover -s tests/ai` (every AI group `scripts/dev.py` runs) | 346 tests OK (1 skipped) |
| `python -m ai.evaluation.agent_eval --check` (offline mock) | 1000 checks, 0 failed, `gate: PASS` |
| `python -m ai.evaluation.risk_calibration --check` | `baseline: PASS` |
| `python ai/smoke.py`, `python -m ai.models.smoke` | pass (mock) |
| `tests/contracts`, `tests/integration`, `tests/data` | 129, 249, 12 tests OK |
| `scripts/validate.py`, `integration/api/contract.py` | OK (47 HTTP examples, 2 events) |
| `ruff check .` | all checks passed |
| `ruff format --check .` | only `tests/data/test_tax024_records.py` would be reformatted; it is unformatted on `origin/main` too and is not touched here |
| `mypy` (Docker `python:3.13-slim`, `requirements-dev.lock`) | no issues in 253 source files |

AI tests ran in a LangGraph venv (`ai/requirements.lock`); the rest in `.venv-dev`.

## Live run (criterion 4): pending

No `GROQ_API_KEY` or `GEMINI_API_KEY` was set (worktree `.env`, the main checkout's `.env`, the
process, user and machine environment were checked for presence only). No live run was made and
none is reported here. With the keys and `VATI_MODEL_PROVIDER=hosted` in the ignored `.env`, run
from the repository root (LangGraph venv, synthetic data only):

```sh
LANGSMITH_TRACING=false python -m ai.evaluation.agent_eval --live --json docs/evidence/TAX-110/live-eval-<date>.json
```

The report then holds every call (`live_model_calls`: scenario/run label, task per briefing
section, level used, attempts with `http_status`, `error_code`, tokens and latency) and a
`live_summary` per task (levels, first-attempt outcomes, median/max latency, charged tokens,
level-0 share, total pacer wait). The TAX-097 scoring fix (interpretation figures may come from
the deterministic tool output, such as `explained_share`) is part of the harness, so that run is
scored with it. Record the results here and check before committing that the JSON holds no key
material (for example `grep -c "gsk_\|AIza" <file>` gives 0).
