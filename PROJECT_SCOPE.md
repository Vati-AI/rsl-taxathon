# Scope and release phases

Baseline: the user-supplied “Proposal to Revenue Services Lesotho — Adaptive Tax Intelligence and Investigation System” and “Technical Summary — Vati AI RSL Taxathon Project”, November 2026. These are product requirements, not verified legal authority.

## Foundation / Taxathon pilot

Connect Frappe taxpayer submission → Docling processing → FtM/yente entity resolution → Jube risk and operational case → LangGraph investigation → OpenAleph evidence/relationships → auditor review → approved human outcome and audit record.

Five mandatory scenarios: legitimate unusual refund; related-party charges with persistent losses; employment-versus-consultancy ambiguity; imported-services VAT/withholding evidence gap; economic concern with no verified legal route, recorded as policy gap. Use fast/standard/targeted refund lanes without automatic denial or payment.

Include taxpayer correction/evidence-response flow, officer briefings with citations, reasonable alternative explanations, human checkpoints, basic supervisor metrics, tenant permissions, provenance, duplicate-event protection and degraded-service handling.

## Expansion

Approved workflow learning, calibration/peer intelligence, institutional knowledge, objections/appeals, EOI preparation/response handling, tax/customs reconciliation, scheme/ownership/intermediary graph, integrity controls, policy intelligence and operational acceptance.

## Future

Lawfully available additional payment sources including digital assets, additional tax domains and broader RSL rollout. Neither novel payment methods nor unusual relationships establish wrongdoing.

## Out of scope for automatic agents

Tax assessment, enforcement, criminal referrals, refund denial/payment, sending EOI requests or changing legal policy without authorized human action. Real RSL connectivity, production taxpayer data and legal interpretations require approved access and subject-matter review.

## Delivery management

TAX-001–016 are planning epics. TAX-018–082 are executable tasks. Phase, dependencies, concrete outputs and verification live in each task. The backlog represents the supplied scope, not a promise that the national platform fits in four days. Prioritize a complete five-scenario vertical slice, then expand.

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
