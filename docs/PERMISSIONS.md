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
| `integration.callback` | | | | | yes |

\* Human-only. A service identity is refused these actions even if the matrix is widened by mistake. Stale approvals, idempotent decision records and the Jube transition belong to TAX-046.

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
