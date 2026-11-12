# TAX-126 evidence: checks on evidence responses before they are sent

The taxpayer portal's evidence-response page now checks the uploaded documents before
the response is sent. It also shows the taxpayer's reminders from earlier claims.
Both are guidance only: nothing they say, and no failure to load them, stops the
response being sent.

All data and screenshots are synthetic.

## What changed

### Checks per answered item (backend)
`vati_tax/responses/checks.py` is pure and has no Frappe import. For each document
uploaded for an item, it reads the latest TAX-038 Docling extraction and reports one or
more of these, with fixed wording:

| Check | Code | When |
| --- | --- | --- |
| Legibility | `document_not_readable` | The extraction failed, or found no text |
| | `document_partly_read` | Docling read only part of the document |
| | `document_still_being_read` | There is no extraction yet |
| Period | `document_period_differs` | The document has dates, and none falls in the expected month (see below) |
| Amount | `document_amount_not_found` | A TAX-127 line item's VAT amount is not among the amounts in the document |
| Invoice number | `document_invoice_number_not_found` | The line's invoice number is not in the document (case and punctuation ignored) |
| None of the above | `document_matches_request` | The message says what was compared |

**Expected month for the period check:**
- for a TAX-127 line item, the month of the requested invoice;
- otherwise, the VAT period of the claim the case was opened for.

**How dates and amounts are read:**
- Dates: ISO, day-first numeric (`14/03/2025`, `14-3-2025`, `14.03.2025`), and written
  dates (`14 March 2025`, `Mar 2025`).
- Amounts: figures with cents (`1,500.00`, `1 500.00`, `1500.00`).

**How the document text is handled:**
- It is treated as data. Only the dates and amounts the checks found are quoted back;
  a test proves that injected text is not echoed.
- When a reminder topic covers a check (period, invoice totals, missing invoices), the
  check is marked with that reminder, as TAX-044 does for claim prompts.

### Review endpoint
`vati_tax.portal.api.review_evidence_response` (POST):
- has the same scope as sending: the caller's own open request and own documents;
- stores nothing;
- returns `blocks_sending: false`;
- is never called by `respond_to_evidence_request`, and a test enforces that.

### Reading documents before they are sent
A response's documents join the case only when the response is sent, so the case
pipeline (TAX-087) had not read them yet.
- The review queues one Docling read for each unread document:
  `pipeline.store.request_draft_extraction`, a background job deduplicated per document,
  run as the pipeline service.
- The result is an ordinary append-only extraction revision. When the response is sent,
  `handle_evidence_received` finds that revision and does not convert the file again.
- Until the read finishes, the item says the document is still being read, and
  "Check again" re-runs the checks.

### Portal
The "Review and send" step has a **Before you send** region:
- a summary line (for example, "2 things to look at… you can still send your
  response");
- each item's guidance, with things to look at first;
- "Also noted on an earlier claim" where the server marked a reminder;
- a "Change item N" button that returns to that step.

How the checks run:
- They run when the taxpayer reaches the step, and again only when the documents
  change, or when the taxpayer chooses "Check again".
- Items answered with a reason are not checked.
- If the checks cannot run, the region says so, and sending is unchanged.

### Reminders on the response page
- **Which reminders:** the TAX-078 reminders whose topic fits the requested document
  types (invoice: missing invoices and invoice totals; bank statement: payment details;
  contract: contract documents), plus any reminder the server marked on a check.
- **How they show:** a closed-by-default disclosure ("2 reminders from your earlier
  claims"), so the response steps stay in view.
- **If they cannot load:** a note says "You can still answer and send this request".
- **First-time claimants** see nothing.

## Tests

### Backend
- `backend/vati_tax/vati_tax/responses/test_checks.py`, 15 Frappe-free tests covering:
  - each check;
  - date and amount formats;
  - line month versus claim period;
  - items answered with a reason, unknown items and unknown documents;
  - multiple documents;
  - reminder marking;
  - neutral wording;
  - no echo of document text;
  - sending never runs the checks.
- `test_responses_bench.py` has three new bench tests:
  - guidance from recorded extractions, with nothing stored and the response still sent;
  - an unread document queued once, deduplicated;
  - scope: another taxpayer's request or document, and a closed request.
- **Not run.** Docker is not available in this session, so there is no Frappe site.
  They need `scripts/frappe_dev.py verify`.

### Portal
- `apps/taxpayer-web/src/lib/responseChecks.test.ts`: 6 vitest tests.
- `tests/frontend/response-checks.spec.ts`: 18 passed (6 tests at 1440, 1024 and 390),
  covering:
  - guidance per item, with a marked reminder;
  - "Change item" and a re-check after replacing a document;
  - sending with an unreadable document;
  - a document still being read, then "Check again";
  - no repeat review for the same documents;
  - checks unavailable: the response still sends, and reason-only items are not sent
    for checking;
  - only relevant reminders;
  - reminders unavailable: the response still sends;
  - no panel for a first-time claimant.
- axe (WCAG 2.1 A/AA, serious and critical) and horizontal-overflow checks run in the
  same tests.
- The browser mock (`support/sessions.ts`) has no Docling, so it keys outcomes off the
  uploaded file name. The real checks are the backend tests above.
- These specs still pass with the change: `responses.spec.ts`, `inbox.spec.ts`,
  `reminders.spec.ts`, `taxpayer.spec.ts` and `claim.spec.ts`.

## Screenshots
Each screenshot is at 1440, 1024 and 390 px:
- `review-with-guidance`
- `review-still-reading`
- `review-clear`
- `review-checks-unavailable`
- `reminders` (disclosure opened)
- `reminders-unavailable`

The reading pane scrolls inside the page, so the capture grows the viewport to the
pane's content.
