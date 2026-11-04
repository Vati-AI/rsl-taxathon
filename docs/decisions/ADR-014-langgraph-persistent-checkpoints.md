# ADR-014: Persistent LangGraph checkpoints and authenticated human resume

Status: proposed (TAX-047, pending review)
Owner and task: Gupta (owner), TAX-047; implemented by claude-gupta-20261104-tax047. Cross-boundary review: Keletso (integration), Leseli (TAX-046 decisions).
Date: 2026-11-04

(ADR-012 is reserved for TAX-036.)

Context and smallest prototype need: docs/AI_STANDARD.md requires persisted checkpoints for real workflows, human resume that authenticates the actor, case and proposed action version, and replay-safe side effects. The locked AI environment had only LangGraph's in-memory checkpointer (`ai/smoke.py`), which loses a paused run when the worker restarts. ADR-013 (TAX-046) makes a supervisor-approved `Vati Decision` the only authority for a consequential action. It says agent drafts are never approved or executed, and TAX-047 resumes LangGraph after an approval.

Decision:

- Persist run state with the official `langgraph-checkpoint-sqlite` `SqliteSaver` on a file path supplied explicitly by the caller (`open_checkpointer`). There is no in-memory or environment-variable fallback. Each thread is scoped to tenant, case and run (`thread_config`).
- `ai/workflows/human_checkpoints.py` compiles `draft_action -> await_approval -> apply_approved_action`. The draft stores the action kind (TAX-046 outcome allowlist only), case version, payload, the payload's canonical SHA-256 (same JSON encoding as TAX-046 `digest`) and the agent identity. `await_approval` calls `interrupt()` before anything leaves the worker.
- Resume (`resume_with_approval`) takes the approved decision and the actor as authenticated by the Vati session layer. It checks them against the persisted draft before invoking the graph, so a refused attempt does not touch the checkpoint. It is refused unless all of the following hold:
  - the actor is authenticated, in the case's tenant, and holds the `supervisor` role;
  - the actor is the decision's `approved_by`, and that is neither the officer proposer nor the agent identity;
  - the decision is `approved` for the same tenant, case, draft (`draft_id`) and outcome;
  - the decision's `case_version` and `payload_digest` equal the draft's.

  Each refusal has a stable code. The node repeats the same check, so a direct `Command(resume=...)` that skips the wrapper just pauses again.
- The approved action goes through an injected `ApprovedActionPort`. Its idempotency key is derived from tenant, case, run, draft, decision and payload digest. If the node runs again after a crash, the port receives the same key and the action is applied only once. `recover_run` continues a run that crashed mid-step but refuses to skip a pending approval. The worker never calls Jube or Frappe. The Vati integration layer implements the port through the TAX-046 engine (ADR-001, ADR-013). A port failure is recorded as `failed` and requires human review. It is never reported as success.

Alternatives and tradeoff:

- Keep `InMemorySaver`: no dependency, but a restart loses the paused run, which fails the acceptance criterion.
- Write our own saver on stdlib `sqlite3`: no new package, but it would re-implement LangGraph's checkpoint and pending-write format, which is risky to get wrong.
- `langgraph-checkpoint-postgres` on the root PostgreSQL: closer to a shared deployment, but it needs Docker for every AI developer and test. SQLite keeps the checks dependency-light. The saver is injected, so a Postgres saver can replace it in a deployment task without changing the graph.
- 3.1.3 (published 2026-10-30) was not used because it is less than two weeks old. 3.1.2 is used.

Official sources and version/date checked (2026-11-04):

- PyPI `langgraph-checkpoint-sqlite` 3.1.2 (uploaded 2026-10-12, MIT): requires `langgraph-checkpoint>=4.3.0,<5` (locked 4.3.1), `aiosqlite>=0.20` and `sqlite-vec>=0.1.6`. Source: https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-sqlite
- Transitive packages: `aiosqlite` 0.22.1 (2025-12-23, MIT) and `sqlite-vec` 0.1.9 (2026-03-31, MIT/Apache-2.0).
- Interrupt/resume semantics: the LangGraph 1.2.19 `interrupt`/`Command(resume=...)` API, as used in `ai/smoke.py`.
- Repository references: ADR-001, ADR-007 (`decision.approve` is supervisor only, with separation of duties) and ADR-013.

Verification performed: `tests/ai/test_human_checkpoints.py` (12 tests, 12 rejection subtests) uses a real SQLite file. A separate Python process starts a run, pauses at the interrupt and exits, and the test process resumes it with a valid approval: the action is applied once. Unauthenticated, missing-identity, cross-tenant, wrong-case, unapproved, wrong-draft, wrong-outcome, non-supervisor, non-approver, self-approval, stale-version and changed-payload resumes are each refused while the run stays paused, after which the real approver succeeds. A direct `Command` resume cannot bypass the check. A second resume after completion is refused. A crash after the side effect replays with the same key and leaves one applied effect. Recovery cannot skip an approval. A port timeout is recorded as a failure. The lockfile was checked by installing it into a clean Python 3.13 environment (`pip check` passes).

Consequences and revisit trigger: TAX-051, TAX-053 and TAX-061 build on this graph and port. The Vati integration layer must implement `ApprovedActionPort` and deliver approved decisions with `draft_id`, `case_version` and `payload_digest`. Note that TAX-046's `Vati Decision` does not yet store the agent payload digest, so that link must be added when the port is wired. Revisit when a shared deployment needs a multi-worker checkpoint store (move to Postgres), when TAX-059 needs checkpoint retention or redaction rules, or if TAX-046 changes the approval record.
