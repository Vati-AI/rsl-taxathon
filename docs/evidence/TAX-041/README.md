# TAX-041 review evidence: evidence viewer and relationship view

Screens for the reviewer's visual check against docs/DESIGN_STANDARD.md. They are not
approved baselines; the frontend QA owner (Kananelo) captures baselines after acceptance.

Captured 2026-11-04 with the installed Microsoft Edge (Playwright `msedge` channel, Windows),
from the built auditor app and the fixture server (`scripts/dev.py serve`, synthetic data only):

```sh
npm run build:auditor
CAPTURE_SCREENSHOTS=1 PLAYWRIGHT_CHANNEL=msedge npm run test:auditor:browser -- screenshots-evidence
```

## Screens (1440, 1024 and 390 px)

| File | What it shows |
|---|---|
| `case-evidence-*` | Evidence tab for SYN-002: document type, version (v1 "Replaced by v2"), received time, extraction state (one failed), short SHA-256 (full hash on hover and on the record page), number of citing findings. |
| `case-relationships-*` | Parties with yente match candidates (unreviewed, confirmed, rejected; "A similarity score is not identity"), ownership with three stakes marked "Uncertain: records disagree" and shown exactly as recorded (60%, 55%, 40%), payments with source links and one "Record not on file". |
| `case-findings-*` | Findings grouped as Records disagree, Evidence missing, Observed in records and Inference, each with cited sources (document, version, page or record), producer confidence ("Not legal authority"), unverified legal route and a proposed step that needs officer approval. |
| `evidence-cited-page-*` | Record page opened from a finding citation: "Cited location: Page 3", back link to the finding, full SHA-256, stored file reference, version history and "Page 3 (the location you followed)". |
| `evidence-superseded-failed-*` | Superseded v1 scan with "A newer version of this document exists" and "Text could not be extracted" (a processing problem, not an empty document). |

## States (1440 and 390 px)

`state-loading-*`, `state-empty-findings-*` (SYN-007), `state-denied-*` (tenant outside scope, no
data shown), `state-unavailable-*` (connection refused, retry offered) and
`state-evidence-not-found-*`.

## Keyboard and accessibility

- `focus-citation-link-1440.png`: visible focus on a citation link. `e2e/evidence.spec.ts`
  follows a record citation with the keyboard only (focus, Enter).
- `focus-returned-finding-1440.png`: after "Back to the finding", focus returns to the finding it
  came from.
- axe (WCAG 2 A/AA, 2.1 AA): no serious or critical violations on the evidence, relationships and
  findings tabs and on two evidence record pages (`e2e/evidence.spec.ts`).
- Wide tables scroll inside labelled, focusable regions ("Evidence on file", "Ownership records",
  "Payment records"); the 390 px captures show no page-wide horizontal scroll.
- No document text is rendered as HTML; every value is interpolated as text.
