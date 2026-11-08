# ADR-043: Vati works downstream of RSL's selection; the taxpayer portal answers evidence requests

Status: proposed
Owner and task: Keletso; amends ADR-040 §4 and revisits ADR-034's separate native logins; implemented by TAX-122 to TAX-143
Date: 2026-11-07

Context and smallest prototype need:
- ADR-040 made RSL's systems the sources of record. It also said "the Frappe portal is the
  evidence-submission layer reached from e-Tax". The portal still opens on a VAT refund claim
  form (TAX-043), an invoice schedule the taxpayer types in (TAX-044) and a submissions list
  (TAX-092). Evidence requests (TAX-045) are third in the menu.
- In the product code, the only way to start a case is a taxpayer sending that form
  (`vati_tax/bridge/rules.py` `should_register`). The Online Refunds Application contract
  (TAX-111) feeds only the evaluation scripts. Nothing goes back to RSL.
- Public research (summarised in [RSL_LANDSCAPE.md](../reference/RSL_LANDSCAPE.md), unverified)
  shows more RSL capability than ADR-040 assumed:
  - The Online Refunds Application already shows claim statuses and rejection reasons.
  - A risk-based audit system (Paradox Box, on PSRM) already runs 120+ risk criteria and audit
    workflows between team leaders, auditors and taxpayers.
  - Lekuka's data warehouse pre-fills returns and feeds RSL's risk tools.
  - WSO2 Identity Server is RSL's identity provider.
- Jube, OpenAleph and FtM are configured for tax work (TAX-107/108/036) but know nothing about
  RSL: no RSL claim ID, TIN, RSL status, RSL score owner or link back to RSL.
- Challenge 2 names persistent VAT credit positions and in-house loss creation. The pilot covers
  persistent losses and single-claim structural credits only.
- We have no RSL access or specifications. The prototype therefore needs contracts, explicit
  fixtures and correct positioning, not live connections.

Decision:
1. **RSL selects; Vati investigates; RSL acts.** Cases start from an RSL source: a claim read
   from the Online Refunds Application (TAX-122) or an audit referral from RSL's risk-based audit
   system on PSRM (TAX-123). Findings, the lane recommendation, the undisputed amount and the
   "evidence requested" status go back to RSL through outbound contracts (TAX-124). RSL's
   systems keep refund status, payment, offset and assessment.
2. **The taxpayer portal answers RSL's evidence requests.** Its home page is evidence requests.
   "My cases" shows RSL's status read-only and labelled as RSL's. Taxpayers file refund claims in
   the Online Refunds Application, not in Vati. The TAX-043 form and the TAX-044 invoice entry
   step leave the product; `submit_refund_claim` remains only as a labelled
   **Online Refunds simulator** for fixtures and the demo (TAX-125).
3. **Preventative checks stay, as a service.** The TAX-044/048 reconciliation becomes a
   "check before you file" service (TAX-129). It reads Lekuka and e-Tax data, shows issues and
   sends the taxpayer to RSL to file. RSL's front door may call it through WSO2. The same checks
   run on evidence responses before they are sent (TAX-126).
4. **Ask only for the gap.** Evidence requests are itemised from Lekuka/ASYCUDA mismatches and
   playbook gaps (TAX-127, TAX-128). Vati never asks for data RSL already holds.
5. **Jube's case is an investigation sub-case of an RSL case.** It carries the RSL claim or audit
   case reference, takes referred cases only and does not select taxpayers from the population.
   ADR-001 authority between Jube and Frappe is unchanged inside Vati (TAX-131, TAX-134).
6. **RSL's scores are inputs; Vati produces findings.** The RSL fraud model and RSL risk sets are
   displayed as RSL's. Vati rules, calibration and peer baselines are corroboration and
   feedback for RSL's Risk Unit, never a competing score (TAX-131).
7. **One system on RSL.** RSL identifiers join the canonical reference register (TAX-133).
   Jube and OpenAleph/FtM show RSL references and source labels (TAX-134, TAX-135). Navigation
   works in both directions (TAX-137). Every value states its source system (TAX-138). This is
   configuration and adapters first; ADR-034's no-fork rule stands. RSL branding only with
   RSL's approval.
8. **One sign-in.** Because RSL already runs WSO2 Identity Server, ADR-034's "separate native
   logins" is revisited. TAX-136 tests OIDC sign-in for Jube and OpenAleph against the pinned
   versions. Where a version cannot, the separate login is recorded as a limitation, not
   claimed as solved.
9. **References, not copies.** Documents RSL already holds (Online Refunds uploads, Lekuka
   invoices) are stored as references with hashes. Only evidence the taxpayer sends in answer
   to a request becomes a `Vati Evidence` file.
10. **Close the challenge gaps.** Add persistent VAT credit positions as a seventh scenario
    (TAX-139), in-house loss creation through intra-group financing (TAX-140), imported
    services booked as expenses (TAX-141) and refund-confidentiality access rules in the pilot
    (TAX-142).

What is retired, reframed and kept:

| Item | Decision |
|---|---|
| TAX-043 claim form, "New submission" route | Retired from the product; kept as the fixture-only Online Refunds simulator |
| TAX-044 invoice entry step | Retired; lines come from the claim contract and Lekuka |
| TAX-044 checks | Kept as the TAX-129 pre-filing service and the TAX-126 response checks |
| TAX-092 submissions list | Reframed as read-only "My cases" showing RSL's status |
| TAX-078 reminders | Moved to evidence responses and the pre-filing check |
| Frappe taxpayer login in production | Replaced by WSO2 IS (TAX-117); Frappe login stays for local use |
| Jube, Jube rules, TAX-066/067 | Reframed: referred cases only; corroboration, not scoring |
| TAX-048/114 lanes and undisputed release | Reframed: a recommendation for claims in RSL audit; RSL sets status and pays |
| TAX-113 SAF-T intake | Reframed: requested evidence for what Lekuka cannot cover |
| TAX-060 metrics, TAX-069/070 objections | Reframed: Vati cases only; references to PSRM records |
| TAX-045 evidence requests | Kept; the portal's core |

Alternatives and tradeoff:
- Keep the portal as a claim front door. This is simpler for the demo, but it duplicates the
  Online Refunds Application and contradicts ADR-040.
- Drop Jube and use RSL's audit system as the only case system. This means less to operate,
  but there is no access to RSL's audit system and it would discard working adapters. We
  revisit this at deployment.
- Leave the open-source tools RSL-agnostic. This is cheaper, but officers would see disconnected
  tools with no RSL context, which weakens pilot potential.

Official sources and version/date checked:
None from RSL. The landscape comes from public news, vendor case studies and RSL publications
found by web search on 2026-11-07. The pages could not be fetched directly from this
environment. Every statement is unverified until RSL confirms it. See
[RSL_LANDSCAPE.md](../reference/RSL_LANDSCAPE.md).

Verification performed (or named implementation task):
This is a documentation and planning change. No behaviour changed and no integration was tested.
The tasks named above carry the implementation and verification.

Consequences and revisit trigger:
- The demo narrative changes. It becomes: an RSL claim or referral arrives, Vati investigates,
  an evidence request appears in RSL with a link, the taxpayer answers in the portal, and
  results go back to RSL (TAX-143).
- TAX-122 must land before TAX-125 retires the form, because the demo and seven frontend specs
  depend on it.
- Revisit when RSL confirms its systems, especially whether the risk-based audit system already
  has a taxpayer document channel. If it does, the portal becomes the structured,
  evidence-checked response layer behind it.

Open questions for RSL:
1. Does the risk-based audit system or PSRM already let taxpayers submit requested documents?
2. Does the Online Refunds Application expose an API or event for new and pending claims, and
   can a "Pending: evidence requested" reason be written back?
3. What format does the audit system use for referrals and case exports, and can a Vati findings
   pack be attached to an audit case?
4. How are Online Refunds statuses ("Processing", "Pending", "Audit in progress") defined, and
   who may change them?
5. Can Jube and OpenAleph be registered as WSO2 Identity Server applications for RSL staff?
