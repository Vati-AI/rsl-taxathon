# Team task index

JSON task files are canonical. Use `scripts/task.py` to claim and update; see `AGENTS.md`. Dependencies must be DONE before starting. All day numbers are relative to kickoff.

| ID | Owner | Task | Day | Depends on |
|---|---|---|---|---|
| [TAX-001](TAX-001.json) | Leseli | Auditor dashboard and case shell | 1 | — |
| [TAX-002](TAX-002.json) | Leseli | Auditor evidence and human decisions | 3 | TAX-001, TAX-006, TAX-008 |
| [TAX-003](TAX-003.json) | Kananelo | Taxpayer portal and submission shell | 1 | — |
| [TAX-004](TAX-004.json) | Kananelo | Evidence responses and submission API integration | 3 | TAX-003, TAX-006 |
| [TAX-005](TAX-005.json) | Edga | Canonical model and API contract | 1 | — |
| [TAX-006](TAX-006.json) | Edga | Case lifecycle and access-controlled APIs | 2 | TAX-005 |
| [TAX-007](TAX-007.json) | Gupta | LangGraph workflow and tool contracts | 1 | — |
| [TAX-008](TAX-008.json) | Gupta | VAT and related-party investigations | 2 | TAX-007, TAX-010, TAX-012 |
| [TAX-009](TAX-009.json) | Jane | Frontend QA plan and browser fixtures | 1 | — |
| [TAX-010](TAX-010.json) | Yellow | Synthetic scenarios and expected outcomes | 1 | TAX-005 |
| [TAX-011](TAX-011.json) | Keletso | Shared integration contracts and dev connectivity | 1 | — |
| [TAX-012](TAX-012.json) | Keletso | Docling, FtM, yente and OpenAleph adapters | 2 | TAX-011, TAX-005 |
| [TAX-013](TAX-013.json) | Keletso | Jube risk and Frappe workflow adapters | 2 | TAX-011, TAX-005 |
| [TAX-014](TAX-014.json) | Jane | End-to-end frontend regression and bug fixes | 4 | TAX-002, TAX-004, TAX-009 |
| [TAX-015](TAX-015.json) | Yellow | AI safety, data and integration regression | 3 | TAX-008, TAX-010, TAX-013 |
| [TAX-016](TAX-016.json) | Keletso | Integrated demo and release review | 4 | TAX-014, TAX-015 |
