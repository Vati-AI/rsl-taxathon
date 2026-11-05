# ADR-028: Frappe to Jube submission and state bridge

Status: proposed (TAX-039, pending review)
Owner and task: Leseli (owner, handed over by Keletso), TAX-039. Implemented by leseli-claude-20261105-039.
Date: 2026-11-05

Context and smallest prototype need: ADR-001 makes Frappe the authority for submissions and Jube the authority for the operational case. A sent claim must reach the risk and case services exactly once in effect, and the taxpayer must see progress without ever seeing risk detail. TAX-033 provides the outbox, TAX-034 the replay-safe case adapter and TAX-035 the reviewed risk model. Jube raises a case only from an archived model invocation (ADR-009), and Jube sends no webhook or notification for any workflow status.

Decision:

- **Send.** A `doc_events` hook on `Vati Submission` (`on_update`) reacts only to the taxpayer's own send (`draft`/`correction_needed` to `submitted`). In the sender's transaction it:
  - registers the canonical case with key `vati:submission-case:<submission_id>`, so there is one case per submission whatever the number of sends;
  - writes the write-once `case_id`;
  - shows the taxpayer `under_review`;
  - creates the staff-only `Vati Case Sync` row;
  - enqueues one `case.registered` outbox event (`source_service: frappe`) keyed per send, `vati:submission-sent:<id>:v<version>`. The payload holds only `submission_id` and `submission_version`.
- **Deliver.** The outbox handler:
  1. invokes the risk model through the effect ledger (`jube.risk_invoke`) with facts taken from the declaration;
  2. asks the TAX-034 adapter for the case (`jube.case`);
  3. reads the case back and mirrors it.

  Both calls are marked idempotent. A repeated invocation only archives one more evaluation of the same facts and changes no case, and `ensure_case` looks up the link and then Jube by key before it creates anything. A case requested before Jube has archived the invocation is `risk_evaluation_missing`, which is retryable, and the next attempt reuses the completed invocation. An older send's event skips the invocation, because a newer event carries the newer figures.
- **Declared facts.** The first evaluation runs before any document is extracted, so `documented_input_vat` is 0 and all claimed input VAT counts as unsupported. That is literally true at this point, and it only routes the case to review. Three inputs stay 0 until later steps (TAX-054) supply them: related-party purchases, loss history and documented VAT. `declared_payload` refuses facts that do not name exactly the ruleset's features, so the bridge and ruleset cannot drift silently. The payload carries `facts_basis: declaration`.
- **Mirror.** `Vati Case Sync` keeps the Jube case number, the Jube status projection, escalation, closure, `observed_at` and the last event ID. Its only readers are System Manager and the tenant's auditors, specialists and supervisors; it has no `taxpayer_id` and no taxpayer DocPerm. The submission carries only `review_status` (`under_review`, `evidence_requested`, `completed`, the portal's existing words) and `review_status_at`. Escalation, failed processing and validation all read `under_review`.
- **Observations.** There are three sources:
  - the outbox handler after delivery;
  - a poller every five minutes for open cases, which writes only when the projection changed;
  - `receive_case_status`, where a service identity holding `integration.callback` in the tenant posts a `case.status_projected` EventEnvelope.

  The payload allowlist is exactly `jube_case_id`, `status`, `escalated`, `closed` and `observed_at`. Any other field is refused, not filtered, so risk scores, rule names or officer IDs cannot ride along.
- **Replay and order.** Posted envelopes are deduplicated in `Vati Bridge Inbound Event` (unique tenant and event key). The same event with the same content returns the stored outcome with `replayed: true`; the same event with changed content is `idempotency_conflict`. Jube has no case version, so ordering uses `observed_at`, the time the state was read from Jube:
  - an older observation is `version_conflict` (409);
  - the same moment with a different state is refused too;
  - a newer observation of the same state only moves the watermark.

  An observation that names a different Jube case from the recorded link is refused.
- **No loop.** Mirror writes are direct database writes (`frappe.db.set_value`, `update_modified=False`) to `review_status`, `review_status_at` and the sync record. They never save a document, so no controller, `doc_events` hook or outbox event runs, and they never touch `status`. Every submission save keeps the stored `review_status`, so no client can set it and a save that raced a mirror write cannot undo it.
- **Configuration.** Sites name factories in `site_config.json`: `vati_case_lifecycle` (shared with TAX-046) and `vati_risk_evaluator` (`integration/jube/risk_invoke.py` `DeclaredRiskEvaluator`). Without them, events retry and then dead-letter as `jube_not_configured`. Only error codes are stored, never upstream text.

Alternatives and tradeoff:

- Calling Jube inside the taxpayer's request: a Jube outage would fail or slow the send, and a crash after Jube accepted would duplicate work. The outbox keeps the send fast and makes delivery durable.
- Mirroring the Jube status name or the contract `CaseStatus` onto the submission: taxpayers can read their submission, so escalation and internal failures would leak. Three portal words carry what a taxpayer needs.
- Ordering by an arrival counter: an envelope that arrives late but was read earlier would overwrite newer state. Read time is the only order Jube gives us, but it relies on the readers' clocks. The poller, the handler and the posting service must share NTP-synchronised time.
- Filtering unknown payload fields: a sender bug would silently drop data that a reviewer believed was mirrored. Refusing makes the boundary visible.
- Polling only: simpler, but a posted envelope lets a future Jube-side or Vati-side reader push changes without waiting five minutes.

Official sources and version/date checked (2026-11-05): Jube locked source `01c4afdbcdb186cc36b128ed79283d544cfb9ec9` (`integration/upstreams.lock.json`). The invoke route `POST /api/Invoke/EntityAnalysisModel/{guid}`, the invoke-key admission and the 404 for a model that is not loaded yet are as recorded in `integration/jube/README.md` (TAX-035) and exercised by `scripts/jube_rules.py verify`. `CreateFromCaseKeyValue` needing an archived invocation is from ADR-009. Frappe Framework v16.54.0 (`integration/frappe-framework.lock.json`): `doc_events` `on_update`, `frappe.db.set_value` (no document hooks), `scheduler_events` cron. The bridge was not run against a live Jube stack in this task.

Verification performed:

- `backend/vati_tax/vati_tax/bridge/test_rules.py` (23 Frappe-free tests): the status mapping, the allowlist, replay and conflict, stale and same-moment ordering, the loop guard and declared facts.
- `tests/integration/test_jube_bridge.py` (14 tests): the real `RiskInvoker`, `DeclaredRiskEvaluator` and TAX-034 `JubeCaseAdapter` against the Jube fake extended with the invoke route and the reviewed rules. It covers invoke-then-create, replay with no second case, retryable missing evaluation, error classification without upstream text, ruleset drift, escalation and resolution mapping, and out-of-order refusal.
- `backend/vati_tax/vati_tax/bridge/test_bridge_bench.py` runs on a disposable site via `scripts/frappe_dev.py verify`; the PR records the results.

Consequences and revisit trigger:

- TAX-054 supplies document-derived risk inputs and may re-invoke after extraction.
- TAX-046's decision execution changes Jube, and the poller mirrors that change to the taxpayer.
- Revisit:
  - if Jube gains webhooks or case versions, replacing `observed_at` ordering;
  - if a shared deployment runs readers on separate hosts (clock skew);
  - if the portal needs finer taxpayer statuses;
  - if a tenant needs more than five-minute freshness.
