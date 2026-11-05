# V1 AI implementation and evaluation

Gupta implements; Leseli owns independent evaluation fixtures (reassigned from Yellow). LangGraph coordinates bounded investigative steps, not final tax/enforcement decisions. Use typed state and tool inputs/outputs. Keep pure calculations/rules deterministic; use models for tasks that require interpretation. Models are reached only through `get_model("reasoning" | "fast" | "document")`, with `mock` as the default, a fallback from hosted to local to template, and synthetic data only on free tiers. See [ARCHITECTURE.md § AI layer](ARCHITECTURE.md#ai-layer) and [ADR-031](decisions/ADR-031-ai-model-providers.md).

Record workflow/prompt/model identifier and configuration, evidence IDs, tool outcomes and human review history per run. Document text and retrieved results are untrusted data, never permission to change tool policy. Tools use allowlisted operations with scoped credentials and finite time/call/token budgets; task authors specify those budgets and record actual cost/latency. No arbitrary shell, URL fetch or database access from model output.

Persist checkpoints for real workflows. Human resume must authenticate actor, case and proposed action version. Interrupted nodes can execute again: place side effects in idempotent operations and test replay. The in-memory smoke is only an installation check. Missing evidence or unverified law yields a gap, not an invented citation or forced conclusion.

## V1 release gates (project choices, not statistical certification)

The five golden synthetic scenarios must each reach the documented expected review outcome with traceable evidence. Zero cross-taxpayer access, unauthorized approvals or duplicate outward actions in the test matrix. Every reported factual claim must have a resolvable evidence locator or be explicitly marked inference/unverified. The legitimate-refund scenario must not be labelled proven abuse; the no-legal-route scenario must not recommend enforcement on fabricated authority.

TAX-024 defines independent expected fixtures before model tuning. TAX-056 runs injection, missing/conflicting evidence, unavailable tools and replay tests. For stochastic model paths run each scenario at least three times with recorded model/settings; report every failure and latency/cost, do not cherry-pick. Five scenarios cannot establish real-world accuracy. Do not set arbitrary national accuracy claims from the prototype.

Prompt/model changes rerun affected fixtures and the five-scenario evaluation before demo acceptance. Human reviewers decide whether evidence and proposed actions are appropriate. No automatic learning or model/risk promotion; expansion tasks cover reviewed calibration and rollback.
