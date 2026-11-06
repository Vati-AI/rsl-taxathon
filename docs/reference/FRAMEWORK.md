# Vati AI: Integrated MNE & Refund Assurance Framework for RSL (first draft, 2026-11-06)

User-supplied scope update. It refines the November 2026 proposal in
[PROPOSAL.md](PROPOSAL.md); where they differ on processes, this framework is the
newer statement of intent. It is a product requirement, **not verified legal or
factual authority**: every legal reference is *(verify)* and must be checked by RSL
Legal or a Lesotho tax lawyer. Statements about RSL systems (Online Refunds
Application, refund-fraud model, WSO2, IDMS, ASYCUDA World) come from public
research and must be confirmed with RSL before any live integration. The
repository's response is recorded in [ADR-040](../decisions/ADR-040-rsl-existing-systems-integration.md)
and tasks TAX-111 to TAX-118.

**Purpose:** help RSL detect abusive multinational tax planning and refund risk faster
and with stronger evidence, while paying valid refunds on time. It runs on RSL's
existing systems, data and teams and replaces nothing.

**Engine and fuel:** the Vati AI platform is the engine; this framework (processes,
rules, roles, service standards, legal anchors) tells it what to do and who decides.

## Part 1: Judging criteria

| Criterion | Weight | Where the framework delivers it |
|---|---|---|
| Taxpayer and business value | 25% | Faster valid refunds (BP3); imported-services leakage closed (BP5); large-group credit returns explained (BP2) |
| Performance and evidence | 20% | Testing plan against a baseline, with error rates and limitations (Part 7) |
| Usability | 15% | Officer and taxpayer journeys for ordinary, exception and human-review cases; low-bandwidth, Sesotho/English (Parts 5, 6) |
| Trust and compliance | 15% | Legal-route gate, explainable findings, officer decisions, refund confidentiality, audit trail (BP9, Part 3) |
| Pilot potential | 15% | Runs on e-Tax, Lekuka, ASYCUDA, the Online Refunds Application and WSO2; six-month pilot (Parts 2, 9) |
| Innovation | 10% | Source-record recompute, legal-route engine adapted to AfriSam, policy-gap register, cross-border evidence in each case |

## Part 2: Existing RSL infrastructure the framework runs on

| Existing RSL asset | What it does (per research, verify) | How the framework uses it |
|---|---|---|
| e-Tax portal | VAT returns filed by the 20th of the following month | Source of declared CIT, VAT, PAYE and WHT positions; taxpayer login entry point |
| Lekuka / IDMS | Data warehouse storing all invoicing information; powers audit and risk tools | Primary evidence for domestic input VAT; read from the warehouse, never duplicated |
| Online Refunds Application | Launched October 2025 for refund automation and turnaround | Front door for refund claims; the framework adds lanes, reasons and evidence packs |
| RSL refund-fraud model | Data science solution to detect fraudulent VAT refunds | Its score is one input to lane assignment; Jube is not a competing scorer |
| ASYCUDA World | Customs control, duty calculation and collection | Import VAT and customs values for reconciliation |
| WSO2 platform | Integrator, API Manager and Identity Server | All Vati integration runs through it; staff and taxpayer logins use its Identity Server |
| SARS agreements | VAT Mutual Assistance Agreement; SARS–RSL VAT refund MoU | First channel for South African supplier and parent verification |
| EOI network | Bilateral, ATAF and SADC agreements | Formal foreign evidence requests (BP8) |
| Organisation | Priority Client Services; Special Sectors Audit | Owners of BP2 and the investigation lanes |
| Strategy | Lesokoana Strategic Plan 2023–2027 | Framework positioned as delivering its digitalisation pillar |

**Design rule:** if RSL already holds a piece of data, the framework reads it; it never
asks the taxpayer again and never stores a second copy.

Component repositioning: the Frappe portal is an evidence-submission layer reached
from e-Tax; Jube is the case and workflow engine that takes RSL's scores as input;
the Vati Integration API is a service behind WSO2.

## Part 3: Governance, roles and decision rights

Principles: evidence over suspicion (a refund position alone does not establish
abuse); source records over summaries; legal route before action (no provision →
policy-gap register); valid refunds fast by default; machines assist, officers
decide; raw data preserved and quality-scored; every case teaches.

| Decision | System may | Who decides |
|---|---|---|
| Assign refund lane | Recommend, with reasons | Refund auditor confirms; supervisor approves overrides |
| Release fast-lane refund | Prepare clearance pack | Refund auditor, within delegation limit |
| Release undisputed portion | Calculate amount | Refund auditor |
| Open investigation | Recommend | Team lead, Special Sectors |
| Send EOI request | Draft package | Competent authority |
| Raise assessment | Calculate and draft | Authorised auditor plus Legal for high value |
| Criminal referral | Never | Investigator plus Deputy Commissioner plus Legal |
| Change a rule or threshold | Propose from outcome data | Rule governance committee |

Roles: business owner (Deputy Commissioner, Special Sectors); group relationship
officer (Priority Client Services); refund auditor; Special Sectors auditor; risk
analyst; EOI officer / competent authority; RSL Legal; rule governance committee;
taxpayer.

## Part 4: Business processes

| BP | Process | Summary |
|---|---|---|
| BP1 | Source-record submission and data integrity | SAF-T-style GL, supplier/customer ledgers, payments, bank statements, intercompany agreements, foreign invoices. Ingest with hash; standardise (Maloti, ISO dates, account categories); clean (flag, never silently fix); resolve entities; **recompute** VAT and key income-tax lines; **declared vs recomputed** line by line; quality score (complete, verified, unverified, contradicted). Low quality alone never triggers action. RSL independently validates; the company remains responsible under self-assessment. Pilot: large MNE groups only. |
| BP2 | Annual risk profile for large groups | Priority Client Services; named officer; pre-review data pack; Low/Medium/High against published criteria; action plan; low-risk groups qualify more easily for the fast lane; Low every 2–3 years, Medium/High yearly. |
| BP3 | Refund triage, three lanes | Claim arrives via the Online Refunds Application. Assemble claim, Lekuka invoices, ASYCUDA imports, RSL fraud score, BP2 rating, BP1 quality. Test structural explanations first. Fast (≤10 working days), Standard (≤30, targeted request, undisputed amount released), Investigation (case plan in 5 days, target 90; undisputed amount released where lawful). All times proposed, verify against the VAT Act. |
| BP4 | Input VAT and Lekuka reconciliation | Match each input line by supplier TIN, invoice number, date and amount; check supplier declared output VAT; classify Matched / Supplier gap / Pre-Lekuka or unintegrated supplier / Unmatched. **Hybrid mode:** unmatched is a question, not a denial, while uptake is low (reported >90% not integrated in July). |
| BP5 | Imported services three-way match | A money out (bank statements; Central Bank where available), B books (GL), C declared VAT/WHT (e-Tax). Tolerances for FX and timing; test treaty relief, exempt services, goods vs services, timing. **CMA caveat:** CMA payments may not appear in Central Bank data; company bank statements are primary. |
| BP6 | Related-party and profit-shifting review | Section 113 does not support imputing an arm's-length standard it does not contain (AfriSam), but allocation of existing income/deductions between associates remains. Routes: deduction tests, s.113 allocation, WHT, thin capitalisation (reported 3:1, scope to verify), else policy gap. |
| BP7 | Employment vs consultancy | Control, duration, exclusivity, who pays, days present, integration; both readings presented for ambiguous cases. |
| BP8 | Cross-border evidence ladder | Domestic proxies → open sources → information already received → SARS channel → formal EOI → joint audit. |
| BP9 | Legal-route check, decision, objection | Finding record with hypothesis, evidence, tests, alternatives, confidence, provision. Versioned rule library; no valid route → cannot proceed. |
| BP10 | Learning and policy-gap register | Quarterly outcome review; first gap entry: no codified arm's-length standard. |

Rule library (all *verify*): related-party allocation (Income Tax Act 1993 s.113 as
limited by AfriSam); input VAT evidence (VAT Act 2001; VAT (E-Invoicing) Regulations
2026); imported services; WHT and treaty rates; thin capitalisation; record
production; refund handling; data use (Data Protection Act, RSL confidentiality).

## Part 5: User journeys

- **Ordinary:** FMCG distributor; Lekuka matches 97% of input VAT, ASYCUDA confirms
  import VAT, zero-rated sales explain the credit, group Low risk → fast lane; officer
  approves the clearance pack.
- **Exception:** BP5 finds M2.4m management fees to a South African parent with no
  VAT/WHT declared → Standard lane, undisputed amount released, targeted request;
  unresolved → BP6 with named legal route.
- **Human review:** two foreign staff >200 days, paid by recharge, contracts say
  consultants → both readings shown, marked ambiguous, specialist decides.

## Part 6: Designed for Lesotho

Low-bandwidth pages; downloadable case packs with offline work and later sync;
resumable uploads; Sesotho and English taxpayer messages; hybrid record mode with
quality scores; in-country hosting; role-based access with audit logs; refund
identities restricted to authorised roles; LLMs only read and draft, calculations are
deterministic, every output is tied to source evidence.

## Part 7: Evidence and testing plan

About 200 synthetic FMCG taxpayers with planted issues (valid structural refunds,
fake invoices, untaxed imported services, disguised employment, related-party loss
creation, policy-gap cases). Baseline: simulated manual process. Report detection
rate, false-positive rate, share of valid refunds in the fast lane, time to an
investigation-ready case vs baseline, reconciliation accuracy, share of findings with
a valid legal route. State limitations openly.

## Parts 8–11 (proposal content, not code)

Operating cost structure (hosting, licences, model use, support, RSL staff time,
training); six-month pilot (one Special Sectors team; FMCG filers with foreign
related parties; 4 months live, 2 evaluation); policy and agreement needs; success
measures. These belong in the written proposal; TAX-118 supplies the measured inputs.
