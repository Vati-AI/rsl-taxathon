# Project scope

## Outcome

Build a four-day demonstration of: taxpayer submission → document processing and reconciliation → explainable risk signal → case preparation → AI investigation → auditor review → recorded outcome.

The source brief is “Vati AI RSL Taxaton Solution Brief”, read from the referenced Challenge 2 conversation on 2026-11-03. The role split and taxpayer portal additions come from that conversation. Uploaded source documents are reference material; they are not redistributed here.

## Prototype commitments

- Taxpayer portal: submission, documents, pre-submission missing-information checks, evidence requests/responses and safe status tracking.
- Auditor workspace: prioritized cases, taxpayer profile, explanation of selection, reconciled vs unresolved amounts, evidence citations and human actions.
- VAT refund and related-party investigation paths; imported-service and employment/consultancy ambiguity must be represented as review questions.
- Traceable evidence with document hash/page/record references, source provenance and immutable audit history.
- Three required outcomes: explained legitimate refund, unresolved risk requiring investigation, ambiguous case requiring specialist review.
- Synthetic data only for the Taxathon. Separate missing evidence from adverse findings. No automatic assessments, penalties, enforcement, refund payment or legal conclusions.
- Approved workflow execution: agents gather/compare/summarize; officers authorize outward evidence requests and decisions.

## Stretch work

Supervised learning from reviewed outcomes; propose recurring auditor workflows for supervisor approval; peer comparisons; evidence export; additional related-party scenarios. No automatic policy changes, self-training, autonomous enforcement or live Lekuka/IDMS access in this scaffold.

## Four-day sequence

1. Contract, data model, dev setup, frontend shells and synthetic fixtures.
2. Submission/case APIs, service adapters and first investigation workflow.
3. Complete the human review loop and run all three scenarios.
4. Regression fixes, reproducible demo, evidence pack and limitations.

## Acceptance

Every displayed finding links to evidence or explicitly states an evidence gap. Legitimate scenarios can be cleared by an officer. Ambiguous scenarios pause for specialist review. Duplicate processing does not duplicate cases. Tenant access tests prevent one taxpayer reading another's records. No failure of an upstream system is reported as a successful investigation.

Report actual measured case preparation time, citation coverage, false positives on known scenarios, unresolved evidence count and review completion; do not invent improvement percentages. Legal routes and statutory citations require RSL subject-matter validation before use.
