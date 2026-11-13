# ADR-052: RSL systems join the canonical reference register; one case reference card

Status: accepted
Owner and task: Gupta, TAX-133 (implements ADR-043 §7)
Date: 2026-11-13

Context and smallest prototype need:
ADR-043 puts Vati downstream of RSL. A Vati case now starts from an RSL claim (Online Refunds,
TAX-122) or an RSL audit case (the audit system, TAX-123), and relies on RSL records: e-Tax
returns, Lekuka (IDMS) invoices, ASYCUDA entries and PSRM records. Those references were stored
in different places. The audit case reference was only on `Vati RSL Referral`, and the claim ID
only on the `Vati Submission` that it opened. Each Vati surface had to know where to look.
ADR-006 already has one register for cross-service references, `Vati Service Link`, with tenant
and conflict rules enforced in the database.

Decision:

1. **RSL systems are services in the register.** `ids.canonical.SERVICES` adds
   `online_refunds`, `audit_system`, `psrm`, `etax`, `idms` and `asycuda`. The record types
   follow the TAX-111 source contracts:
   - `online_refunds.claim` and `audit_system.audit_case`: at most one per case;
   - `etax.vat_return|wht_return|cit_return|paye_roster`, `idms.invoice`, `asycuda.entry`
     and `psrm.objection|payment`: many per case.

   The ADR-006 rules apply unchanged:
   - one RSL record maps to one case in a tenant;
   - the same reference in another tenant is a separate record and cannot be probed;
   - replays are no-ops;
   - a second claim or audit case on a case is refused.

   A link only records which RSL record a case is about. Vati never writes to RSL through it.
2. **A TIN is not a link.** A taxpayer has many cases, so a TIN cannot be unique per case.
   The card reads it from the case's taxpayer, masked as everywhere else (`tin_masked`). The
   full TIN is never on the card.
3. **The intake records the links.**
   - The TAX-123 referral intake links the audit case reference in the same transaction
     that opens the case.
   - The TAX-122 Online Refunds intake links the claim ID once the send has registered the
     case.

   A conflict is reported as `refused` with the register's reason, and nothing is written.
   Patch `v0_4.link_rsl_references` links references stored before this change through the
   same function, and counts any conflicts.
4. **One case reference card.** `vati_tax.references` builds one shape from the register.
   - Fields: tenant, Vati case ID and display ID, masked TIN, RSL claim, RSL audit case, Jube
     case, OpenAleph collection, and the other RSL records (sorted, capped at 200, with a
     truncation flag).
   - Officers get it from `GET /api/v1/cases/{case_id}/references` (`getCaseReferences`).
     This uses the same `case.read` check and TAX-142 refund-confidentiality gate as
     `getCase`.
   - Taxpayers get it from `portal.api.get_case_references`. They pass one of their own
     submissions or evidence requests, because the portal never shows a case ID.
   - The taxpayer view keeps only the references RSL or Vati already gave the taxpayer: the
     case reference, the masked TIN, the RSL claim and the RSL audit case. Jube, OpenAleph
     and the RSL record list are `null`/empty, exactly as when they are absent.
5. **Contract.** `ServiceLink.service` accepts `ServiceName` or the new `RslServiceName`, so
   `getCase` returns RSL links too. Event and error `service` fields are unchanged: RSL does
   not post events to Vati.

Alternatives and tradeoff:

- A separate RSL reference DocType: rejected. It would be a second register with its own
  tenant and conflict rules, the problem ADR-006 removed.
- Put the TIN in the register as a `psrm.taxpayer` link: rejected. It would either break
  "one record, one case" or allow only one case per taxpayer.
- Read the claim and audit references from their source records when building the card:
  rejected. Every surface would then depend on each intake's storage, and the card would
  miss references recorded by future adapters.

Official sources and version/date checked:
None from RSL. The system names and record kinds are the TAX-111 assumptions (ADR-040,
docs/reference/RSL_LANDSCAPE.md, unverified).

Verification performed (or named implementation task):
- `ids/test_canonical.py` (Frappe-free) covers:
  - each RSL type accepted, and unknown types and a TIN link refused;
  - one claim and one audit case per case;
  - one reference refused for a second case;
  - many returns allowed per case;
  - tenant separation.
- `references/test_rules.py` covers the officer and taxpayer views: hidden references look
  the same as absent ones, unacknowledged links are skipped, and the record list is capped.
- `tests/contracts/test_case_references.py` and example 49 cover the fixture operation:
  contract-checked, with not-found, other-tenant and missing-tenant refusals.
- Bench tests on a disposable site:
  - `rsl_referrals`: links recorded and replayed, the backfill patch, and the officer card
    across tenants;
  - `rsl_intake`: claim links recorded and replayed, the officer card, and the taxpayer card
    through the portal, with other taxpayers' submissions and unknown requests not found;
  - `api_v1`: the route is contract-checked, RSL links appear on `getCase`, another tenant
    gets 404 and a taxpayer gets 403.

Consequences and revisit trigger:
- TAX-134/135 can show RSL references in Jube and OpenAleph from this register.
- The auditor app labels RSL links as RSL-owned. A visible reference card component is left
  to the frontend tasks that place it.
- Revisit when RSL publishes identifiers through WSO2 that differ from the assumed shapes, or
  when a case needs more than one RSL claim.
