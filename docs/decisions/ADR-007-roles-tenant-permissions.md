# ADR-007: Vati roles, tenant memberships and convention-based record isolation

Status: proposed (TAX-029, pending human review)
Owner and task: Edga (owner), TAX-029; implemented by keletso-claude-code on Keletso's instruction
Date: 2026-11-04

Context and smallest prototype need: ADR-001 makes Frappe the authority for users, roles and permissions. ADR-003 requires `X-Vati-Tenant-ID` to select a tenant inside the principal's permitted scope (400 `missing_tenant_context`, 403 `tenant_scope_denied`). V1_ACCEPTANCE requires server-side role and taxpayer isolation, taxpayer A versus B tests, officer/supervisor permissions and human approval for consequential actions. Later DocTypes (TAX-030 to TAX-033, TAX-046) need one isolation mechanism, not one per DocType.

Decision:

- Five Frappe roles (`Vati Taxpayer`, `Vati Auditor`, `Vati Specialist`, `Vati Supervisor`, `Vati Service`) are created idempotently in `before_install`/`before_migrate`. Only staff roles have Desk access.
- Tenant scope comes from `Vati Tenant Membership` rows (user, tenant_id, vati_role, taxpayer_id). There is one per user and tenant, enforced by a DB unique index added in `on_doctype_update`. A membership grants nothing unless it is enabled, its tenant is enabled and the user holds the matching Frappe role. Taxpayer, staff and service identities are separate users.
- Convention: any `vati_tax` DocType with `tenant_id` is tenant-scoped, and adding `taxpayer_id` makes it taxpayer-owned. Wildcard (`"*"`) `permission_query_conditions` and `has_permission` hooks apply the pure policy to every such DocType. A new DocType is protected by adding the fields, and its tests only need to show the negative cases. The hooks only deny. Role DocPerms still grant operations.
- A `DocShare` validate hook stops sharing from crossing tenant or taxpayer scope, because Frappe sharing can otherwise grant access after a controller denial.
- Decision actions use a code-level role/action matrix (`policy.ACTION_ROLES`), checked through `require_action`. Human-only actions are refused for service identities independently of the matrix, and `decision.approve` requires a different proposer. Operators get no bypass for actions.
- Operators (`Administrator`, `System Manager`) are not narrowed on records, so site administration and the TAX-030 internal mapping paths keep working.

Alternatives and tradeoff:

- Frappe User Permissions on a `Vati Tenant` link: built-in, but they do not express "taxpayer X inside tenant Y". They are ignored by `ignore_user_permissions` fields and are easy to misconfigure per DocType. Rejected for the core rule.
- Per-DocType hook registration: more explicit, but a new DocType could ship unprotected. The wildcard costs a cached meta lookup on every permission check. It is limited to this app's module and short-circuits when there is no `tenant_id` field.
- Multiple roles per membership: more flexible, but ambiguous for separation of duties. Staff can still hold different roles in different tenants.
- Exempting `System Manager` is a prototype convenience. Revisit before any shared demo with non-developer operators.

Official sources and version/date checked (2026-11-04): Frappe Framework v16.54.0 at the commit in `integration/frappe-framework.lock.json`. Source read in the pinned bench: `frappe/permissions.py` (`has_permission`, `get_doc_permissions`, `has_controller_permissions` with `hooks.get("*")`, the share fallback after a controller denial) and `frappe/model/db_query.py` (`get_permission_query_conditions` with `hooks.get("*")`).

Verification performed: `python3 scripts/frappe_dev.py verify --project rsl-taxathon-frappe-tax019` on a disposable site: install, migrate twice, 22 site-backed tests in `identity/test_tenant_permissions.py` passing. Neutralising the two tenant hooks made 17 of the 22 fail. An HTTP smoke on the disposable site with a synthetic service API key returned 403 as guest, 400 without the header, 403 for a non-member tenant and 200 with the scope for its own tenant. The portal API returned 403 for the service identity. 44 Frappe-free tests in `identity/test_policy.py` and `portal/test_access.py` pass under plain `unittest`.

Consequences and revisit trigger: TAX-031 to TAX-033 add `tenant_id`/`taxpayer_id` and role DocPerms, plus their own negative tests. TAX-046 calls `require_action` and adds stale-approval and idempotency rules. The Vati API maps a service identity's API key to its memberships through `get_access_scope`. Revisit if a user needs several roles in one tenant, if tenants need hierarchy, or if Frappe changes wildcard hook semantics.
