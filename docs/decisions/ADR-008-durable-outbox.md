# ADR-008: Durable outbox and idempotent delivery workers

Status: proposed (TAX-033, pending human review)
Owner and task: Edga (owner), TAX-033. Implemented by keletso-claude-code on Keletso's instruction.
Date: 2026-11-04

Context and smallest prototype need: Frappe changes, such as a validated submission or a recorded evidence request, must trigger work in other services (a Jube case, an outward evidence request) exactly once in effect. Crashes, retries and replays must not duplicate a case or an outward request (V1_ACCEPTANCE, API_STANDARD, INTEGRATIONS: "Non-idempotent upstream calls require a persisted deduplication record before retries"). ENGINEERING_STANDARD requires that outbox jobs deliver committed changes and that handlers are idempotent. ADR-006 put canonical IDs in Frappe, and there is still no separate Vati database.

Decision:

- Three `vati_tax` DocTypes, written only through `vati_tax.outbox`. Pure rules are in `outbox/policy.py` and are unit-tested without a bench.
  - `Vati Outbox Event` (name = `event_id`) stores the `EventEnvelope` from openapi.json plus delivery state: `pending`, `in_progress`, `retry`, `delivered`, `dead_letter`. The contract `SyncState` maps these to `pending`/`synced`/`failed`.
  - `Vati Outbox Attempt` stores one row per delivery attempt. The row is committed before the handler runs, so a crash still uses up the retry budget.
  - `Vati Outbox Effect` is a ledger of named outward side effects per event, with states `started` and `completed`.
- **Transactional enqueue.** `store.enqueue` inserts the event in the caller's transaction and never commits, so the domain row and its event commit or roll back together. The unique `dedupe_key` (tenant, idempotency key) makes enqueue idempotent. The same key with the same request digest is replayed. The same key with a changed digest raises `idempotency_conflict` (422). The case must belong to the tenant; otherwise the result is `not_found`.
- **Claiming and leases.** The worker selects due rows with `FOR UPDATE SKIP LOCKED`, sets a lease token and expiry, and commits. A row whose lease has expired (its holder crashed) is replayed. If that row has already used its whole budget, it is dead-lettered (`lease_expired`) instead of being run again. Finalising and outward calls first re-check the lease token under a row lock, which fences off a stale worker.
- **Retries.** Retries are bounded: by default 5 attempts, exponential backoff starting at 30 s and capped at 1 h, with equal jitter, and `Retry-After` is honoured. Permanent errors, a missing handler and exhausted budgets go to `dead_letter`. Only error codes are stored, never messages, because messages may contain upstream or document text. An operator (`System Manager`) can retry a dead letter, which starts a fresh budget and is versioned. Retrying an event that is still being delivered is `idempotency_in_progress` (409).
- **Ordering.** Events of one aggregate (tenant + case) are delivered in enqueue order, using a unique `sequence_key`. A failing or dead-lettered event blocks later events of the same case. Other cases are not blocked.
- **Idempotent handlers.** A handler's local writes commit atomically with the `delivered` mark. Outward calls go through `Delivery.effect(name, perform, idempotent=..., reconcile=...)`. The ledger commits `started` before the call and `completed` (with the upstream reference) straight after it. On a replay:
  - a completed effect is reused and not called again;
  - a `started` effect is re-sent only if the upstream honours the stable idempotency key;
  - otherwise the call is looked up by key, or the event is dead-lettered (`effect_outcome_unknown`) so an operator can reconcile it (`resolve_effect` or `release_effect`). It is never blindly re-sent.
- Handlers are registered per event type with the `vati_outbox_handlers` hook. A cron scheduler entry (every minute) runs `worker.dispatch_scheduled`.

Alternatives and tradeoff:

- Plain `frappe.enqueue` (RQ) jobs: they are not transactional with the domain write, because a job can run before or without the commit. Their retries are in Redis, not the database. Rejected as the source of truth. RQ can still be used later to wake the dispatcher.
- Exactly-once delivery: this cannot be achieved across services. We use at-least-once delivery with idempotent handlers and the effect ledger. The cost is that an upstream that is non-idempotent and has no lookup needs a human to reconcile after a crash mid-call.
- Global FIFO: this is simpler, but one poison event would stall every tenant. Per-case order limits head-of-line blocking to that case.

Official sources and version/date checked (2026-11-04): Frappe Framework v16.54.0 (`integration/frappe-framework.lock.json`); `scheduler_events` cron hooks, https://docs.frappe.io/framework/user/en/python-api/hooks#scheduler-events; MariaDB 11.8 (docker-compose.frappe.yml) `SKIP LOCKED`, https://mariadb.com/kb/en/select/#skip-locked; draft-ietf-httpapi-idempotency-key-header-08 (as in ADR-003). Behaviour was observed in the tests below.

Verification performed: `python3 scripts/frappe_dev.py verify --project rsl-taxathon-frappe-tax019 --http-port 58010 --socketio-port 59010` installed and migrated a disposable site twice. It then ran 53 site-backed tests, all passing, 18 of them from `vati_tax.outbox.test_outbox_bench`. These cover: domain row and event committing or rolling back together; idempotent enqueue and the 422 conflict; DB unique keys; tenant scoping; crash after a committed local write (one case); crash after an outward call with a non-idempotent upstream plus reconcile, and with an idempotent upstream, for both a Jube case and an evidence request (one upstream record each); crash after a completed effect (no second call); unknown outcome leading to dead letter and then operator resolve/release and retry; stale-worker fencing; bounded backoff to dead letter with failed writes rolled back; permanent error and missing handler; a bounded crash loop; manual-retry rules including 409; per-case ordering and blocking; read-only, tenant-isolated desk access. 17 Frappe-free outbox policy tests (120 app unit tests in total) also passed. The site was dropped afterwards.

Consequences and revisit trigger: TAX-034 and later adapters register handlers and call outward APIs through `Delivery.effect`, giving the upstream idempotency key where the API supports one. TAX-031/032 call `store.enqueue` in the same transaction as their records. Revisit when the Vati API server lands (it may front these tables), if throughput needs an RQ wake-up or more than one worker per site, or if a case needs parallel unordered streams.
