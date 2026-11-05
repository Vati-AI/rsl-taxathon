# ADR-031: AI model providers, routing and fallback

Status: accepted (approved by Keletso, 2026-11-05; document model confirmed as `gemini-3.6-flash`)
Owner and task: Gupta (AI area). Implemented by TAX-089 (router), TAX-097 (investigation agents), TAX-098/TAX-099 (taxpayer assistant). Review: Keletso (contract), Leseli (evaluation), Kananelo and Leseli (disclosure in the UIs).
Date: 2026-11-05

Context and smallest prototype need: The team agreed on an agentic architecture in which LangGraph agents use hosted language models for interpretation, while deterministic Python produces every figure. The repository recorded none of this. ADR-021 rejected a hosted model "for now" because no provider was approved, and TAX-089 planned a local model as the only model path. Every agent already drafts through a model port (for example `BriefingModel` in `ai/workflows/case_briefing.py`) whose output is validated whatever produced it, and TAX-056 (`python -m ai.evaluation.agent_eval --check`) gates every model or prompt change. This ADR approves the providers that fill those ports for the synthetic-data demo.

Decision:

- **Roles, not providers, in agent code.** Agents call `get_model("reasoning" | "fast" | "document")`, which returns an object that satisfies the existing model ports. Graphs never import a provider SDK or read a key. The role-to-model mapping and credentials come from the ignored `.env` file. Changing provider means changing configuration, not a graph.

  | Role | Used for | Primary (demo) |
  |---|---|---|
  | `reasoning` | Investigation reasoning, hypotheses and alternatives, related-party analysis, case briefings | Groq `openai/gpt-oss-120b` |
  | `fast` | Classification, short summaries, evidence categorisation, routine tool selection, taxpayer explanations | Groq `openai/gpt-oss-20b` |
  | `document` | Long contracts, financial statements, reasoning over Docling output | Google `gemini-3.6-flash` |

- **Fallback chain, per call:** configured hosted model → optional local open-weights model (Ollama) → deterministic template. A rate limit, timeout, refusal, invalid structured output or failed validation moves to the next level. The level used is recorded on the output and shown in the UI. A failure is never hidden, and a fallback never invents a finding.
- **`mock` is the default provider.** With no keys configured, every role returns deterministic fixture responses, so CI, `agent_eval --check` and ordinary development need no network and no keys. Live provider tests are opt-in, skipped without keys, and record model ID, settings, latency and token use.
- **Deterministic Python is mandatory** for VAT and refund calculations, reconciliation, thresholds, rules, exact comparisons and permissions. Models reason over those validated results and the cited evidence. They never produce a figure, a legal conclusion, an assessment or a decision.
- **Docling** remains the document parser, reached through the Vati API / TAX-038 adapter. Agents do not call it directly.
- **Provenance on every output:** provider, model ID, role, prompt version, settings, fallback level, latency and token use, plus the validation result. This extends the existing `model`/`prompt_version` record and is carried through the API contract (TAX-096).
- **Two permission contexts.** Investigation agents act for an authenticated RSL officer. The taxpayer assistant acts for one taxpayer and sees only that taxpayer's submissions, documents, validation results and taxpayer-facing RSL requests. It never sees risk scores, internal notes, other taxpayers' records or investigation findings. Both use the same router, but with separate tool allowlists.
- **Data rule:** only synthetic data may be sent to these free tiers (see sources). This is a prototype choice, not the RSL production hosting model. Production needs approved enterprise or private inference under a data-processing agreement, decided in a separate ADR.

Alternatives and tradeoff: A local model as the only path (TAX-089 as first written) keeps data on the laptop but gives weaker reasoning and depends on the demo laptop's hardware. It remains the second fallback level. A single provider for every role is simpler, but Groq's free per-minute token budget is small for long documents, while Gemini has a long context window. Calling providers directly from graphs was rejected because it couples workflows to vendors and makes keyless tests impossible.

Official sources and version/date checked (2026-11-05):

- Groq models (console.groq.com/docs/models): `openai/gpt-oss-120b` and `openai/gpt-oss-20b` are Production, with a 131,072-token context and 65,536 max completion tokens. The deprecations page (console.groq.com/docs/deprecations) lists neither as deprecated. Both are the recommended replacements for retired models.
- Groq rate limits (console.groq.com/docs/rate-limits): both models are published at 30 requests/min, 1,000 requests/day, 8,000 tokens/min and 200,000 tokens/day. The page labels these "base limits for the Developer plan", and an organisation's actual limits are shown at console.groq.com/settings/limits, so TAX-089 must record the account's observed limits. 8,000 tokens/min is tight for long reasoning prompts, so prompts need budgets and the fallback must handle HTTP 429.
- Groq data (console.groq.com/docs/your-data): "By default, Groq does not retain customer data for inference requests". Inputs and outputs may be logged for up to 30 days for reliability or abuse investigation, and this can be disabled with Zero Data Retention in Data Controls. That page does not state the training policy, so it is treated as unverified. Enable ZDR on the team's demo organisation.
- Google models (aistudio.google.com/docs/models): `gemini-2.5-flash` is **deprecated**. It is no longer selectable in AI Studio, API access is limited to existing users, and from 2026-12-01 requests are routed to `gemini-3.5-flash-lite`. Listed stable Flash models: `gemini-3.6-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`. `gemini-3.8-flash` is marked New. The page does not show token limits.
- Google rate limits (aistudio.google.com/docs/rate-limits): free-tier limits are not published per model. They are shown per project at aistudio.google.com/rate-limit and must be recorded from there.
- Gemini API Additional Terms, effective 2026-03-23 (aistudio.google.com/docs/terms): for unpaid services, Google uses submitted content and responses "to provide, improve, and develop" its products, "human reviewers may read, annotate, and process your API input and output", and "Do not submit sensitive, confidential, or personal information to the Unpaid Services." Paid services do not use prompts for product improvement and fall under a Data Processing Addendum.

Blockers:

- **Gemini 2.5 Flash cannot be the `document` model.** New projects cannot use it, and it is rerouted on 2026-12-01. Following the team rule (record, don't swap silently), the configuration ships with no default document model ID. Resolved 2026-11-05: Keletso confirmed `gemini-3.6-flash`, the stable previous-generation Flash. TAX-089 must verify its context window and the project's observed free-tier limits before relying on it; until verified, the `document` role falls back down the chain.

Verification performed (or named implementation task): Documentation only. TAX-089 implements the router, mock provider, adapters and opt-in live smoke, and records observed limits. TAX-097 enables routed models in the investigation agents and must keep `agent_eval --check` passing in mock mode and over three recorded live runs per scenario. TAX-098/TAX-099/TAX-102 do the same for the taxpayer assistant, including leakage tests. No live call has been made for this ADR.

Consequences and revisit trigger: ADR-021's "no hosted model" alternative is superseded for synthetic data only. Its validation, fencing and citation rules apply unchanged to model output. TAX-089 changes from "local model only" to "router with hosted primary, local and template fallbacks". Revisit if a model is deprecated, free limits change, a provider's data terms change, the demo cannot complete within limits, or real taxpayer data is proposed (that requires a new ADR and an approved data-processing arrangement).
