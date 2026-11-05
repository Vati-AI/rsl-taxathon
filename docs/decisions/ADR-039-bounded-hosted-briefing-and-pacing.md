# ADR-039: Section-by-section hosted briefing and failure-aware pacing

Status: accepted
Owner and task: Leseli; TAX-110 (follow-up to TAX-097, ADR-031)
Date: 2026-11-05

Context and smallest prototype need:
The TAX-097 live run (docs/evidence/TAX-097) sent the whole case briefing to Groq
`openai/gpt-oss-120b` in one call. A full SYN briefing is about 6,400 characters of JSON
(roughly 1,800 output tokens) and gpt-oss reasoning tokens count against the same
2,048-token output budget, so all 17 hosted briefings were truncated, failed with an HTTP error
whose status and code were not recorded, failed to connect or were rate limited.
The evidence reading (`fast`, `openai/gpt-oss-20b`) reached the model 2 times in 29. The live
harness paced only on reported tokens; failed calls reported none, so it caused its own HTTP
429s. The prototype needs the hosted briefing and evidence reading to succeed on the free tiers
(Groq 8,000 tokens/min and 30 requests/min per model) without weakening validation, fencing,
citations, alternatives or injection resistance.

Decision:
- **Draft the briefing one section per call** (`ai.models.briefing.draft_by_section`, used by
  `RoutedModel.draft`). Each call carries only the data its section needs, has an output
  budget of `SECTION_MAX_OUTPUT_TOKENS` (1,536), and is validated on its own: the section must
  pass `case_briefing.validate_draft` with no rejected statement, contain only that section,
  not be empty when the template has statements, and cover the references and actions the
  deterministic template requires (every search checked; every unresolved search, unsearchable
  document and flagged item; the same next actions in the same order). A failing section falls
  back to the template's section alone. A section whose template is empty is not sent. The
  graph still validates the merged draft against the verified search.
- **Record per section.** The merged provenance (`model.config.provenance`) keeps a record per
  section (`sections`) and the list of sections that fell back (`section_fallbacks`). The
  contract `AnalysisProvenance`/`AiDisclosure` report the most capable level any section used,
  with `fell_back` true when any section fell back, so a disclosure never hides model-written
  text.
- **Flagged document text is not sent to the briefing model at all.** Sections that read
  evidence receive only items not flagged as instruction-like; `what_is_unresolved` receives a
  flagged item's citation ID and type, never its text. The evidence reading still receives it,
  fenced and flagged, as before.
- **Low reasoning effort for gpt-oss.** Requests to `openai/gpt-oss-*` send
  `reasoning_effort: "low"` so reasoning tokens stay inside the per-call budget; it is
  recorded in the attempt settings.
- **Bounded evidence reading.** The `fast` role gets at most 1,024 output tokens and 5
  statements; only fenced text longer than `LONG_DOCUMENT_CHARS` goes to `document` (1,536
  tokens, 8 statements).
- **Every failed attempt records `http_status` and `error_code`.** The code is the provider's
  machine-readable code from a non-2xx body (Groq `error.code`/`error.type`, Gemini
  `error.status`), a finish reason for 2xx failures (`length`, `MAX_TOKENS`, refusal reasons)
  or a fixed transport code (`timeout`, `connection_failed`). Messages are never read. A code
  is dropped if it is not a short `[A-Za-z0-9_.-]` token, looks like a credential or overlaps a
  request header value. The contract attempt shape is unchanged (codes stay in the router
  record and the live call log).
- **The live pacer charges failed calls.** Each hosted attempt is charged its reported tokens,
  or, without usage, its input estimate (characters / 4) plus the requested output budget. A
  call waits until its own estimate fits the 6,500 tokens/min window, and after an HTTP 429 the
  role is held until `retry-after` or Groq's reset header (at most 61 s). Briefing sections are
  paced and recorded one by one.

Alternatives and tradeoff:
- Raising the output budget to 8,192 for one whole-briefing call: a single call would then
  reserve most of Groq's 8,000 tokens/min, any truncation or JSON failure still loses the whole
  briefing, and one bad statement would still discard all five sections.
- Streaming or continuation of truncated JSON: more provider-specific code and no help with
  validation granularity.
- Sending fewer sections to a model (for example only `what_happened`): fewer calls, but the
  checked, unresolved and next-action wording would always be template text. Coverage rules
  give the same guarantees with model wording.
- Section-by-section costs five calls per briefing instead of one (about 6,000 to 7,000
  charged tokens for a SYN briefing instead of one call that failed), so a paced live run of
  3 x SYN-001..SYN-005 takes longer.

Official sources and version/date checked:
- Groq rate limits and models as recorded in ADR-031 (console.groq.com/docs/rate-limits,
  console.groq.com/docs/models, checked 2026-11-05).
- console.groq.com/docs/reasoning (read 2026-11-05): `openai/gpt-oss-20b` and
  `openai/gpt-oss-120b` accept `reasoning_effort` `low` ("the model will use a small number of
  reasoning tokens"), `medium` or `high`. The page does not say whether reasoning tokens count
  against `max_completion_tokens`; the TAX-097 run (output tokens reported above the visible
  JSON) suggests they do.
- console.groq.com/docs/structured-outputs (read 2026-11-05): JSON object mode "will either
  return valid JSON or throw an error"; no status or code is named there.
- console.groq.com/docs/errors (read 2026-11-05): the error body is
  `{"error": {"message", "type"}}`; no `code` field is documented. The adapter therefore takes
  `code` when present and otherwise `type`.
- Gemini error bodies (`{"error": {"code", "message", "status"}}`) were not re-checked here.
None of these was confirmed live in this task (no keys were configured); the live run in
docs/evidence/TAX-110 must record the status and codes actually returned.

Verification performed (or named implementation task):
`tests/ai/test_hosted_reliability.py` (fake transports): bounded section calls inside the
per-minute limit, truncation and each rejection class falling back per section, flagged text
never sent, contract-valid provenance and disclosure, HTTP status and code recording without
key material, failure-aware pacing against a fake provider that enforces 8,000 tokens/min
(no HTTP 429; the same test fails with the TAX-097 accounting), and role selection by document
length. `agent_eval --check` passes offline. The recorded live run (criterion 4 of TAX-110) is
pending until Groq and Gemini keys are configured.

Consequences and revisit trigger:
The briefing prompt version is `tax110-briefing-sections-v1` and the router version
`tax110-router-v2`; the playbook registry is re-pinned (SYN-PBR-2026.11.3). Revisit when the
live run shows a section still truncating or failing coverage in most calls, when Groq or
Gemini limits change, or when production inference (a separate ADR) replaces the free tiers.
