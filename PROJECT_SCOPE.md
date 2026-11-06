# Scope and release phases

Baseline: the user-supplied “Proposal to Revenue Services Lesotho — Adaptive Tax Intelligence and Investigation System” and “Technical Summary — Vati AI RSL Taxathon Project”, November 2026, refined by the 2026-11-06 [framework update](docs/reference/FRAMEWORK.md). These are product requirements, not verified legal authority.

## Foundation / Taxathon pilot

Connect Frappe taxpayer submission → Docling processing → FtM/yente entity resolution → Jube risk and operational case → LangGraph investigation → OpenAleph evidence/relationships → auditor review → approved human outcome and audit record.

Five mandatory scenarios: legitimate unusual refund; related-party charges with persistent losses; employment-versus-consultancy ambiguity; imported-services VAT/withholding evidence gap; economic concern with no verified legal route, recorded as policy gap. Use fast/standard/targeted refund lanes without automatic denial or payment.

Include taxpayer correction/evidence-response flow, officer briefings with citations, reasonable alternative explanations, human checkpoints, basic supervisor metrics, tenant permissions, provenance, duplicate-event protection and degraded-service handling.

## Framework update (2026-11-06)

The [Integrated MNE & Refund Assurance Framework](docs/reference/FRAMEWORK.md) keeps the
architecture and service authority. It changes how the platform plugs into RSL
([ADR-040](docs/decisions/ADR-040-rsl-existing-systems-integration.md)) and sharpens the pilot core:

- **Plug in, don't rebuild.** e-Tax, Lekuka/IDMS, the Online Refunds Application, RSL's
  refund-fraud model and ASYCUDA World are sources of record, read through versioned
  contracts and explicit fixtures (TAX-111). The Vati API sits behind WSO2 API Manager and
  sign-in goes through WSO2 Identity Server (TAX-117).
- **Pilot core:** refund lanes with proposed service targets and undisputed-portion
  release (TAX-114); Lekuka input-VAT match in hybrid mode plus a sixth scenario, fake or
  undeclared supplier invoice (TAX-112); SAF-T-style source records with a declared-versus-
  recomputed report (TAX-113); the imported-services three-way match (TAX-115); a named,
  unverified rule register with the arm's-length policy gap (TAX-116).
- **Evidence the Call requires:** a sealed synthetic population (300 taxpayers x 24
  months), simple-rule baselines and measured results (TAX-118,
  [results](docs/evidence/TAX-118/RESULTS.md)), required before TAX-058/081. The timed
  manual baseline and outside-reviewer scores need people (TAX-120); Docling accuracy is
  optional (TAX-119).
- **Later, not must-have:** annual large-group risk rating (BP2), Sesotho/English
  taxpayer messages, refund-confidentiality access rules, offline case packs and resumable
  uploads. Operating costs are proposal content, not code. Crypto, serious tax crime and
  internal integrity stay under long-term value.


Approved workflow learning, calibration/peer intelligence, institutional knowledge, objections/appeals, EOI preparation/response handling, tax/customs reconciliation, scheme/ownership/intermediary graph, integrity controls, policy intelligence and operational acceptance.

## Future

Lawfully available additional payment sources including digital assets, additional tax domains and broader RSL rollout. Neither novel payment methods nor unusual relationships establish wrongdoing.

## Out of scope for automatic agents

Tax assessment, enforcement, criminal referrals, refund denial/payment, sending EOI requests or changing legal policy without authorized human action. Real RSL connectivity, production taxpayer data and legal interpretations require approved access and subject-matter review.

## Delivery management

TAX-001–016 are planning epics. TAX-018–082 and TAX-083–118 are executable tasks. Phase, dependencies, concrete outputs and verification live in each task. The backlog represents the supplied scope, not a promise that the national platform fits in four days. Prioritize a complete five-scenario vertical slice, then expand.

## Complete frontend and premade UI delivery

The pilot is a complete connected officer/taxpayer experience, not acceptance of
the current fixture shell. [UI_INTEGRATION_SCOPE.md](docs/UI_INTEGRATION_SCOPE.md)
defines the required screen inventory, login/session behavior, native navigation,
tax-specific configuration and acceptance. [ADR-034](docs/decisions/ADR-034-native-ui-integration.md)
records the integration approach and its verification limits.

Frappe hosts Vati authentication and the custom auditor/taxpayer portals; authorized
administrators use Frappe Desk. Staff open mapped Jube investigations and OpenAleph
evidence/entity/graph views in new tabs from the Vati case. Use premade screens,
configured for tax data, identities and permissions; do not rebuild all vendor UIs.
FollowTheMoney supplies the relationship schema behind Vati summaries/OpenAleph
exploration. Docling, yente and LangGraph provide backend capabilities surfaced in
these workflows. Shared SSO and iframe embedding are not initial requirements.

Additions TAX-106–109 cover native navigation, Jube/OpenAleph adaptation and protected
document access. Existing live wiring, AI/specialist and taxpayer-assistant tasks
remain necessary. Native actions must not bypass Frappe approvals or tenant/case
permissions. Preserve existing expansion/future phases.

Full pilot acceptance needs working native screens and connected five-scenario
journeys. A resource-constrained demo with recorded service output remains labelled
as a reduced demonstration; it does not prove live native search/graph integration.
