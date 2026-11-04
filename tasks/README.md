# Task backlog

Human owner = accountability. AI agent = implementer recorded in `claimed_by`. TAX-001–016 are planning epics; claim leaf tasks TAX-018 onward. TAX-017 tracks workspace setup. Run `python3 scripts/task.py list --owner NAME --ready` to find eligible work. Future phases are planned, not required for the four-day demo.

| ID | Owner | Phase | Task | Dependencies |
|---|---|---|---|---|
| [TAX-018](TAX-018.json) | Keletso | foundation | Versioned integration API and event contract | none |
| [TAX-019](TAX-019.json) | Edga | foundation | Frappe custom application and site installation | none |
| [TAX-020](TAX-020.json) | Leseli | foundation | Auditor shell and shared visual components | none |
| [TAX-021](TAX-021.json) | Kananelo | foundation | Frappe taxpayer portal shell | none |
| [TAX-022](TAX-022.json) | Gupta | foundation | LangGraph state and tool interface | none |
| [TAX-023](TAX-023.json) | Kananelo | foundation | Frontend test harness and accessibility baseline | none |
| [TAX-024](TAX-024.json) | Yellow | foundation | Five pilot scenarios and expected outcomes | none |
| [TAX-025](TAX-025.json) | Keletso | foundation | Jube isolated development deployment | none |
| [TAX-026](TAX-026.json) | Keletso | foundation | OpenAleph isolated development deployment | none |
| [TAX-027](TAX-027.json) | Keletso | foundation | yente synthetic dataset deployment | none |
| [TAX-028](TAX-028.json) | Keletso | foundation | Docling extraction deployment | none |
| [TAX-029](TAX-029.json) | Edga | foundation | Frappe identity, roles and tenant permissions | TAX-019 |
| [TAX-030](TAX-030.json) | Edga | foundation | Canonical IDs and external service mapping | TAX-018, TAX-019 |
| [TAX-031](TAX-031.json) | Edga | pilot | Submission and tax-domain DocTypes | TAX-029, TAX-030 |
| [TAX-032](TAX-032.json) | Edga | pilot | Evidence storage, extraction records and provenance | TAX-029, TAX-030 |
| [TAX-033](TAX-033.json) | Edga | pilot | Durable outbox and idempotent workers | TAX-030 |
| [TAX-034](TAX-034.json) | Keletso | pilot | Jube case lifecycle adapter | TAX-018, TAX-025, TAX-030 |
| [TAX-035](TAX-035.json) | Keletso | pilot | Jube explainable tax-risk configuration | TAX-025, TAX-024 |
| [TAX-036](TAX-036.json) | Leseli | pilot | OpenAleph and FtM tax entity mapping | TAX-026, TAX-030, TAX-032 |
| [TAX-037](TAX-037.json) | Keletso | pilot | yente candidate review adapter | TAX-027, TAX-036 |
| [TAX-038](TAX-038.json) | Keletso | pilot | Docling extraction adapter | TAX-028, TAX-032, TAX-033 |
| [TAX-039](TAX-039.json) | Keletso | pilot | Frappe to Jube submission and state bridge | TAX-031, TAX-033, TAX-034, TAX-035 |
| [TAX-040](TAX-040.json) | Leseli | pilot | Auditor case queue and taxpayer profile | TAX-020, TAX-018 |
| [TAX-041](TAX-041.json) | Leseli | pilot | Evidence viewer and economic relationship view | TAX-040, TAX-036 |
| [TAX-042](TAX-042.json) | Leseli | pilot | Human decision and specialist referral UI | TAX-040, TAX-046 |
| [TAX-043](TAX-043.json) | Kananelo | pilot | Claim form and validated document upload | TAX-021, TAX-031, TAX-032 |
| [TAX-044](TAX-044.json) | Kananelo | pilot | Pre-submission reconciliation and correction UI | TAX-043, TAX-048 |
| [TAX-045](TAX-045.json) | Kananelo | pilot | Evidence request responses and safe tracking | TAX-043, TAX-046 |
| [TAX-046](TAX-046.json) | Leseli | pilot | Human decisions and evidence request authorization | TAX-029, TAX-034, TAX-032 |
| [TAX-047](TAX-047.json) | Gupta | pilot | Persistent checkpoints and authenticated human resume | TAX-022, TAX-046 |
| [TAX-048](TAX-048.json) | Gupta | pilot | VAT refund reconciliation and three lanes | TAX-022, TAX-024, TAX-018 |
| [TAX-049](TAX-049.json) | Gupta | pilot | Related-party service and persistent-loss playbook | TAX-048, TAX-036 |
| [TAX-050](TAX-050.json) | Gupta | pilot | Imported-services VAT and withholding evidence playbook | TAX-048, TAX-024 |
| [TAX-051](TAX-051.json) | Gupta | pilot | Employment versus consultancy specialist playbook | TAX-047, TAX-024 |
| [TAX-052](TAX-052.json) | Gupta | pilot | Legal-route registry and policy-gap gate | TAX-022, TAX-024 |
| [TAX-053](TAX-053.json) | Gupta | pilot | Evidence search and cited case briefing agents | TAX-036, TAX-038, TAX-047 |
| [TAX-054](TAX-054.json) | Keletso | pilot | End-to-end pipeline and degraded-service behavior | TAX-039, TAX-037, TAX-038, TAX-047, TAX-053 |
| [TAX-055](TAX-055.json) | Kananelo | pilot | Portal and auditor end-to-end regression | TAX-041, TAX-042, TAX-044, TAX-045 |
| [TAX-056](TAX-056.json) | Leseli | pilot | Agent evaluation and injection regression | TAX-049, TAX-050, TAX-051, TAX-052, TAX-053 |
| [TAX-057](TAX-057.json) | Leseli | pilot | Tenant isolation, replay and failure matrix | TAX-054, TAX-046 |
| [TAX-058](TAX-058.json) | Keletso | pilot | Five-scenario demo and measured acceptance | TAX-055, TAX-056, TAX-057 |
| [TAX-059](TAX-059.json) | Leseli | pilot | Audit ledger and segregation of duties | TAX-046 |
| [TAX-060](TAX-060.json) | Leseli | pilot | Management workload and assurance dashboard | TAX-040, TAX-059 |
| [TAX-061](TAX-061.json) | Gupta | pilot | Approved playbook and reusable tool registry | TAX-047, TAX-052 |
| [TAX-062](TAX-062.json) | Edga | pilot | Pilot data access, retention and export controls | TAX-032, TAX-059 |
| [TAX-063](TAX-063.json) | Keletso | pilot | Deployment configuration, backup and restore rehearsal | TAX-054, TAX-062 |
| [TAX-064](TAX-064.json) | Gupta | expansion | Auditor action observation and workflow proposals | TAX-061, TAX-059 |
| [TAX-065](TAX-065.json) | Edga | expansion | Workflow approval, versioning and rollback | TAX-064 |
| [TAX-066](TAX-066.json) | Leseli | expansion | Risk calibration and drift evaluation | TAX-056, TAX-059 |
| [TAX-067](TAX-067.json) | Gupta | expansion | Taxpayer baselines and peer-group intelligence | TAX-066, TAX-024 |
| [TAX-068](TAX-068.json) | Gupta | expansion | Institutional knowledge retrieval | TAX-053, TAX-062 |
| [TAX-069](TAX-069.json) | Edga | expansion | Objection, appeal and final-outcome records | TAX-046, TAX-059 |
| [TAX-070](TAX-070.json) | Gupta | expansion | Outcome and appeal feedback loop | TAX-068, TAX-069, TAX-066 |
| [TAX-071](TAX-071.json) | Gupta | expansion | EOI evidence-gap and request drafting agent | TAX-053, TAX-052 |
| [TAX-072](TAX-072.json) | Edga | expansion | EOI approval and response evidence lifecycle | TAX-071, TAX-062 |
| [TAX-073](TAX-073.json) | Keletso | expansion | Tax/customs multi-value reconciliation | TAX-048, TAX-018 |
| [TAX-074](TAX-074.json) | Keletso | expansion | Scheme, beneficial ownership and intermediary graph | TAX-036, TAX-037 |
| [TAX-075](TAX-075.json) | Gupta | expansion | Emerging network pattern review | TAX-074, TAX-067 |
| [TAX-076](TAX-076.json) | Edga | expansion | Controlled serious-case referral package | TAX-062, TAX-052 |
| [TAX-077](TAX-077.json) | Leseli | expansion | Policy-gap and integrity management views | TAX-052, TAX-059, TAX-069 |
| [TAX-078](TAX-078.json) | Kananelo | expansion | Recurring submission-error guidance | TAX-044, TAX-069 |
| [TAX-079](TAX-079.json) | Keletso | future | Lawful payment-channel extension contracts | TAX-073, TAX-074 |
| [TAX-080](TAX-080.json) | Gupta | future | Additional tax-domain playbook extension design | TAX-061, TAX-070 |
| [TAX-081](TAX-081.json) | Leseli | expansion | Pilot operational acceptance and performance baseline | TAX-058, TAX-060, TAX-063 |
| [TAX-082](TAX-082.json) | Kananelo | expansion | User onboarding and officer usability validation | TAX-055, TAX-060 |

| [TAX-083](TAX-083.json) | Keletso | foundation | Prototype engineering standards and checks | none |
