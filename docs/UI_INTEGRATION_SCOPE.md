# Complete frontend and native-tool scope

Agreed direction recorded by TAX-105, 2026-11-05. This is the required experience,
not a claim that it is implemented. See ADR-034 and the verification report.
The original technical summary section 7 already requires two frontend experiences
and access to Jube/OpenAleph; this document makes the missing integration explicit.

## Screens and ownership

| Audience / screen | Delivery surface | Delivery tasks |
|---|---|---|
| Login, expired session, denied access, logout and supported password recovery | Frappe authentication; return to an authorized destination | TAX-021, 029, 091 |
| Officer overview, searchable queue, case workspace and taxpayer profile | Vati auditor app hosted in Frappe | TAX-040, 090, 091 |
| Evidence list, original document access, extraction status, versions and clickable citations | Vati case context plus protected Frappe file/native OpenAleph viewer | TAX-032, 041, 109 |
| Parties, ownership, payments, match candidates and source conflicts | Vati case summaries; OpenAleph interactive exploration backed by FtM | TAX-036, 037, 041, 074, 106, 108 |
| AI briefing, findings, provenance, progress/checkpoints, suggested next actions and feedback | Vati case workspace; LangGraph/model services behind it | TAX-086, 088, 093, 096, 097 |
| Decisions, approval work, referrals, specialist opinion, evidence requests and review history | Vati case review; approved records in Frappe | TAX-042, 046, 093 |
| Management workload and assurance | Vati supervisor view | TAX-060, 090 |
| Native operational investigation and risk configuration | Jube, adapted to tax cases and role-restricted | TAX-034, 035, 107 |
| Evidence search, entity exploration and interactive network diagrams | Native OpenAleph, scoped collections and real mapped records | TAX-036, 108 |
| Users, roles, memberships, business records and permitted administration | Frappe Desk, not a duplicate custom admin app | TAX-029, 091, 106 |
| Evidence requests (home), evidence response with checks, read-only My cases showing RSL's status, check before you file, and account. Claims are filed in RSL's Online Refunds Application, not here (ADR-043) | Separate Vati taxpayer portal hosted in Frappe | TAX-021, 045, 125–127, 129, 130 (TAX-043/044/092 reframed) |
| Taxpayer compliance and evidence-response assistance | Taxpayer portal, never officer-only risk/findings | TAX-098–102 |

Policy-gap/integrity views (TAX-077), appeals (TAX-069/070), EOI (TAX-071/072),
workflow learning and other expansion features keep their existing phase. A backend
record or completed algorithm is not evidence of a usable screen. Expansion
acceptance must identify the actual authorized surface: Vati or native Frappe/tool.
No promise of a new custom screen for every backend module is implied.

## Navigation and login

Vati is the main entrance. Staff navigate Overview / My cases / Management (when
permitted) / Administration (when permitted). A case exposes Summary, Evidence,
Relationships, AI briefing and Review/decisions. Preserve case reference and
breadcrumbs. Keep the component showcase in development/design access, outside
normal operational navigation. Taxpayers have their own submissions/requests/account
navigation and never receive staff-tool links.

From a case, “Open investigation in Jube” opens the mapped native case in a new tab.
“Explore in OpenAleph” opens the matching authorized collection/entity/diagram.
Evidence links open the corresponding protected document where supported. Keep the
Vati tab available as the return path. Frappe Desk is the staff administration entry.
Do not substitute a generic service homepage for a missing record-specific link.

Use configured environment base URLs and canonical external-ID mappings. Verify URL
patterns against the deployed, pinned UI; do not derive browser routes from API paths.
Resolve permissions server-side before returning a destination, allowlist origins,
encode identifiers and never place credentials/API keys in a URL. Native permissions
must independently enforce the same case/collection boundary, including a copied URL.
A link is navigation, not authorization. Explain unavailable services, missing mappings,
login requirements, and recorded-only data; never pretend an unavailable link worked.

Frappe provides real taxpayer/officer login, session expiry, logout and supported
account recovery; test a safe return to the requested permitted page. Password-reset
email delivery needs configured mail or an explicitly documented administrator-assisted
local recovery flow. A role dropdown is allowed only in fixture development.
Jube/OpenAleph retain separate native accounts/sessions initially. Map staff identities
and grants explicitly; do not give ordinary users shared administrator credentials.
Vati logout must not claim to terminate those separate native sessions. Shared SSO,
federated logout and iframe embedding are not required for this iteration; revisit
through a reviewed decision if needed.

## Adapt the premade tools

Jube: configure tax workflow statuses, queues, displayed fields, forms, selection
reasons, assignments and risk rules using the locked version's supported configuration.
Jube remains operational case authority; Frappe remains approved-decision authority.
Test every exposed native mutation. Native status/closure/approval-like actions must
not bypass approved Frappe decisions, segregation of duties or the audit trail. If a
native action cannot be constrained safely, deny it server-side for pilot roles and
route it through Vati; hiding a button is insufficient. Authorized operational changes
must synchronize back to Vati. Only designated administrators configure risk rules.

OpenAleph: provision restricted synthetic collections, map documents and FtM entities,
expose relevant search/filter/relationship navigation, and verify originals, citations,
conflicting ownership and candidate-match disclosure. Collection permissions must not
leak another case merely because both cases share a tenant. Configure per-case collections
or another proven permission mapping. Search, graph and direct document access must all
honor it. Native editing must not silently overwrite authoritative evidence or decisions.

FollowTheMoney supplies entity/relationship schemas and mappings, not a separate
login application. Its visible result is profiles, ownership/payment relationships,
provenance and interactive exploration through OpenAleph. Docling, yente and LangGraph
are backend capabilities surfaced in these workflows, not additional required portals.

Configure first; apply focused Vati adapters/UI changes next. Fork or extensively
retheme upstream only when a demonstrated requirement cannot be met otherwise, with
source/version, maintenance implications and validation recorded. Reuse native patterns;
consistent labels, context and permissions matter more than identical styling.

## Full pilot versus reduced laptop demonstration

The full pilot includes usable Jube and OpenAleph native screens on an authorized
local or remote deployment, real Vati/Frappe authentication and connected case workflows.
Recorded extraction/matching results may be disclosed as recorded evidence, but cannot
substitute for demonstrating a promised live search or interactive native UI.

TAX-095 may retain a resource-constrained profile with recorded OpenAleph/Docling/yente
outputs. That profile is a reduced demonstration, not acceptance of the full native-tool
experience. For full acceptance, provision the required native UI services on suitable
hardware and measure their resource use; no paid platform or simultaneous all-services
laptop deployment is assumed. Do not start services or change billing merely to update
scope.

## Acceptance and sequence

1. Finish adapter packaging, event wiring, findings and real API/runtime work
   (TAX-085–090, 096–097), then the hosted authenticated auditor app (TAX-091).
2. Verify Jube and OpenAleph native tax experiences (TAX-107/108), add permission-checked
   navigation (TAX-106) and protected document access (TAX-109). These tasks can progress
   independently where their declared dependencies permit.
3. Complete AI/specialist and taxpayer assistant journeys (TAX-093, 098–102).
4. Test connected journeys under TAX-103 and demonstrate the five scenarios under TAX-058;
   use TAX-082 for officer usability validation, not screenshots alone.

Acceptance covers real logins, role/tenant/case isolation, native destination context,
unauthorized direct URLs, service failures, stale mappings, sessions and return navigation.
Show an officer evidence request reaching the taxpayer and the response returning to the
same case. Capture the actual configured native screens as well as Vati at relevant
viewports, keyboard/accessibility evidence and human visual review. Optional screenshot
skips, fixture test passes or a running container do not establish full acceptance.
