# ADR-020: Management summary in the Vati API

Status: proposed (TAX-060; contract owner Keletso and backend owner Edga to review)
Owner and task: Leseli (auditor-frontend), TAX-060; implemented by leseli-claude-20261104-060.
Date: 2026-11-04

(ADR-018 is used on the open TAX-042 branch. ADR-019 is left for TAX-041, which is in progress, so this takes ADR-020.)

Context and smallest prototype need: TAX-060 asks for supervisors to see workload, elapsed time, unresolved evidence, false positives and legal-route coverage "from measured data", with checked denominators, missing-data states and supervisor-only visibility. The figures come from four authorities: Jube for open cases, Frappe for decisions and evidence requests (TAX-046), the findings store for legal routes (TAX-052), and the TAX-059 audit ledger. The Vati contract had no operation for any of this. Building the view from per-case calls would need every case and finding to reach the browser. It would also put the denominator rules in the client, and officers would be able to read the inputs.

Decision:

- `getManagementSummary` (`GET /api/v1/management/summary`, optional `window_days` 1 to 365, default 30). Only principals with the TAX-029 supervisor role in the selected tenant may call it. Anyone else gets `403 forbidden` and no data. The usual ADR-003 rules apply: the `X-Vati-Tenant-ID` header, `ErrorResponse` and an echoed correlation ID.
- The response holds counts only, never taxpayer or document content. Each ratio is a `Ratio` (`numerator`, `denominator`), so the denominator is always visible. A denominator of 0 means there was nothing to measure, and the client shows "No data", never 0%. Each elapsed time is a `DurationStats`: `count`, `excluded`, then median, nearest-rank 90th percentile and maximum as decimal-string hours. These are null when nothing was measured.
- Point-in-time figures (at `generated_at`):
  - workload: open cases by status and by assigned officer, plus unassigned cases;
  - open-case age;
  - outstanding and overdue evidence requests.
- Window figures:
  - cases closed in the window, by approved TAX-046 outcome. A closure without a recorded outcome is counted, left out of both ratios and listed in `missing_data`. "No further action" over closures with an outcome is the false-positive measure: the review found nothing to correct. It is documented as a measure of case selection, never of an officer or taxpayer;
  - time to close.
- Legal-route coverage: reviewer-verified findings over all findings on open cases and on cases closed in the window. `unverified` and `no_verified_route` (policy gap) are given separately, and the three always add up to the denominator.
- `assurance` comes from the TAX-059 reads (`ledger_summary` and `integrity_signals`) for the same window. It gives the chain check, the entry, override and refusal counts, and distinct actors. Review signals are counted by kind only. Signals about the viewing supervisor are withheld by the TAX-059 rule, and only their number is shown. Per-person detail stays in the restricted integrity views (TAX-077). If the ledger cannot be read, the result is `available: false` with a reason, never zeros.
- `missing_data` lists every known gap, for example a closed case without a creation time. A partial figure is never presented as complete.
- `scripts/fixture_api.py` measures the summary from `fixtures/auditor_queue.json` (open cases) and the new synthetic `fixtures/management.json` (closed cases, outcomes, evidence requests, findings, ledger events, and officer names and ledger logins). It seals the ledger events and reads them with the real Frappe-free `vati_tax.audit.ledger` functions. The fixture clock is the file's `as_of`, so the figures stay fixed.
- In fixture mode, the acting officer and their role come from TAX-042's fixture-only `X-Vati-Fixture-Role` header (the `case_review.json` principals), so there is no separate mechanism. The default `auditor` and the `specialist` get `403`; only `supervisor` (Synthetic Supervisor C) sees the figures. The app shows the "Supervision" nav entry only when acting as supervisor, and reloads the dashboard when the role changes. The server still decides access. A real server ignores the header and takes the role from authentication (TAX-029).
- Contract example 25 is generated from the fixture response as the supervisor, and a test keeps the two equal. Example 26 is the refusal.

Alternatives and tradeoff:

- Build the figures in the browser from `listCases`, `listFindings` and the Frappe ledger reads. That needs many requests and leaves the denominators to the client, and officers could read the inputs. The server-side summary keeps the rules in one place.
- Call the Frappe `vati_tax.audit.api` methods straight from the auditor app. That is a second client authority and session model next to `/api/v1` (ADR-003). The summary goes through the Vati boundary instead, and the Vati service calls the ledger.
- Show per-officer integrity signals on the dashboard. Rejected: ADR-015 treats signals as review prompts for an authorised supervisor. A management overview naming people would read as a finding. Counts by kind are enough to prompt a review.
- Use "false positive rate" as the field name. The field is named after the decision outcome, `no_further_action`, and labelled "false positives" only in the UI, with an explanation. This keeps the contract tied to the TAX-046 outcome list.

Official sources and version/date checked (2026-11-04):
- OpenAPI Specification 3.1.1 (https://spec.openapis.org/oas/v3.1.1.html) for the query parameter and `anyOf` response shape, as used by ADR-003 and ADR-005;
- in this repository: ADR-013 (decision outcomes), ADR-015 (ledger read contract and signal rules) and docs/DESIGN_STANDARD.md (tables, no decorative charts or invented metrics).

Verification performed:
- `integration/api/contract.py` validates the contract with 26 HTTP examples (TAX-042's included), 2 of them new: 25 (summary) and 26 (forbidden for an officer).
- `tests/contracts/test_management_summary.py` (18 tests) covers:
  - supervisor-only access: auditor (the default) and specialist refused with no data, an unknown fixture role rejected, a supervisor without a ledger identity refused, other tenant, missing tenant, out-of-range windows;
  - schema conformance for 1, 7, 30, 90 and 365 days;
  - breakdowns adding up to totals, and ratios sharing one denominator that excludes missing outcomes;
  - a window that moves closures and ledger counts but not point-in-time figures;
  - median and nearest-rank p90;
  - excluded timestamps listed in `missing_data`, zero denominators for empty records, and a missing officer name reported;
  - signals about the viewer withheld and no logins in the response;
  - a tampered chain reported as broken with a `ledger_integrity` signal;
  - an unavailable ledger shown as a gap;
  - example 21 equal to the fixture response.
- The client parser rejects a response whose breakdowns or ratios don't add up, or one that isn't synthetic (vitest). Browser tests cover the shown states.

Consequences and revisit trigger: the Vati service must implement this from Jube, Frappe, the findings store and the TAX-059 reads, recording the ledger read as TAX-059 requires. TAX-077 adds the restricted per-signal views. TAX-066 may change the signal thresholds; this contract only counts signals. Revisit if supervisors need figures per refund lane or per playbook, or if a deployment needs a scope wider than one tenant.
