# ADR-036: Event wiring from Frappe to Jube, Docling, OpenAleph and yente

Status: proposed (TAX-087, pending review by Keletso for integration and Edga for Frappe)
Owner and task: Leseli (owner, handed over by Keletso), TAX-087. Implemented by leseli-claude-20261105-087.
Date: 2026-11-05

Context and smallest prototype need:
- Every service adapter exists and the TAX-085 factories build them inside Frappe:
  - Jube case and risk (TAX-034/039), Docling (TAX-038), FtM and OpenAleph (TAX-036), yente (TAX-037).
- Only the Jube step ran at runtime. TAX-039 enqueues `case.registered` when a taxpayer sends a submission.
- Nothing moved a submitted claim's documents on to extraction, entity indexing or screening, so a demo needed manual steps.
- The outbox (ADR-008) already gives ordered, retried, dead-lettered delivery with an effect ledger. Its contract event types already include `evidence.received`, `extraction.completed` and `extraction.failed`.

Decision:

**Entry points** (both run in the caller's transaction, so an event commits with its cause):
- `doc_events` `Vati Submission.on_update` runs second, after the TAX-039 bridge registered the case. On the taxpayer's send, every document the refund claim refers to is linked to the case:
  - the documents come from `evidence_ids` and the schedule rows' invoice and contract documents;
  - the link is a `frappe`/`Vati Evidence` service link, the TAX-032 way.
- `doc_events` `Vati Service Link.after_insert`: any evidence linked to a case gets one `evidence.received` event. This covers the send, an officer's upload with `case_id` and later responses, through one rule.
- Event keys are derived from case, evidence and SHA-256 (or extraction ID), so enqueueing a step again replays it.
- Evidence events reuse the correlation ID of the case's `case.registered`, and later events reuse theirs. One ID traces a claim end to end.

**Handlers** (`vati_outbox_handlers`; TAX-039 keeps `case.registered`, so Jube is not duplicated):
- **`evidence.received`**: Docling converts the stored original and the result is recorded as an extraction revision.
  - The original is re-read and re-hashed before conversion.
  - The result is recorded through the TAX-032 `record_extraction`, as the service identity.
  - Docling's submission is not idempotent and cannot be looked up by key. It is the outbox effect `docling.submit`, whose result is the Docling task ID, recorded before the conversion is awaited:
    - a replay resumes that task through the adapter's job store (the adapter gained a public `submit()`);
    - a submission Docling refused (an HTTP answer, a refused connection) drops the effect row, so the retry submits again;
    - a lost task (`task_lost`) also drops the effect row;
    - a timeout keeps the row, so an unknown outcome dead-letters for an operator (TAX-033 `resolve_effect` or `release_effect`) instead of converting twice.
  - Links recorded: `docling`/`conversion_job` (task ID) and `docling`/`extraction` (with the outbox event ID as `source_event_id`).
  - Then `extraction.completed` (success or partial) or `extraction.failed` is enqueued.
- **`extraction.completed` and `extraction.failed`** (one handler):
  - **Mapping:** the case's taxpayer (`Vati Taxpayer`), the claim's purchase-invoice schedule and the case's evidence are mapped by the TAX-036 `tax_mapping` to FtM entities. Every invoice and payment cites its documents by SHA-256.
  - **OpenAleph:** entities not yet in the collection are written, and recorded in the new `Vati Entity Index`, keyed by tenant, collection and entity.
    - This is an idempotent outbox effect (`openaleph.ingest`), so the ledger rows commit with it even if screening then fails.
    - An entity whose mapping changed is never re-sent. It is flagged `reindex_required`, for a reviewed re-index (TAX-036), without blocking the case.
  - **yente:** parties not yet screened on yente's current dataset version are matched (TAX-037), as the idempotent effect `yente.match`. Candidates are stored `unreviewed` as contract `MatchCandidate` objects in the new `Vati Screening Result`. Only an officer decides.
  - **Links:** `openaleph`/`document` for the evidence documents.
    - Party, invoice and payment entities are not linked, because a party can belong to several cases while a service link maps one upstream record to one case. They are traced through `Vati Entity Index.case_id` and its event ID.
    - Candidates are not linked either, for the same reason. They are traced through `Vati Screening Result`.

**Identity, configuration and failures:**
- **Service identity:** the handlers run as `vati_pipeline_service_user`, a user with only `Vati Service` memberships named in site config, because the evidence store authorises the session user. If it isn't configured, the step retries as `service_identity_not_configured`.
- **Services:** they come from the TAX-085 factories (`vati_services`). An unconfigured, unreachable or busy service is a retryable `<service>_<reason>` code: it retries with backoff, then dead-letters. TAX-033 order means later steps of that case wait. Only codes are stored, never upstream text.
- **Operator view:** `pipeline.api.case_status` (System Manager) shows for one case:
  - the events with status, attempts, error code and correlation ID;
  - the effects;
  - the links;
  - each document's latest extraction;
  - indexed entities, re-index flags and screenings.

  Dead letters are retried with the existing `outbox.api.retry_event`.

**Demo and fakes:**
- `pipeline.demo.run` (verify sites only) sends SYN-001 and SYN-002 through the portal method and drains the outbox with no manual step.
- `pipeline.fakes` provides in-process transports for Docling, OpenAleph and yente, which still run the real adapters, and port-level Jube. They are used only by the demo and the bench tests. The report marks each service `live` or `fake`.

Alternatives and tradeoff:
- **New event types** (for example `entities.index_requested`): rejected. It would be a contract change to `EventEnvelope.type`; the existing types fit the steps.
- **Indexing on `case.registered`:** the outbox allows one handler per type and the bridge owns it. A second hook path would duplicate Jube work or split ordering.
- **Persisting each entity as a service link:** link keys are unique per tenant and upstream ID, so a party shared by two cases would conflict. A per-collection ledger matches the TAX-036 ingest contract (`entity_id -> digest`).
- **A Docling job DocType:** the outbox effect ledger already stores a durable, lease-fenced reference per event and gives operators reconcile and release tools.
- **Running handlers as Administrator:** this would bypass the TAX-029 action checks that `record_extraction` relies on.

Official sources and version/date checked (2026-11-05):
- The adapters' own locked sources, unchanged here:
  - docling-serve 1.38.2 async routes (TAX-038);
  - OpenAleph `_bulk` (TAX-036);
  - yente 5.5.0 `/catalog` and `/match` (TAX-037).
- ADR-008 (outbox, effects), ADR-011, ADR-012, ADR-022, ADR-028 and ADR-035 in this repository.
- Frappe 16.54.0 `doc_events` (a list runs in order) and `frappe.set_user`, in the pinned bench.

Verification performed: see the TAX-087 evidence in `tasks/TAX-087.json` and the PR.

Consequences and revisit trigger:
- **Site configuration:** every site that should run the pipeline needs `vati_pipeline_service_user` (a user created with `identity.provision.ensure_service_identity`) and the `vati_services` sections. `frappe_dev.py init` doesn't create that user yet.
- **Long waits:** Docling conversion waits inside the worker up to the adapter deadline (300 s). Very large documents would hold a worker; revisit with an async poll event if that matters.
- **Changed entities:** a changed mapping (for example a corrected claim) leaves `reindex_required` rows for an operator, as TAX-036 requires.
- **Revisit when:**
  - the Vati service process exists (move the operator view there);
  - a second OpenAleph collection per tenant is needed;
  - yente review decisions are persisted (TAX-054 or the Vati API).
