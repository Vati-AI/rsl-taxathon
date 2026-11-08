# Task backlog

## Delivery order (ADR-038)

From 2026-11-06 Keletso owns every task, including completed tasks and epics, without exceptions. Previous owners are retained in task notes and Git history. Take the ready task with the lowest `sequence`; a later task can start when its dependencies are DONE.

TAX-111–118 are the must-have additions from the 2026-11-06 framework scope update ([FRAMEWORK.md](../docs/reference/FRAMEWORK.md), [ADR-040](../docs/decisions/ADR-040-rsl-existing-systems-integration.md)). TAX-058 and TAX-081 now also depend on TAX-118 (measured results against a baseline).

TAX-122–143 come from [ADR-043](../docs/decisions/ADR-043-vati-downstream-of-rsl-selection.md) (2026-11-07). They make RSL's systems start and receive cases, centre the taxpayer portal on evidence requests, make the open-source tools RSL-aware and add the missing Challenge 2 items (persistent VAT credit positions, in-house loss creation). TAX-122 must land before TAX-125 retires the claim form.

| Seq | Task | Title |
|---|---|---|
| 1 | [TAX-086](TAX-086.json) | Case findings record for rule and agent output |
| 2 | [TAX-090](TAX-090.json) | Vati /api/v1 service in Frappe for the auditor app |
| 3 | [TAX-121](TAX-121.json) | Run the assurance checks inside the live pipeline and re-measure through the platform |
| [TAX-122](TAX-122.json) | Keletso | pilot | Ingest refund claims from the Online Refunds Application contract into cases | TAX-111, TAX-039, TAX-121 |
| [TAX-123](TAX-123.json) | Keletso | pilot | RSL risk-based audit referral contract and fixture | TAX-111, TAX-034 |
| [TAX-124](TAX-124.json) | Keletso | pilot | Outbound contracts from Vati to RSL systems | TAX-111, TAX-086, TAX-114, TAX-046 |
| [TAX-125](TAX-125.json) | Keletso | pilot | Re-centre the taxpayer portal on evidence requests and retire the claim form | TAX-122, TAX-045, TAX-092 |
| [TAX-126](TAX-126.json) | Keletso | pilot | Checks on evidence responses before they are sent | TAX-125, TAX-044, TAX-078, TAX-038 |
| [TAX-127](TAX-127.json) | Keletso | pilot | Evidence requests that ask only for the gap | TAX-112, TAX-115, TAX-046, TAX-125 |
| [TAX-128](TAX-128.json) | Keletso | pilot | Structured transfer-pricing and multinational evidence request templates | TAX-049, TAX-116, TAX-125 |
| [TAX-129](TAX-129.json) | Keletso | pilot | Check-before-you-file service for refund claims | TAX-044, TAX-112, TAX-111, TAX-125 |
| [TAX-130](TAX-130.json) | Keletso | expansion | Response receipts, practitioner delegation and deadlines | TAX-125, TAX-029 |
| [TAX-131](TAX-131.json) | Keletso | pilot | Reframe Jube and Vati risk output downstream of RSL selection | TAX-035, TAX-123, TAX-066, TAX-067 |
| [TAX-132](TAX-132.json) | Keletso | pilot | Refund lanes read RSL status and recommend for claims in audit | TAX-114, TAX-122, TAX-124 |
| [TAX-133](TAX-133.json) | Keletso | pilot | Shared reference register including RSL systems | TAX-030, TAX-111, TAX-122, TAX-123 |
| [TAX-134](TAX-134.json) | Keletso | pilot | Make the Jube tax configuration RSL-aware | TAX-107, TAX-133 |
| [TAX-135](TAX-135.json) | Keletso | pilot | Make OpenAleph and FtM mappings RSL-aware | TAX-108, TAX-036, TAX-133 |
| [TAX-136](TAX-136.json) | Keletso | pilot | One sign-in through WSO2 Identity Server for Jube and OpenAleph | TAX-117 |
| [TAX-137](TAX-137.json) | Keletso | pilot | Navigation in both directions and one product identity | TAX-106, TAX-133 |
| [TAX-138](TAX-138.json) | Keletso | pilot | Source-system labels on every value | TAX-133, TAX-086, TAX-090 |
| [TAX-139](TAX-139.json) | Keletso | pilot | Persistent VAT credit positions: multi-period pattern and seventh scenario | TAX-048, TAX-111, TAX-118, TAX-121 |
| [TAX-140](TAX-140.json) | Keletso | pilot | In-house loss creation through intra-group financing | TAX-049, TAX-116, TAX-118 |
| [TAX-141](TAX-141.json) | Keletso | pilot | Imported services booked as expenses, through cross-border payments | TAX-115, TAX-079, TAX-118 |
| [TAX-142](TAX-142.json) | Keletso | pilot | Refund-confidentiality access rules in the pilot | TAX-029, TAX-062 |
| [TAX-143](TAX-143.json) | Keletso | pilot | Demo and tests on the RSL-first journey | TAX-122, TAX-123, TAX-125, TAX-094 |
| 4 | [TAX-110](TAX-110.json) | Reliable hosted briefing and evidence reading within free-tier limits |
| 5 | [TAX-111](TAX-111.json) | RSL source-system contracts and fixture adapters |
| 6 | [TAX-112](TAX-112.json) | Lekuka/IDMS input-VAT match in hybrid mode and sixth scenario |
| 7 | [TAX-113](TAX-113.json) | SAF-T-style source-record intake and recompute engine |
| 8 | [TAX-114](TAX-114.json) | Refund lane service targets, undisputed-portion release and RSL inputs |
| 9 | [TAX-115](TAX-115.json) | Imported-services three-way match with CMA caveat |
| 10 | [TAX-116](TAX-116.json) | Named rule register (unverified) and AfriSam policy-gap entry |
| 11 | [TAX-122](TAX-122.json) | Ingest refund claims from the Online Refunds Application contract into cases |
| 12 | [TAX-125](TAX-125.json) | Re-centre the taxpayer portal on evidence requests and retire the claim form |
| 13 | [TAX-133](TAX-133.json) | Shared reference register including RSL systems |
| 14 | [TAX-123](TAX-123.json) | RSL risk-based audit referral contract and fixture |
| 15 | [TAX-124](TAX-124.json) | Outbound contracts from Vati to RSL systems |
| 16 | [TAX-139](TAX-139.json) | Persistent VAT credit positions: multi-period pattern and seventh scenario |
| 17 | [TAX-140](TAX-140.json) | In-house loss creation through intra-group financing |
| 18 | [TAX-141](TAX-141.json) | Imported services booked as expenses, through cross-border payments |
| 19 | [TAX-142](TAX-142.json) | Refund-confidentiality access rules in the pilot |
| 20 | [TAX-126](TAX-126.json) | Checks on evidence responses before they are sent |
| 21 | [TAX-127](TAX-127.json) | Evidence requests that ask only for the gap |
| 22 | [TAX-129](TAX-129.json) | Check-before-you-file service for refund claims |
| 23 | [TAX-131](TAX-131.json) | Reframe Jube and Vati risk output downstream of RSL selection |
| 24 | [TAX-132](TAX-132.json) | Refund lanes read RSL status and recommend for claims in audit |
| 25 | [TAX-088](TAX-088.json) | AI investigation runtime on real cases |
| 26 | [TAX-108](TAX-108.json) | Configure and verify OpenAleph tax evidence and graph screens |
| 27 | [TAX-135](TAX-135.json) | Make OpenAleph and FtM mappings RSL-aware |
| 28 | [TAX-054](TAX-054.json) | End-to-end pipeline and degraded-service behavior |
| 29 | [TAX-091](TAX-091.json) | Auditor app hosted in Frappe with real officer login |
| 30 | [TAX-117](TAX-117.json) | Vati API behind WSO2 API Manager and sign-in through WSO2 Identity Server |
| 31 | [TAX-136](TAX-136.json) | One sign-in through WSO2 Identity Server for Jube and OpenAleph |
| 32 | [TAX-107](TAX-107.json) | Configure and verify Jube native tax investigation screens |
| 33 | [TAX-134](TAX-134.json) | Make the Jube tax configuration RSL-aware |
| 34 | [TAX-094](TAX-094.json) | Demo seed and reset for the five scenarios |
| 35 | [TAX-143](TAX-143.json) | Demo and tests on the RSL-first journey |
| 36 | [TAX-106](TAX-106.json) | Case-aware navigation to native investigation and administration screens |
| 37 | [TAX-137](TAX-137.json) | Navigation in both directions and one product identity |
| 38 | [TAX-138](TAX-138.json) | Source-system labels on every value |
| 39 | [TAX-109](TAX-109.json) | Protected original-document access from case evidence and citations |
| 40 | [TAX-057](TAX-057.json) | Tenant isolation, replay and failure matrix |
| 41 | [TAX-093](TAX-093.json) | Auditor screens for AI briefing and specialist opinion |
| 42 | [TAX-095](TAX-095.json) | Presentation environment on the demo laptop |
| 43 | [TAX-103](TAX-103.json) | Live-app end-to-end regression re-run |
| 44 | [TAX-063](TAX-063.json) | Deployment configuration, backup and restore rehearsal |
| 45 | [TAX-098](TAX-098.json) | Taxpayer compliance assistant agent |
| 46 | [TAX-100](TAX-100.json) | Taxpayer-scoped assistant service in Frappe |
| 47 | [TAX-099](TAX-099.json) | Taxpayer evidence-response assistant |
| 48 | [TAX-101](TAX-101.json) | Taxpayer portal assistant UI |
| 49 | [TAX-102](TAX-102.json) | Taxpayer assistant scenarios, expected outcomes and leakage tests |
| 50 | [TAX-118](TAX-118.json) | Synthetic taxpayer population, manual baseline and measured results |
| 51 | [TAX-120](TAX-120.json) | Human manual baseline and outside-reviewer scores for TAX-118 |
| 52 | [TAX-119](TAX-119.json) | Docling extraction accuracy on generated invoice and agreement PDFs |
| 53 | [TAX-058](TAX-058.json) | Five-scenario demo and measured acceptance |
| 54 | [TAX-128](TAX-128.json) | Structured transfer-pricing and multinational evidence request templates |
| 55 | [TAX-130](TAX-130.json) | Response receipts, practitioner delegation and deadlines |
| 56 | [TAX-072](TAX-072.json) | EOI approval and response evidence lifecycle |
| 57 | [TAX-076](TAX-076.json) | Controlled serious-case referral package |
| 58 | [TAX-081](TAX-081.json) | Pilot operational acceptance and performance baseline |

Human owner = accountability. AI agent = implementer recorded in `claimed_by`. TAX-001–016 are planning epics; claim leaf tasks TAX-018 onward. TAX-017 tracks workspace setup. Run `python3 scripts/task.py list --owner NAME --ready` to find eligible work. Future phases are planned, not required for the four-day demo.

| ID | Owner | Phase | Task | Dependencies |
|---|---|---|---|---|
| [TAX-018](TAX-018.json) | Keletso | foundation | Versioned integration API and event contract | none |
| [TAX-019](TAX-019.json) | Keletso | foundation | Frappe custom application and site installation | none |
| [TAX-020](TAX-020.json) | Keletso | foundation | Auditor shell and shared visual components | none |
| [TAX-021](TAX-021.json) | Keletso | foundation | Frappe taxpayer portal shell | none |
| [TAX-022](TAX-022.json) | Keletso | foundation | LangGraph state and tool interface | none |
| [TAX-023](TAX-023.json) | Keletso | foundation | Frontend test harness and accessibility baseline | none |
| [TAX-024](TAX-024.json) | Keletso | foundation | Five pilot scenarios and expected outcomes | none |
| [TAX-025](TAX-025.json) | Keletso | foundation | Jube isolated development deployment | none |
| [TAX-026](TAX-026.json) | Keletso | foundation | OpenAleph isolated development deployment | none |
| [TAX-027](TAX-027.json) | Keletso | foundation | yente synthetic dataset deployment | none |
| [TAX-028](TAX-028.json) | Keletso | foundation | Docling extraction deployment | none |
| [TAX-029](TAX-029.json) | Keletso | foundation | Frappe identity, roles and tenant permissions | TAX-019 |
| [TAX-030](TAX-030.json) | Keletso | foundation | Canonical IDs and external service mapping | TAX-018, TAX-019 |
| [TAX-031](TAX-031.json) | Keletso | pilot | Submission and tax-domain DocTypes | TAX-029, TAX-030 |
| [TAX-032](TAX-032.json) | Keletso | pilot | Evidence storage, extraction records and provenance | TAX-029, TAX-030 |
| [TAX-033](TAX-033.json) | Keletso | pilot | Durable outbox and idempotent workers | TAX-030 |
| [TAX-034](TAX-034.json) | Keletso | pilot | Jube case lifecycle adapter | TAX-018, TAX-025, TAX-030 |
| [TAX-035](TAX-035.json) | Keletso | pilot | Jube explainable tax-risk configuration | TAX-025, TAX-024 |
| [TAX-036](TAX-036.json) | Keletso | pilot | OpenAleph and FtM tax entity mapping | TAX-026, TAX-030, TAX-032 |
| [TAX-037](TAX-037.json) | Keletso | pilot | yente candidate review adapter | TAX-027, TAX-036 |
| [TAX-038](TAX-038.json) | Keletso | pilot | Docling extraction adapter | TAX-028, TAX-032, TAX-033 |
| [TAX-039](TAX-039.json) | Keletso | pilot | Frappe to Jube submission and state bridge | TAX-031, TAX-033, TAX-034, TAX-035 |
| [TAX-040](TAX-040.json) | Keletso | pilot | Auditor case queue and taxpayer profile | TAX-020, TAX-018 |
| [TAX-041](TAX-041.json) | Keletso | pilot | Evidence viewer and economic relationship view | TAX-040, TAX-036 |
| [TAX-042](TAX-042.json) | Keletso | pilot | Human decision and specialist referral UI | TAX-040, TAX-046 |
| [TAX-043](TAX-043.json) | Keletso | pilot | Claim form and validated document upload | TAX-021, TAX-031, TAX-032 |
| [TAX-044](TAX-044.json) | Keletso | pilot | Pre-submission reconciliation and correction UI | TAX-043, TAX-048 |
| [TAX-045](TAX-045.json) | Keletso | pilot | Evidence request responses and safe tracking | TAX-043, TAX-046 |
| [TAX-046](TAX-046.json) | Keletso | pilot | Human decisions and evidence request authorization | TAX-029, TAX-034, TAX-032 |
| [TAX-047](TAX-047.json) | Keletso | pilot | Persistent checkpoints and authenticated human resume | TAX-022, TAX-046 |
| [TAX-048](TAX-048.json) | Keletso | pilot | VAT refund reconciliation and three lanes | TAX-022, TAX-024, TAX-018 |
| [TAX-049](TAX-049.json) | Keletso | pilot | Related-party service and persistent-loss playbook | TAX-048, TAX-036 |
| [TAX-050](TAX-050.json) | Keletso | pilot | Imported-services VAT and withholding evidence playbook | TAX-048, TAX-024 |
| [TAX-051](TAX-051.json) | Keletso | pilot | Employment versus consultancy specialist playbook | TAX-047, TAX-024 |
| [TAX-052](TAX-052.json) | Keletso | pilot | Legal-route registry and policy-gap gate | TAX-022, TAX-024 |
| [TAX-053](TAX-053.json) | Keletso | pilot | Evidence search and cited case briefing agents | TAX-036, TAX-038, TAX-047 |
| [TAX-054](TAX-054.json) | Keletso | pilot | End-to-end pipeline and degraded-service behavior | TAX-039, TAX-037, TAX-038, TAX-047, TAX-053, TAX-087, TAX-088 |
| [TAX-055](TAX-055.json) | Keletso | pilot | Portal and auditor end-to-end regression | TAX-041, TAX-042, TAX-044, TAX-045 |
| [TAX-056](TAX-056.json) | Keletso | pilot | Agent evaluation and injection regression | TAX-049, TAX-050, TAX-051, TAX-052, TAX-053 |
| [TAX-057](TAX-057.json) | Keletso | pilot | Tenant isolation, replay and failure matrix | TAX-054, TAX-046 |
| [TAX-058](TAX-058.json) | Keletso | pilot | Five-scenario demo and measured acceptance | TAX-055, TAX-056, TAX-057, TAX-089, TAX-091, TAX-092, TAX-093, TAX-094, TAX-095, TAX-097, TAX-103, TAX-101, TAX-102, TAX-118, TAX-139 |
| [TAX-059](TAX-059.json) | Keletso | pilot | Audit ledger and segregation of duties | TAX-046 |
| [TAX-060](TAX-060.json) | Keletso | pilot | Management workload and assurance dashboard | TAX-040, TAX-059 |
| [TAX-061](TAX-061.json) | Keletso | pilot | Approved playbook and reusable tool registry | TAX-047, TAX-052 |
| [TAX-062](TAX-062.json) | Keletso | pilot | Pilot data access, retention and export controls | TAX-032, TAX-059 |
| [TAX-063](TAX-063.json) | Keletso | pilot | Deployment configuration, backup and restore rehearsal | TAX-054, TAX-062 |
| [TAX-064](TAX-064.json) | Keletso | expansion | Auditor action observation and workflow proposals | TAX-061, TAX-059 |
| [TAX-065](TAX-065.json) | Keletso | expansion | Workflow approval, versioning and rollback | TAX-064 |
| [TAX-066](TAX-066.json) | Keletso | expansion | Risk calibration and drift evaluation | TAX-056, TAX-059 |
| [TAX-067](TAX-067.json) | Keletso | expansion | Taxpayer baselines and peer-group intelligence | TAX-066, TAX-024 |
| [TAX-068](TAX-068.json) | Keletso | expansion | Institutional knowledge retrieval | TAX-053, TAX-062 |
| [TAX-069](TAX-069.json) | Keletso | expansion | Objection, appeal and final-outcome records | TAX-046, TAX-059 |
| [TAX-070](TAX-070.json) | Keletso | expansion | Outcome and appeal feedback loop | TAX-068, TAX-069, TAX-066 |
| [TAX-071](TAX-071.json) | Keletso | expansion | EOI evidence-gap and request drafting agent | TAX-053, TAX-052 |
| [TAX-072](TAX-072.json) | Keletso | expansion | EOI approval and response evidence lifecycle | TAX-071, TAX-062 |
| [TAX-073](TAX-073.json) | Keletso | expansion | Tax/customs multi-value reconciliation | TAX-048, TAX-018 |
| [TAX-074](TAX-074.json) | Keletso | expansion | Scheme, beneficial ownership and intermediary graph | TAX-036, TAX-037 |
| [TAX-075](TAX-075.json) | Keletso | expansion | Emerging network pattern review | TAX-074, TAX-067 |
| [TAX-076](TAX-076.json) | Keletso | expansion | Controlled serious-case referral package | TAX-062, TAX-052 |
| [TAX-077](TAX-077.json) | Keletso | expansion | Policy-gap and integrity management views | TAX-052, TAX-059, TAX-069 |
| [TAX-078](TAX-078.json) | Keletso | expansion | Recurring submission-error guidance | TAX-044, TAX-069 |
| [TAX-079](TAX-079.json) | Keletso | future | Lawful payment-channel extension contracts | TAX-073, TAX-074 |
| [TAX-080](TAX-080.json) | Keletso | future | Additional tax-domain playbook extension design | TAX-061, TAX-070 |
| [TAX-081](TAX-081.json) | Keletso | expansion | Pilot operational acceptance and performance baseline | TAX-058, TAX-060, TAX-063, TAX-118 |
| [TAX-082](TAX-082.json) | Keletso | expansion | User onboarding and officer usability validation | TAX-055, TAX-060 |

| [TAX-083](TAX-083.json) | Keletso | foundation | Prototype engineering standards and checks | none |
| [TAX-085](TAX-085.json) | Keletso | pilot | Service adapters packaged and configured inside Frappe | TAX-034, TAX-038 |
| [TAX-086](TAX-086.json) | Keletso | pilot | Case findings record for rule and agent output | TAX-030, TAX-035, TAX-046, TAX-096 |
| [TAX-087](TAX-087.json) | Keletso | pilot | Event wiring from Frappe to Jube, Docling, OpenAleph and yente | TAX-085, TAX-039, TAX-036, TAX-037, TAX-038 |
| [TAX-088](TAX-088.json) | Keletso | pilot | AI investigation runtime on real cases | TAX-087, TAX-086, TAX-047, TAX-053, TAX-089, TAX-097, TAX-110 |
| [TAX-089](TAX-089.json) | Keletso | pilot | Model router with Groq/Gemini providers, local and template fallback | TAX-022, TAX-053, TAX-056 |
| [TAX-090](TAX-090.json) | Keletso | pilot | Vati /api/v1 service in Frappe for the auditor app | TAX-086, TAX-039, TAX-096 |
| [TAX-091](TAX-091.json) | Keletso | pilot | Auditor app hosted in Frappe with real officer login | TAX-090 |
| [TAX-092](TAX-092.json) | Keletso | pilot | Taxpayer portal live submissions list and status | TAX-039 |
| [TAX-093](TAX-093.json) | Keletso | pilot | Auditor screens for AI briefing and specialist opinion | TAX-090, TAX-088, TAX-096 |
| [TAX-094](TAX-094.json) | Keletso | pilot | Demo seed and reset for the five scenarios | TAX-085, TAX-086 |
| [TAX-095](TAX-095.json) | Keletso | pilot | Presentation environment on the demo laptop | TAX-085 |
| [TAX-096](TAX-096.json) | Keletso | pilot | AI briefing, provenance, feedback and taxpayer-assistance contract | TAX-018 |
| [TAX-097](TAX-097.json) | Keletso | pilot | Model-assisted reasoning in the four investigation agents | TAX-089, TAX-056 |
| [TAX-098](TAX-098.json) | Keletso | pilot | Taxpayer compliance assistant agent | TAX-089, TAX-096, TAX-044, TAX-038 |
| [TAX-099](TAX-099.json) | Keletso | pilot | Taxpayer evidence-response assistant | TAX-098, TAX-045 |
| [TAX-100](TAX-100.json) | Keletso | pilot | Taxpayer-scoped assistant service in Frappe | TAX-096, TAX-029, TAX-031, TAX-032 |
| [TAX-101](TAX-101.json) | Keletso | pilot | Taxpayer portal assistant UI | TAX-100, TAX-098, TAX-092 |
| [TAX-102](TAX-102.json) | Keletso | pilot | Taxpayer assistant scenarios, expected outcomes and leakage tests | TAX-024 |
| [TAX-103](TAX-103.json) | Keletso | pilot | Live-app end-to-end regression re-run | TAX-055, TAX-091, TAX-092, TAX-094, TAX-106, TAX-107, TAX-108, TAX-109, TAX-143 |
| [TAX-104](TAX-104.json) | Keletso | pilot | Fix auditor defects found by TAX-055 | TAX-055 |
| [TAX-105](TAX-105.json) | Keletso | pilot | Verify complete frontend and native UI integration scope | none |
| [TAX-106](TAX-106.json) | Keletso | pilot | Case-aware navigation to native investigation and administration screens | TAX-030, TAX-090, TAX-091, TAX-107, TAX-108 |
| [TAX-107](TAX-107.json) | Keletso | pilot | Configure and verify Jube native tax investigation screens | TAX-085, TAX-034, TAX-035, TAX-046 |
| [TAX-108](TAX-108.json) | Keletso | pilot | Configure and verify OpenAleph tax evidence and graph screens | TAX-085, TAX-032, TAX-036, TAX-037 |
| [TAX-109](TAX-109.json) | Keletso | pilot | Protected original-document access from case evidence and citations | TAX-032, TAX-090, TAX-108 |
| [TAX-110](TAX-110.json) | Keletso | pilot | Reliable hosted briefing and evidence reading within free-tier limits | TAX-097 |
| [TAX-111](TAX-111.json) | Keletso | pilot | RSL source-system contracts and fixture adapters | TAX-018, TAX-030, TAX-073 |
| [TAX-112](TAX-112.json) | Keletso | pilot | Lekuka/IDMS input-VAT match in hybrid mode and sixth scenario | TAX-111, TAX-048, TAX-024 |
| [TAX-113](TAX-113.json) | Keletso | pilot | SAF-T-style source-record intake and recompute engine | TAX-111, TAX-032, TAX-024 |
| [TAX-114](TAX-114.json) | Keletso | pilot | Refund lane service targets, undisputed-portion release and RSL inputs | TAX-048, TAX-111, TAX-112, TAX-113 |
| [TAX-115](TAX-115.json) | Keletso | pilot | Imported-services three-way match with CMA caveat | TAX-050, TAX-079, TAX-113 |
| [TAX-116](TAX-116.json) | Keletso | pilot | Named rule register (unverified) and AfriSam policy-gap entry | TAX-052, TAX-077 |
| [TAX-117](TAX-117.json) | Keletso | pilot | Vati API behind WSO2 API Manager and sign-in through WSO2 Identity Server | TAX-090, TAX-091 |
| [TAX-118](TAX-118.json) | Keletso | pilot | Synthetic taxpayer population, manual baseline and measured results | TAX-112, TAX-113, TAX-114, TAX-115, TAX-116, TAX-056 |
| [TAX-119](TAX-119.json) | Keletso | pilot | Docling extraction accuracy on generated invoice and agreement PDFs | TAX-118, TAX-038 |
| [TAX-120](TAX-120.json) | Keletso | pilot | Human manual baseline and outside-reviewer scores for TAX-118 | TAX-118 |
| [TAX-121](TAX-121.json) | Keletso | pilot | Run the assurance checks inside the live pipeline and re-measure through the platform | TAX-086, TAX-087, TAX-111, TAX-112, TAX-113, TAX-114, TAX-115, TAX-116, TAX-118 |
