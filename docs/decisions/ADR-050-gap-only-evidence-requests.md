# ADR-050: Evidence requests ask only for the lines RSL cannot evidence

Status: accepted
Owner and task: Gupta, TAX-127 (implements ADR-043 §4 "ask only for the gap")
Date: 2026-11-12

Context and smallest prototype need:
ADR-043 §4 says Vati never asks a taxpayer for data RSL already holds. Evidence requests
(TAX-045/046) were free text: an officer typed document types and descriptions. The TAX-112
line match already knows, per claimed input-VAT line, whether Lekuka or ASYCUDA evidences it
(`matched`), whether RSL holds the taxpayer's uploaded copy (`unintegrated_supplier`), and
which lines have a gap (`supplier_gap`, `unmatched`). The lane proposal records that result
per line. The prototype needs a request built from it, approved by an officer, and answered
line by line.

Decision:

1. **Proposal** (`vati_tax.evidence_gaps`, `propose_gap_request`). It is computed and not
   stored. It is built from RSL's own claim lines (the Online Refunds claim read through the
   TAX-111 source) and the latest lane proposal's `line_match`:
   - lines RSL already evidences are listed as **already held**, with the source named in
     words (Lekuka e-invoicing records, ASYCUDA customs records, or the copy uploaded to the
     Online Refunds Application). They are not asked for;
   - each other line becomes one item (`invoice` or `import_declaration`) whose description
     names the invoice or customs entry, supplier, date and VAT, and says what is missing in
     fixed wording. It is answered with `confirm`, `dispute` or `attach_proof`.

   A proposal carries no score, lane, finding, officer or internal status code. An allowlist
   check refuses anything else. It is refused when the match does not cover every claim line
   (the lane proposal is out of date), when more than 20 lines would be asked (the TAX-046
   item limit), or when RSL's claim cannot be read. A `proposal_digest` pins what the officer
   reviewed.
2. **Officer approval** (`approve_gap_request`). It rebuilds the proposal and refuses it with
   409 if the digest changed. It refuses a proposal with nothing to ask. Otherwise it records
   the request through the TAX-046 authorisation (auditor or supervisor, case version,
   idempotency, audit ledger, TAX-142 refund gate, TAX-124 notice to RSL). In the same
   transaction it writes `line_items` (per item: the line and its answer options),
   `already_held` and `gap_proposal_digest` on the `Vati Evidence Request`; the controller
   freezes them afterwards. Nothing reaches the taxpayer before approval.
3. **Taxpayer view and answers** (TAX-045 rules, extended). The portal view adds, for such a
   request, `already_held` and per item `line` and `answer_options`, each from an allowlist.
   An answer to a line item names one option:
   - `confirm` needs nothing more;
   - `dispute` needs a reason;
   - `attach_proof` needs at least one of the taxpayer's own documents.

   Ordinary requests, their answers and their stored digests are unchanged, and an
   `answer` on an ordinary item is refused.

Alternatives and tradeoff:

- Store the proposal as its own record: deferred. A computed proposal plus a digest is enough
  to make approval exact, and nothing about it needs to be kept until it is approved.
- Ask a free-text question per line from the TAX-112 question codes: rejected. The codes are
  internal and their wording is not taxpayer-facing; the fixed wording table is reviewable.
- Re-run the TAX-112 match at proposal time: rejected. The officer reviews the match the
  assurance step recorded; a newer match supersedes the lane proposal and changes the digest.

Official sources and version/date checked:
None from RSL; Lekuka, ASYCUDA and Online Refunds shapes are the TAX-111 assumptions
(ADR-040, docs/reference/RSL_LANDSCAPE.md, unverified).

Verification performed (or named implementation task):
- `evidence_gaps/test_rules.py` (Frappe-free): every claim of the TAX-118 synthetic
  taxpayers, through the real TAX-112 match, gives a proposal whose items are exactly the
  `unmatched` and `supplier_gap` lines and whose already-held lines are the rest, with no
  internal words. It also covers held-by sources, gap wording, the digest, out-of-date and
  oversized inputs, the allowlist, the taxpayer view and line answers, and unchanged
  ordinary requests.
- `evidence_gaps/test_evidence_gaps_bench.py` on a disposable site:
  - the proposal stores nothing and the taxpayer sees nothing before approval;
  - the approved request reaches the portal with lines, options and the already-held lines
    and no internal data, and is answered line by line;
  - a changed line match needs a new review;
  - only officers propose and approve (taxpayer, service and auditors outside the refund team
    are refused);
  - portal claims and unmatched claims cannot be itemised.

Consequences and revisit trigger:
- The taxpayer portal UI (Keletso's lane, TAX-126) still has to render `already_held`, the
  line and the three answers; the API already returns them.
- Revisit when RSL confirms which lines it can evidence itself (ADR-043 open questions) or
  when RSL Legal decides whether a missing Lekuka invoice may be refused (TAX-112
  `hard_rule_mode`).
