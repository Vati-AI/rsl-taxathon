# Canonical data model (implementation contract)

Use UUIDs for internal IDs, UTC ISO timestamps, explicit tenant IDs and string decimal amounts with ISO currency. Never use floats for money. Keep source IDs separately; never join entities solely on names.

| Entity | Key fields and relationships |
|---|---|
| Tenant / UserMembership | tenant_id, user_id, role (taxpayer, auditor, supervisor, service) |
| Taxpayer | taxpayer_id, tenant_id, synthetic/approved tax identifier, legal name, industry |
| Submission | submission_id, taxpayer_id, period, type, status, source_ref, version |
| VATReturn / RefundClaim | submission_id, declared amounts, currency, period, claim_id |
| Invoice / Payment / Import | record_id, taxpayer_id, counterparty_id, date, currency, decimal amounts, source_ref |
| RelatedParty | source_entity_id, target_entity_id, relationship_type, valid dates, evidence_ids |
| Evidence | evidence_id, tenant_id, case_id, document_sha256, restricted object_ref, page/record locator, extraction version, source, received_at |
| Case | case_id, tenant_id, taxpayer_id, submission_id, status, assigned_officer, version |
| Finding | finding_id, case_id, category, rule/model version, explanation, evidence_ids, unresolved_amount, confidence, evidence_gap |
| EntityMatch | match_id, local_entity_id, candidate_id, score, dataset/version, reviewer, resolution |
| InvestigationRun | run_id, case_id, workflow_version, checkpoint/thread_id, status, tool_calls, evidence_ids |
| EvidenceRequest / Response | request_id, case_id, requested items, approval_actor, sent_at, response_refs |
| Decision | decision_id, case_id, authorized_actor, outcome, rationale, evidence_ids, decided_at |
| AuditEvent / Outbox | event_id, tenant_id, case_id, actor, action, timestamp, correlation_id, payload_hash, delivery status |

Proposed UI state projection (map to configured Jube workflow; do not create a second lifecycle): RECEIVED → VALIDATING → INVESTIGATING → AWAITING_HUMAN → RESOLVED. AWAITING_EVIDENCE and FAILED_PROCESSING are explicit branches with retry/re-entry rules. Only authorized humans can transition AWAITING_HUMAN to RESOLVED. Closure does not authorize refund payment or enforcement.

Maintain a `service_links` table unique on (tenant_id, service, external_id), mapping case_id/evidence_id to Jube/OpenAleph/Frappe IDs. Optimistic version checks prevent concurrent decision overwrites. Idempotency key and request digest prevent replay with changed payloads. Foreign keys include tenant boundaries or equivalent database constraints.

Evidence is append-only: corrections create a new version with a supersedes pointer. Findings distinguish observed facts, inference, missing evidence and proposed actions. Store reviewed outcomes separately from model suggestions. A legal-route field remains `unverified` until a qualified reviewer supplies an authoritative citation.

Jube is operational Case authority. Frappe implements users, tax submissions, requests and approved decision records. Vati stores cross-service links and delivery state. See ADR-001.
