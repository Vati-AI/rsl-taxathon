# ai

Owner: Gupta. See `tasks/README.md` for work and `docs/ARCHITECTURE.md` for boundaries.

TAX-022 provides typed investigation state, case-scoped tool result envelopes, and a deterministic fake-tool LangGraph. It makes no live service calls and performs no tax assessment or enforcement action.

For the model-free graph, construct state with `create_investigation_state(...)` and invoke `build_fake_tool_graph()`. The state factory requires tenant, case and run IDs and limits each run to at most eight tool calls. Tool failures are returned in the error envelope and stop execution for human review.
