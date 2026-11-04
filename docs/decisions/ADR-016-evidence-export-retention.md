# ADR-016: Evidence export manifest, retention metadata and access audit

Status: proposed (TAX-062, pending review)
Owner and task: Edga (owner), TAX-062; implemented by edga-claude-code-20261104-062
Date: 2026-11-04

Context and smallest prototype need: REQUIREMENTS.md lists privacy, access, retention and operational controls (TAX-029, 032, 057, 059, 062–063). TAX-032 (ADR-011) stores evidence originals with a server-computed SHA-256 and extraction revisions, and defers retention. TAX-059 (ADR-015) records reads, approvals and refusals in a per-tenant hash chain, and leaves "ledger retention and export" to this task. Nothing yet controls evidence leaving a case: no request, no human authorisation, no record of what was handed over. TAX-063 (backup/restore) and TAX-072/076 (EOI and referral packages) build on this.

Decision:

- **Export is a manifest, not a file transfer.** `vati_tax.exports` records a request to hand named evidence of one case to a named recipient for a stated purpose. On approval it freezes a manifest. For each evidence version, the manifest holds the evidence ID, taxpayer, version, lineage, supersedes pointer, document SHA-256, media type, size, document type, source, receipt time, the latest extraction revision (ID, revision, status, extractor and version, the SHA-256 it read, and `matches_document`) and retention metadata. It also carries the request, the approver and its own `manifest_sha256` over everything else. It contains no document content, extracted text, file path or URL (`content_included: false`). Copying files out stays a human-controlled act outside the prototype; the manifest is the authorised, verifiable description of what may be handed over.
- **Workflow and authority.** Three new human-only actions are added to the TAX-029 matrix:
  - `export.request` (auditor, specialist, supervisor);
  - `export.approve` (supervisor; separation of duties: never the requester);
  - `export.read` (auditor, specialist, supervisor; only the requester and the approver may read a manifest).

  A request names 1 to 200 evidence items (no whole-case export), a purpose (`supervisory_review`, `specialist_review`, `taxpayer_copy`, `authorised_disclosure`; these are internal reasons, not legal powers), a recipient (3 to 200 characters) and a reason (20 to 2,000 characters). An unanswered request lapses after 72 hours. Approval and rejection carry `expected_version`, and rejection needs a reason. Approval re-checks the scope against the case's current evidence before freezing the manifest. The manifest is readable for 24 hours after approval.
- **Unauthorised records are denied.** Every requested item must be evidence of that case (registered for it, or linked through a TAX-030 service link) in the caller's tenant. Unknown evidence, another case's evidence and another tenant's evidence are all refused with the same `not_found`, so a request cannot probe them. The tenant check is repeated on the evidence list itself, in case a query ever leaks a row. The pilot exports only records marked `synthetic`.
- **Access audit.** Every step is sealed into the TAX-059 ledger under subject type `export`: `export.requested` (write), `export.approved`/`export.rejected` (approval), `export.read` (read), and every refusal as `<action>.denied` with its code. Refused self-approvals count towards the existing `repeated_separation_of_duties_attempts` review signal. Supervisors see all of this through the existing ledger API, filtered by case.
- **Storage.** `Vati Evidence Export` carries `tenant_id`, so the wildcard tenant hooks narrow it. Its controller freezes the request fields and only allows `requested → approved | rejected`, with a version bump. It requires a manifest exactly when approved and checks it against `manifest_sha256`, refuses a decider equal to the requester, and refuses deletes. Desk read is limited to System Manager: the stored manifest must be read through the API, which applies the requester/approver rule and records the read.
- **Retention is metadata, never deletion.** No RSL record-retention period has been verified, so the schedule `pilot-synthetic-unverified-1` sets no disposal date by default. Every record is marked `policy_verified: false` and `disposal: manual_review_only`. A site may set `vati_retention_days` per class (`evidence`, `export_manifest`) for rehearsal, which only adds a `review_after` date. Unknown classes and invalid values are refused rather than ignored. **The audit ledger is permanent:** it can never be given a disposal date, because its hash chain must stay verifiable for the life of the site. Ledger export is the existing supervisor read API plus `verify_ledger`'s `head_hash`; no separate ledger file export is added.

Alternatives and tradeoff:

- Produce a ZIP of the files: the most convenient for recipients, but it creates a second, uncontrolled copy of evidence in the prototype and needs malware, encryption and transport decisions that belong to a real deployment.
- Let any supervisor read any manifest: simpler, but it widens who learns what left the case. Supervisors still see that an export happened, who approved it and its manifest hash in the ledger.
- Configurable retention with automatic disposal: rejected for the pilot. Deleting records against an unverified schedule risks destroying evidence that real rules require keeping, and a deletion path would also have to protect ledger verifiability.

Official sources and version/date checked (2026-11-04): this repository's REQUIREMENTS.md, PROJECT_SCOPE.md, ADR-001, ADR-007, ADR-011 and ADR-015; Frappe Framework v16.54.0 at the commit in `integration/frappe-framework.lock.json` (same pinned source as ADR-011/013/015). No RSL legal retention or disclosure source was consulted, and none is claimed. Both retention and the purpose list need subject-matter review before real data.

Verification performed: 27 Frappe-free tests in `exports/test_service.py` cover:

- refusal and recording of other-case, other-tenant and unknown evidence, including evidence leaked by a faulty source;
- refused and recorded requests by a taxpayer or service;
- self-approval refused and signalled; auditor, specialist and service approvals refused;
- rejection reasons, stale, decided and lapsed requests, and scope changes before approval;
- manifest hashes, lineage, extraction provenance and mismatch flagging, and tamper detection;
- requester/approver-only reads and the 24-hour expiry;
- an intact ledger after the workflow, the retention schedule rules, and the policy matrix.

Removing separation of duties, the tenant filter or the reader check each makes these tests fail. The full backend suite (269 tests, 37 site-backed skipped) and repository-wide mypy pass. Bench tests (`exports/test_exports_bench.py`) are written for `scripts/frappe_dev.py verify` and were **not run** in this session, because Docker Desktop does not start on this machine.

Consequences and revisit trigger: TAX-063 can include export records in backup/restore rehearsals. TAX-072 (EOI) and TAX-076 (referral packages) should reuse this request/approve/manifest path rather than add their own export. Revisit before real data: verified RSL retention periods and legal holds, disclosure purposes and their authority, and a controlled file-transfer step with encryption and malware scanning.
