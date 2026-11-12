# ADR-048: Officer-approved results go back to RSL through outbound contracts

Status: accepted
Owner and task: Gupta, TAX-124 (implements ADR-043 §1 "RSL acts" for results)
Date: 2026-11-12

Context and smallest prototype need:
ADR-043 decided that RSL selects, Vati investigates and RSL acts. Cases now start from RSL:
an Online Refunds claim (TAX-122) or an audit referral (TAX-123, ADR-047). Until now nothing
went back. RSL needs Vati's approved findings, the lane recommendation and undisputed amount,
and a notice when Vati asks the taxpayer for evidence, with a link the taxpayer can follow.
We have no RSL access or specification. The prototype needs explicit assumed contracts,
sinks that say they are fixtures, and delivery that cannot send an unapproved item, send an
item twice, or report an item as sent when RSL did not answer.

Decision:

1. **Three assumed outbound contracts** in `integration/rsl_sinks/contracts.py`, versioned
   `rsl.<system>.<kind>.v0-assumed`:
   - `findings_pack` (to `audit_system` for a referral case, or `online_refunds` for a claim
     case): the approved decision (ID, outcome, approval time, approver role) and the
     findings it names (kind, category, code, confidence, legal-route status, explanation,
     unresolved amount, evidence count). `authority: findings_for_rsl_review`.
   - `lane_recommendation` (to `online_refunds` only): the latest TAX-114 lane proposal's lane,
     claimed, undisputed and disputed amounts, due date and the approving decision.
     `authority: recommendation_only`.
   - `evidence_requested` (to the system the case came from): request ID, due date, item
     count, document types and a deep link `/taxpayer/requests/<request_id>`. No message text
     or item descriptions. `authority: notice_only`.

   Every message names its RSL record (`rsl_reference`), the TIN and the Vati case, states
   `route: wso2_api_manager` (ADR-040) and carries the assumed-shape notice. No contract has
   a field that sets an RSL status, pays, offsets or assesses; the validator refuses those
   names explicitly and any field outside the contract.
2. **Fixture sink.** `integration/rsl_sinks/fixture_sink.py` checks each message against its
   contract and stores it in a local directory (site key `vati_rsl_sinks`). Receipts carry
   `fixture: true` and a `FIXTURE-` ID. It honours the idempotency key (same key and message:
   the stored receipt; same key, different message: refused) and can be configured as
   unavailable per RSL system. It opens no network connection.
3. **Only officer-approved items are queued.** `vati_tax.rsl_outbound` hooks two events, in
   the officer's own transaction:
   - a `Vati Decision` that becomes `approved` (by an officer other than the proposer, TAX-046)
     queues a findings pack, and a lane recommendation when the case is an Online Refunds
     claim with a lane proposal;
   - an officer-authorised `Vati Evidence Request` queues an evidence notice.

   Draft, proposed, rejected and withdrawn decisions queue nothing. A case that did not come
   from RSL queues nothing.
4. **Delivery through the TAX-033 outbox.** Each item is one `rsl_outbound.requested` event
   (a new contract `EventEnvelope.type`, as ADR-042 did for `assurance.requested`) under the
   key `rsl-outbound:<kind>:<subject>`. The handler rebuilds the message from frozen records,
   checks approval again (a request withdrawn after queueing is never sent) and delivers it
   through the outbox effect ledger, so a retry after a crash reuses the receipt. The message
   ID and issue time come from the event, so a rebuilt message is identical.
5. **Recorded in the audit ledger.** Queueing writes `rsl_outbound.<kind>.queued` (the
   approving or authorising officer) and delivery writes `rsl_outbound.<kind>.delivered`
   (with the message SHA-256 and receipt ID), both `write` entries in the tenant hash chain.
   A replayed delivery adds no second entry.
6. **RSL not answering is not delivered.** An unavailable sink is a retryable failure. The item
   stays `queued` with `last_error_code: rsl_sink_unavailable` and no receipt. These events get
   the largest outbox budget (20 attempts, about 15 hours of capped backoff); after that the
   item is `not_delivered` until a site operator retries it (`outbox.api.retry_event`).
   Officers see each item's state with `rsl_outbound.api.get_case_outbound` (`case.read`).

Alternatives and tradeoff:

- Send at approval time, inside the request: rejected. RSL being down would fail the officer's
  approval, or the item would be lost. The outbox commits the intent with the approval.
- Store a separate `Vati RSL Outbound` record: deferred. The outbox event, its effect row and
  the ledger already hold the item, its delivery state, the receipt and the message digest.
- Let the lane proposal leave on its own when it is produced: rejected by ADR-043 §6 and the
  TAX-124 acceptance. A machine proposal is only released by an officer's approved decision.
- Send the taxpayer message text in the notice: rejected. RSL needs the status and the link;
  the request text stays in Vati and the portal.

Official sources and version/date checked:
None from RSL; see ADR-043 open questions 2 and 3 and docs/reference/RSL_LANDSCAPE.md
(unverified public descriptions, 2026-11-07). Every field is an assumption.

Verification performed (or named implementation task):
- `tests/integration/test_rsl_sinks.py`: the contracts (versioning, route, authority, refused
  status/payment/offset/assessment fields, floats, unexpected fields, links) and the fixture
  sink (receipts, idempotency, conflict, unavailable, tenants).
- `backend/vati_tax/vati_tax/rsl_outbound/test_rules.py` (Frappe-free): references, items,
  approval refusals, messages validated against the contracts and delivered to the sink,
  stable rebuilds and the delivery states.
- `test_rsl_outbound_bench.py` on a disposable site: one pack per approved decision, pack and
  lane for a claim case, nothing for rejected decisions or non-RSL cases, the evidence notice
  with its portal link, an unavailable sink leaving the item queued and then delivering it
  once, dead letter and operator retry, a withdrawn request never sent, ledger entries and
  tenant scope.

Consequences and revisit trigger:
- RSL receives results only once a sink is configured for the site; until then items stay
  queued and visible, never reported as sent.
- The auditor app does not show outbound state yet; `get_case_outbound` is ready for it.
- Revisit when RSL answers ADR-043 questions 2 and 3 (how a "Pending: evidence requested"
  reason is written back, how a findings pack attaches to an audit case) and when WSO2 API
  Manager publishes real endpoints; the live sink then replaces the fixture behind the same
  contracts.
