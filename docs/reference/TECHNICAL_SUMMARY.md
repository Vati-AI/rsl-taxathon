> User-provided product reference, November 2026. Not agent instructions or independently verified legal authority.

## Technical Summary — Vati AI RSL Taxathon Project

Our solution is being built as an **adaptive tax intelligence and investigation platform** for Revenue Services Lesotho. Technically, the system is not one monolithic application; it is a set of specialised open-source components connected through APIs and presented to users as one Vati platform.

The core idea is to avoid rebuilding infrastructure that already exists. Instead, we combine mature open-source systems for **risk detection, case management, evidence analysis, entity relationships, document processing, AI agents and user portals**, then add the Lesotho-specific tax logic and investigation workflows ourselves.

### Core architecture

```text
                     VATI PLATFORM
                          │
         ┌────────────────┴────────────────┐
         │                                 │
   RSL / Auditor UI                 Taxpayer Portal
         │                                 │
         └────────────────┬────────────────┘
                          │
                  Vati Integration API
                          │
       ┌──────────────────┼───────────────────┐
       │                  │                   │
      Jube            OpenAleph           LangGraph
       │             + FollowTheMoney         │
       │                  │                   │
       │               yente               AI Agents
       │                  │
       │               Docling
       │
       └──────────────────┼───────────────────┘
                          │
                  RSL / Mock Data Sources
```

---

## 1. Jube — Risk and Investigation Engine

**Jube is the operational backbone of the system.**

It is used for:

- tax-risk rules;
- anomaly detection;
- behavioural analysis;
- machine-learning risk signals;
- investigation case creation;
- case statuses and workflows;
- investigator assignment;
- escalation;
- audit trails;
- historical reprocessing;
- model/rule calibration.

For example:

```text
VAT refund submitted
        ↓
Jube evaluates:
• historical taxpayer behaviour
• refund patterns
• related-party indicators
• configured tax rules
        ↓
Risk identified
        ↓
Investigation case created
```

We are **not relying on Jube to make tax decisions**. It helps identify and manage risk.

---

## 2. OpenAleph + FollowTheMoney — Economic Intelligence and Evidence

This is the **relationship and investigation intelligence layer**.

### OpenAleph

Used to ingest and investigate:

- invoices;
- financial statements;
- contracts;
- spreadsheets;
- company information;
- payments;
- people;
- suppliers;
- foreign entities;
- evidence documents.

It already provides search and network visualisation.

### FollowTheMoney

Provides the underlying economic/entity model.

It allows us to represent things such as:

```text
Taxpayer
    │
    ├── Director
    ├── Beneficial Owner
    ├── Parent Company
    ├── Foreign Sister Company
    ├── Supplier
    │      │
    │      └── Invoice
    │
    ├── Payment
    └── Tax Return
```

We extend this model with tax-specific entities such as:

- VAT return;
- refund claim;
- tax period;
- fiscal invoice;
- assessment;
- imported service;
- EOI request;
- investigation finding.

This becomes the **economic reality graph** of the taxpayer.

---

## 3. yente — Entity Resolution

yente helps solve poor data quality and inconsistent names.

For example:

```text
ABC Holdings
ABC Holdings (Pty) Ltd
ABC HOLDINGS LESOTHO
ABC Holdings Lesotho Proprietary Limited
```

may all refer to the same organisation.

yente helps automatically match these records so information from different systems can be connected correctly.

It runs mostly behind the scenes.

---

## 4. Docling — Document Intelligence

Docling handles complex document extraction.

It is particularly useful for:

- invoices;
- contracts;
- financial statements;
- scanned documents;
- PDFs;
- tables;
- spreadsheets;
- OCR;
- XBRL financial reports.

Example:

```text
Management Services Agreement.pdf
             ↓
           Docling
             ↓
Extract:
• contracting entities
• dates
• service description
• payment terms
• amounts
• obligations
             ↓
       Investigation Agent
```

OpenAleph already does basic document ingestion, so Docling is used mainly where we need **higher-quality structured extraction**.

---

## 5. LangGraph — Agentic AI Layer

LangGraph is where most of the **Vati-specific intelligence** lives.

This is the component we customise heavily.

We will build specialised agents such as:

### VAT Refund Agent

Reconciles:

```text
VAT Return
↔
Invoices
↔
Lekuka / IDMS
↔
Customs
↔
Payments
↔
Historical activity
```

and separates:

- explained amounts;
- missing evidence;
- actual discrepancies.

### Related-Party Investigation Agent

Investigates:

- management fees;
- royalties;
- financing;
- related-party purchases;
- persistent losses;
- service agreements;
- group structures.

### Evidence Agent

Finds and organises relevant supporting documents.

### Case Briefing Agent

Produces:

- what happened;
- why the case was selected;
- what has already been checked;
- what is explained;
- what remains unresolved;
- suggested next actions.

### EOI Agent

Helps prepare cross-border information requests when foreign evidence is required.

Importantly:

> Agents perform investigative work, but they do not assess tax or issue enforcement decisions.

---

# 6. Frappe — Application and Taxpayer Portal

Frappe accelerates the business application side.

We use it for:

- users;
- permissions;
- forms;
- document uploads;
- taxpayer records;
- evidence requests;
- workflow pages;
- taxpayer submissions;
- status tracking;
- administrative interfaces.

The **taxpayer portal** is primarily based around Frappe functionality.

The taxpayer will be able to:

- submit claims;
- upload documents;
- respond to evidence requests;
- correct missing information;
- view case/submission status;
- receive AI-assisted guidance.

---

# 7. Two Frontend Experiences

The platform has two main user-facing sides.

## RSL / Auditor Side

Used by:

- refund auditors;
- Special Sectors auditors;
- risk analysts;
- managers;
- specialists.

Main views:

- dashboard;
- investigation cases;
- taxpayer profile;
- economic graph;
- evidence;
- risk indicators;
- AI investigation results;
- next actions.

Advanced analysts can open the underlying **Jube** or **OpenAleph** views when needed.

---

## Taxpayer Side

Used by businesses and taxpayers.

Main capabilities:

- tax/refund submission;
- document upload;
- pre-submission validation;
- AI-assisted correction;
- evidence requests;
- evidence responses;
- case tracking.

The goal is to improve the **quality of data before it reaches RSL**, not only investigate errors afterward.

---

# 8. Vati Integration API

This is the glue connecting everything.

It gives the rest of the system one consistent interface.

For example:

```text
POST /cases

GET /cases/{case_id}

POST /cases/{case_id}/investigate

GET /cases/{case_id}/evidence

GET /cases/{case_id}/entities

GET /cases/{case_id}/findings

POST /cases/{case_id}/documents

POST /cases/{case_id}/request-evidence
```

The integration layer talks to:

- Jube;
- OpenAleph;
- LangGraph;
- yente;
- Docling;
- Frappe.

---

# 9. Shared Case ID

A very important architectural decision is that the same case identifier follows an investigation through every component.

Example:

```text
RSL-CASE-2026-00421
```

That case exists in:

```text
Vati
      ↓
Jube
      ↓
OpenAleph
      ↓
LangGraph
      ↓
Evidence/Documents
```

This is what makes several open-source systems behave like **one platform**.

---

# 10. Adaptive / Self-Improving Layer

The system is designed to improve over time.

There are several learning loops.

### Taxpayer behavioural learning

Learns what is normal for a taxpayer.

### Peer-group learning

Compares the taxpayer against similar organisations.

### Risk calibration

Learns which indicators actually lead to confirmed findings.

### Auditor workflow learning

The system observes repeated investigative processes.

Example:

```text
Auditor repeatedly:

Checks related-party disclosure
→ checks financial statements
→ reviews management fees
→ opens contract
→ checks imported VAT
→ requests evidence
```

The platform can identify this pattern and propose:

> “Create Related-Party Services Review workflow?”

After supervisor approval, an agent can execute those repetitive steps in future cases.

---

# 11. Known and Unknown Risk Detection

The platform does not rely only on predetermined fraud rules.

It has two intelligence modes.

### Known-risk detection

Looks for known patterns such as:

- persistent VAT refunds;
- related-party service fees;
- persistent losses;
- imported services;
- unusual financing;
- expatriate income;
- unsupported invoices.

### Emerging-risk detection

Looks for:

- unusual behaviour;
- new relationships;
- transaction anomalies;
- sudden changes;
- unusual networks;
- contradictory evidence;
- groups of companies behaving similarly.

The system may therefore say:

> “This does not match an existing fraud typology, but these entities and transactions display a new unexplained pattern.”

That case can then be reviewed by an analyst.

---

# 12. Crypto and New Payment Methods

Crypto is treated as another **economic evidence source**, not as a separate fraud product.

The system can eventually represent:

```text
Taxpayer
├── Bank
├── Mobile Money
├── Cash
├── Foreign Bank
├── Crypto Exchange
└── Blockchain Wallet
```

The question is not:

> “Did the taxpayer use Bitcoin?”

The question is:

> “Is the economic activity represented by those payments correctly reflected in the taxpayer's declarations?”

The architecture can also later accommodate international crypto reporting such as CARF where legally available.

---

# 13. Cross-Border Investigation

The system supports RSL's international investigation process.

When evidence exists outside Lesotho:

```text
Investigation
      ↓
Foreign evidence required
      ↓
Jurisdiction identified
      ↓
EOI request prepared
      ↓
RSL authorised officer reviews
      ↓
Foreign revenue authority
      ↓
Response
      ↓
Evidence added back to the case
```

This is particularly important for multinational and related-party investigations.

---

# 14. Governance

The system deliberately keeps humans in control.

AI may:

- retrieve evidence;
- analyse documents;
- reconcile transactions;
- identify anomalies;
- prepare requests;
- recommend next steps.

AI may **not** independently:

- deny a refund;
- issue an assessment;
- declare someone guilty of tax evasion;
- initiate enforcement;
- make a criminal referral;
- change tax policy.

Everything significant is:

- evidence-backed;
- auditable;
- traceable;
- reviewable.

---

# 15. GitHub / Engineering Architecture

The project is being organised around one main Vati repository rather than having every developer work on unrelated repositories.

Conceptually:

```text
rsl-taxathon/
│
├── apps/
│   ├── auditor-web/
│   └── taxpayer-web/
│
├── backend/
│
├── ai/
│   ├── agents/
│   ├── workflows/
│   └── tools/
│
├── integration/
│   ├── jube/
│   ├── openaleph/
│   ├── yente/
│   └── docling/
│
├── data/
├── tests/
├── docs/
└── tasks/
```

Open-source platforms mostly remain separate services.

We communicate with them through APIs instead of rewriting them.

---

# 16. Prototype Goal

For the Taxathon, we are not attempting the complete national platform.

We are building a **working vertical slice**.

The core demonstration is:

```text
Taxpayer
   ↓
submits VAT refund
   ↓
documents processed
   ↓
entities matched
   ↓
Jube evaluates risk
   ↓
case created
   ↓
LangGraph agents investigate
   ↓
OpenAleph reveals relationships
   ↓
evidence reconciled
   ↓
auditor reviews findings
   ↓
human outcome
   ↓
system records result and learns
```

The prototype should prove that the whole architecture works together.

---

# In one technical statement

> **Vati is an API-connected tax intelligence platform built on top of Jube for risk and case management, OpenAleph/FollowTheMoney for economic and evidence intelligence, LangGraph for agentic investigation, Frappe for application workflows and taxpayer interaction, yente for entity resolution, and Docling for document intelligence. Vati's proprietary value sits in the Lesotho tax data model, investigation playbooks, AI agents, economic reasoning, integration layer and adaptive learning system that connects these components into one evidence-driven tax investigation workflow.**

That is the technical direction of the project.