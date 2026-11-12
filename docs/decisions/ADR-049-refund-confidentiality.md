# ADR-049: Refund cases are visible only to the refund team and the assigned officer

Status: accepted
Owner and task: Gupta, TAX-142 (implements ADR-043 §10, "refund-confidentiality access rules in the pilot")
Date: 2026-11-12

Context and smallest prototype need:
TAX-029 isolates tenants and taxpayers, but inside a tenant every auditor, specialist and
supervisor could read every case. ADR-043 moved refund-confidentiality rules from "later"
into the pilot: refund case data must be visible only to the people working the refund,
and refused access must be tested and logged. Vati had no record of who works a case
(Jube holds its own assignment, ADR-046), so there was nothing to scope by.

Decision:

1. **A refund case** is a case whose submission is a `refund_claim` (Online Refunds claims
   from TAX-122 and portal claims). Its records are refund data: every tenant-scoped
   DocType with a `case_id`, plus `Vati Submission` and the records keyed by its
   `submission_id` (`Vati Refund Claim`, `Vati VAT Return`, `Vati Imported Service`).
   The audit ledger, outbox events and bridge inbound events are exempt: they carry
   references, not refund figures, and the ledger must stay whole for supervisor review.
2. **Refund team.** `Vati Tenant Membership.refund_team` (staff only) puts an auditor,
   specialist or supervisor on the tenant's refund team.
3. **Who may read a refund case** (`identity.policy.may_read_refund_case`):
   - a service identity (the pipeline and integrations);
   - a refund-team supervisor (every refund case of the tenant, to assign the work);
   - the refund-team auditor or specialist the case is assigned to.

   Everyone else, including supervisors outside the refund team, is refused. Taxpayers are
   not affected (they only reach their own records), and site operators are not narrowed,
   as for TAX-029.
4. **Assignment.** `Vati Case Assignment` (one row per case, versioned, never deleted)
   names the officer. Only a refund-team supervisor assigns or reassigns
   (`refund_access.api.assign_case`, `case.assign`). The assignee must be refund-team
   staff of the tenant, and a stale `expected_version` is refused. Each assignment is a
   `case.assigned` ledger entry naming the officer. Reassigning removes the previous
   officer's access.
5. **Enforced everywhere case data is read or changed:**
   - Desk: the wildcard `permission_query_conditions` hide refund records from list
     queries, and `has_permission` refuses opening one;
   - /api/v1: every operation with a `case_id` passes the gate, and `listCases` omits
     hidden cases;
   - the case-scoped whitelisted endpoints (decisions, evidence, exports, challenges,
     reminders, pipeline status, RSL referrals and outbound, bridge review status).
6. **Refusals are logged.** A refused gate call is sealed into the tenant's audit ledger
   as `<action>.denied` with outcome `refund_confidentiality` and committed, then raised as
   403. A refused Desk open is recorded the same way (committed for a read). Being left out
   of a list is not an attempt and is not logged.

Alternatives and tradeoff:

- Mirror Jube's assignment into Vati: deferred. There is no Jube webhook (TAX-039), and the
  rule must hold even when Jube is down. The assignment record can later be fed from Jube.
- Restrict only the auditor app: rejected. Desk and the other endpoints would still show the
  data.
- Let every supervisor see refund cases: rejected. That is the cross-role access the task
  asks us to deny; a supervisor joins the refund team to see them.
- Use Frappe `User Permission`s: rejected. They cannot express "assigned to me in this
  tenant", and they bypass the TAX-029 hooks' logging.

Official sources and version/date checked:
No RSL policy text was available; the rule follows ADR-043 §10 and PROJECT_SCOPE.md. The
Frappe hook behaviour (`permission_query_conditions`, `has_permission`) is the same one
TAX-029 relies on in the pinned Frappe (integration/frappe-framework.lock.json).

Verification performed (or named implementation task):
- `identity/test_policy.py` (Frappe-free): who may read a refund case, refund team per
  tenant, staff-only refund team and assignees, the SQL restriction for case and submission
  records.
- `refund_access/test_refund_access_bench.py` on a disposable site: every staff combination
  before and after assignment and reassignment, ordinary cases unaffected, a ledger entry for
  each refusal, Desk lists and document opens, /api/v1 list and case refusals, assignment
  rules (refund-team supervisor only, refund-team assignee only, stale version, ledger), and
  no refund team for taxpayers.
- `bridge/test_bridge_bench.py` now expects a refund claim's Jube mirror to be hidden from an
  auditor outside the refund team.

Consequences and revisit trigger:
- A refund case needs an assignment before an auditor or specialist can work it. Until the
  auditor app has an assignment screen, refund-team supervisors assign with
  `assign_case` (or bench).
- The /api/v1 `Case.assigned_officer_id` still reads null; it can be filled from
  `Vati Case Assignment` in a later task.
- Revisit when RSL states its refund-confidentiality policy or when Jube assignments are
  mirrored.
