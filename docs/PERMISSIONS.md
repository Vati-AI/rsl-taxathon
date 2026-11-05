# Roles, tenants and permissions (TAX-029)

Frappe owns application identity and permissions (ADR-001). The rules live in `backend/vati_tax/vati_tax/identity/policy.py`. Executable tests are in `identity/test_policy.py` (no bench needed) and `identity/test_tenant_permissions.py` (site-backed, run by `scripts/frappe_dev.py verify`). Design and tradeoffs are in [ADR-007](decisions/ADR-007-roles-tenant-permissions.md).

## Identity model

| Concept | Where | Rule |
| --- | --- | --- |
| Tenant | `Vati Tenant` (name = lower-case UUID `tenant_id`) | Disabled tenants grant nothing. |
| Membership | `Vati Tenant Membership` (user, tenant_id, vati_role, taxpayer_id, enabled) | One per user per tenant. Taxpayer memberships need a `taxpayer_id`, and other roles must not have one. The user must also hold the matching Frappe role. |
| Frappe roles | `Vati Taxpayer`, `Vati Auditor`, `Vati Specialist`, `Vati Supervisor`, `Vati Service` | Created on install and on every migrate. Only the three staff roles have Desk access. |
| Separate identities | membership validation | One user is a taxpayer, staff or a service, never a mix. |
| Service identity | `provision.ensure_service_identity` | A login-less user with only `Vati Service`, plus an explicit membership per tenant. Its API key is created in Desk and kept in ignored config. |

A principal's scope comes only from its enabled memberships. The `X-Vati-Tenant-ID` header selects one of the principal's tenants and never grants access. A missing or malformed header returns 400 (`MissingTenantContext`). A tenant outside the principal's memberships returns 403 (`TenantScopeDenied`). `Administrator` and `System Manager` are site operators and are not narrowed on records. They get no bypass for decision actions.

## Record isolation

Any `vati_tax` DocType with a `tenant_id` field is tenant-scoped. If it also has `taxpayer_id`, it is taxpayer-owned. Wildcard `permission_query_conditions` and `has_permission` hooks narrow every such DocType for list, read, write, create and delete. The DocType's role permissions still decide which operations a role has.

| Principal | Reaches |
| --- | --- |
| Taxpayer | Taxpayer-owned records with its own `(tenant_id, taxpayer_id)`. Nothing else. |
| Auditor, specialist, supervisor, service | Records in its member tenants. |
| No membership | Nothing (fail closed). |

A `DocShare` can only target a user whose scope already reaches the document, so sharing cannot cross a tenant or taxpayer boundary. `frappe.get_all`, raw SQL and `ignore_permissions` bypass these hooks. Use them only on documented internal paths that check scope explicitly.

## Submission DocTypes (TAX-031)

`Vati Taxpayer`, `Vati Submission`, `Vati VAT Return`, `Vati Refund Claim` and `Vati Imported Service` are taxpayer-owned and use the isolation above. Field-level rules ([ADR-010](decisions/ADR-010-submission-doctypes.md)):

| Principal | Taxpayer profile | Submission | Figures (return, claim, imported service) |
| --- | --- | --- | --- |
| Taxpayer | read own | create own; change declaration, contact email and status (`draft`/`correction_needed` -> `submitted`, `draft` -> `withdrawn`) | create, change, delete own while the submission is `draft` or `correction_needed` |
| Auditor, supervisor | read; supervisor edits profile | read; `submitted` -> `correction_needed` with a reason | read |
| Specialist | read | read | read |
| Service | read, create, edit profile | read; set `case_id` once | read |

Server-set fields (reference, version, submission time, period dates, keys, net VAT, masked TIN) are refused when a client changes them. Submitted figures cannot change.

A `Vati Refund Claim` may reference only `Vati Evidence` of its own tenant and taxpayer in `evidence_ids` (TAX-043). A missing ID and another taxpayer's or tenant's evidence are refused the same way (422 `invalid_reference`), so a claim cannot probe for other records. The portal method `vati_tax.portal.api.submit_refund_claim` acts only for the caller's own taxpayer membership.

## Action matrix

Callers check actions with `permissions.require_action(action, tenant_id, taxpayer_id=..., proposed_by=...)`. `vati_tax.identity.api.get_access_scope` lists the caller's allowed actions for the header tenant.

| Action | Taxpayer | Auditor | Specialist | Supervisor | Service |
| --- | --- | --- | --- | --- | --- |
| `submission.create`, `evidence.respond` | own taxpayer | | | | |
| `submission.read` | own taxpayer | yes | yes | yes | yes |
| `case.read`, `evidence.read` | | yes | yes | yes | yes |
| `evidence.register` (TAX-032) | own taxpayer | yes | yes | yes | yes |
| `investigation.request`, `finding.review` | | yes | yes | yes | |
| `draft.record` (agent drafts for human review) | | yes | yes | yes | yes |
| `evidence_request.authorise`*, `specialist.refer`* | | yes | | yes | |
| `specialist.opinion.record`* | | | yes | | |
| `decision.propose`* | | yes | yes | yes | |
| `decision.approve`* | | | | yes, not own proposal | |
| `case.assign`*, `playbook.approve`* | | | | yes | |
| `decision.execute` (TAX-046: resend an approved decision to Jube) | | | | yes | yes |
| `ledger.read`* (TAX-059: audit ledger, integrity check, signals, summary) | | | | yes | |
| `override.record`* (TAX-059: exception to a named control) | | | | yes, not on own work | |
| `export.request`* (TAX-062: request a scoped evidence export) | | yes | yes | yes | |
| `export.approve`* (TAX-062: authorise or refuse an export) | | | | yes, not own request | |
| `export.read`* (TAX-062: export status; manifest for requester and approver only) | | yes | yes | yes | |
| `challenge.lodge`* (TAX-069: object to an approved decision or appeal an objection outcome) | own taxpayer | yes, on the taxpayer's behalf | | yes, on the taxpayer's behalf | |
| `challenge.review`* (TAX-069: record the reviewed outcome) | | | | yes, not on a decision or challenge they took part in | |
| `challenge.read` (TAX-069: decision, challenges and final outcome) | own taxpayer | yes | yes | yes | yes |
| `integration.callback` | | | | | yes |

\* Human-only. A service identity is refused these actions even if the matrix is widened by mistake. Stale approvals, idempotent decision records and the Jube transition are TAX-046 (see Decisions below).

## Local setup (synthetic users only)

```sh
bench --site taxathon.localhost execute vati_tax.identity.provision.ensure_tenant \
  --kwargs '{"tenant_id": "10000000-0000-4000-8000-000000000001", "tenant_name": "Synthetic tenant"}'
bench --site taxathon.localhost execute vati_tax.identity.provision.grant_membership \
  --kwargs '{"user": "taxpayer.a@example.test", "tenant_id": "10000000-0000-4000-8000-000000000001", "role": "taxpayer", "taxpayer_id": "80015243-dd34-4e97-b085-7356ffdd042f"}'
```

The taxpayer above owns fixture `SUB-SYN-0001`. `portal/fixtures.py` maps every portal fixture to its synthetic owner.

## Evidence (TAX-032)

`Vati Evidence` and `Vati Evidence Extraction` follow the record-isolation convention (`tenant_id` + `taxpayer_id`). Officers and services read evidence in their tenant. A taxpayer reads only evidence it uploaded itself (`if_owner`), never officer uploads or extraction revisions. Only a service identity records extraction revisions (`integration.callback`). The evidence source is bound to the role: `taxpayer_portal` for taxpayers, `officer_upload` for officers, `synthetic_fixture` for officers or services. Evidence originals are private Files that nobody can make public, move, edit or delete through Frappe. See [ADR-011](decisions/ADR-011-evidence-storage-provenance.md).

## Decisions and evidence requests (TAX-046)

`Vati Decision` and `Vati Evidence Request` follow the record-isolation convention (`tenant_id` + `taxpayer_id`). Officers and services read them in their tenant; taxpayers have no DocType permission. `Vati Audit Event` is the audit ledger (TAX-059, below): only supervisors read it in Desk, and officers see a case's decision trail through `get_case_review_state`. Nothing is created through Desk: `vati_tax.decisions.store` writes them after the action checks below, and controllers refuse deletes and edits to recorded fields. See [ADR-013](decisions/ADR-013-decisions-approvals-evidence-requests.md).

TAX-045: taxpayers read their own evidence requests only through the portal (`vati_tax.portal.api.get_evidence_requests` / `get_evidence_request`), which filters by the caller's own (tenant, taxpayer) memberships and returns an allowlisted view: no case ID, finding IDs, authoriser, case version or keys; another scope's request is reported as not found. `respond_to_evidence_request` needs `evidence.respond` for the request's own taxpayer. `Vati Evidence Response` is read-only for every role (written only by `vati_tax.responses.store`, never edited or deleted) and isolated by `tenant_id` + `taxpayer_id`. Attached evidence must be the caller's own and not already attached to another request. See [ADR-019](decisions/ADR-019-evidence-request-responses.md).

| Step | Who | Refused when |
| --- | --- | --- |
| Record an agent draft | any `draft.record` role | never executable; approving it is `agent_only_decision` |
| Propose | auditor, specialist, supervisor (`decision.propose`) | service or taxpayer; another decision on the case is open; stale `expected_version`; Jube status differs from `expected_case_status`; prohibited or unknown outcome; closing without a citation; evidence outside the tenant or taxpayer |
| Approve or reject | a supervisor who did not propose it (`decision.approve`) | proposer, auditor, specialist or service; not `proposed`; stale `expected_version`; proposal older than 72 hours; Jube status changed since proposal |
| Refer to a specialist (`refer_to_specialist`, TAX-042) | auditor, supervisor (`decision.propose` and `specialist.refer`) | specialist, service or taxpayer; then as Propose. An internal escalation in Jube, never a referral outside the tax office |
| Withdraw | the proposer | anyone else |
| Retry the Jube call | supervisor or service (`decision.execute`) | not approved; already synced (replay); final failure; approval older than 24 hours |
| Authorise an evidence request | auditor, supervisor (`evidence_request.authorise`) | specialist, service or taxpayer; stale `expected_version`; case without a taxpayer; due date not in the next 180 days |

`get_case_review_state` also returns `viewer` (TAX-042): the caller and the review actions they may take on the case. `decision.approve` is listed only when an open proposal exists that the caller did not make. It is a hint for the auditor screen; each action is authorised again when sent.

## Audit ledger and separation of duties (TAX-059)

Every `Vati Audit Event` takes the next position in its tenant's SHA-256 hash chain (`chain_seq`, `prev_hash`, `entry_hash`; `chain_key` is unique per tenant and position). The ledger records decision and evidence-request events, case and evidence reads, ledger reads, supervisor overrides and refused attempts, each with the actor, role and correlation ID. See [ADR-015](decisions/ADR-015-audit-ledger-separation-of-duties.md).

| Step | Who | Refused when |
| --- | --- | --- |
| Read entries, verify the chain, read signals or the summary (`vati_tax.audit.api`) | a supervisor of the tenant (`ledger.read`) | auditor, specialist, service or taxpayer; another tenant. The refusal is recorded |
| Record an override (`record_override`) | a supervisor (`override.record`) | auditor, specialist, service or taxpayer; the override concerns the supervisor's own work (`target_actor`); unknown control; reason shorter than 20 characters. The refusal is recorded |
| Any decision action refused by the policy | anyone | recorded as `<action>.denied`, then refused |

Entries cannot be edited or deleted through Frappe. A row changed directly in the database fails `verify_ledger`. Integrity signals (repeated overrides, repeated refused self-approvals or overrides, a broken chain) are review prompts only: they are never shown to the supervisor they concern and they change nothing.

## Evidence export and retention (TAX-062)

An export hands named evidence of one case to a named recipient. It produces a frozen hash/provenance manifest, never the files or their text. `Vati Evidence Export` is tenant-scoped. Its Desk read is limited to System Manager, so manifests are only read through `vati_tax.exports.api`, which checks the reader and records the read. Every step and refusal is sealed into the audit ledger (subject type `export`). See [ADR-016](decisions/ADR-016-evidence-export-retention.md).

| Step | Who | Refused when |
| --- | --- | --- |
| Request (`request_export`) | auditor, specialist, supervisor (`export.request`) | taxpayer or service; evidence not in that case or tenant, or unknown (all `not_found`); evidence not synthetic (pilot); no evidence named; purpose unknown; reason shorter than 20 characters. The refusal is recorded |
| Approve or reject (`approve_export`, `reject_export`) | a supervisor who did not make the request (`export.approve`) | the requester; auditor, specialist, service or taxpayer; already decided; stale `expected_version`; request older than 72 hours; requested evidence no longer in the case; rejection without a reason. The refusal is recorded |
| Read the manifest (`get_export_manifest`) | the requester or the approver (`export.read`) | anyone else (`not_found`); not approved; more than 24 hours after approval; manifest no longer matching its hash. The read and any refusal are recorded |
| Read the retention policy (`get_retention_policy`) | auditor, specialist, supervisor | taxpayer or service |

Retention is metadata only (`pilot-synthetic-unverified-1`, `policy_verified: false`). Nothing is disposed of automatically. A site's `vati_retention_days` can only add a `review_after` date, and the audit ledger is permanent.

## Objections, appeals and final outcomes (TAX-069)

`Vati Decision Challenge` is one objection to an approved `Vati Decision`, or one appeal against the reviewed outcome of that decision's objection. It is taxpayer-owned (`tenant_id` + `taxpayer_id`). Officers and services read it in Desk; taxpayers have no DocType permission and use `vati_tax.challenges.api`, which hides staff identities. Nothing in this flow changes the decision, its evidence or earlier ledger entries. An overturned outcome asks for a new decision through TAX-046 and raises a review-only `decision_overturned` signal, withheld from the original decider. No deadline or statutory route is enforced (`legal_route_verified: false`). See [ADR-023](decisions/ADR-023-objections-appeals-final-outcomes.md).

| Step | Who | Refused when |
| --- | --- | --- |
| Lodge an objection (`lodge_challenge`, `stage: objection`) | the decision's taxpayer, or an auditor or supervisor on its behalf (`challenge.lodge`) | specialist, service or another taxpayer (`not_found`); decision not approved; another challenge to it still open; the decision already has an objection; cited evidence not the taxpayer's. The refusal is recorded |
| Lodge an appeal (`stage: appeal`) | as above | objection not yet reviewed; objection overturned or withdrawn; already appealed; parent is not an objection. The refusal is recorded |
| Review (`review_challenge`) | a supervisor (`challenge.review`) | the decision's proposer or approver, the lodger, or for an appeal the objection's reviewer; auditor, specialist, service or taxpayer; unknown outcome; reason shorter than 20 characters; stale `expected_version`; already reviewed; decision changed since lodging. The refusal is recorded |
| Read the history (`get_challenge_history`) | the taxpayer (own), staff, service (`challenge.read`) | another taxpayer or tenant (`not_found`). The read is recorded |
