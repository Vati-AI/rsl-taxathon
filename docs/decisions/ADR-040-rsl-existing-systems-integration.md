# ADR-040: Plug into RSL's existing systems through WSO2 instead of rebuilding them

Status: proposed
Owner and task: Keletso; scope update from [FRAMEWORK.md](../reference/FRAMEWORK.md); implemented by TAX-111 to TAX-118
Date: 2026-11-06

Context and smallest prototype need:
The framework update names RSL systems the original proposal did not: e-Tax, Lekuka/IDMS,
the Online Refunds Application, RSL's refund-fraud model, ASYCUDA World and the WSO2
platform (Integrator, API Manager, Identity Server). As written, three Vati components
could look like duplicates: the Frappe portal (e-Tax/Lekuka self-service), Jube (RSL's
fraud model) and the Vati Integration API (WSO2). Judges score pilot potential on how the
solution plugs in. We have no RSL access, specifications or data, so the prototype needs
contracts and explicit fixtures, not guessed endpoints.

Decision:
1. **RSL systems are sources of record.** Vati reads declared positions (e-Tax), invoices
   (IDMS warehouse), refund claims (Online Refunds Application), fraud scores (RSL model)
   and customs entries (ASYCUDA World). It never asks the taxpayer again for data RSL holds
   and never keeps a competing copy; it stores references, hashes and the derived results
   it needs for a case.
2. **One versioned source contract per RSL system** (TAX-111). Shapes are assumptions until
   RSL supplies a specification. The fixture adapters always identify themselves as
   fixtures, and so do their records. No real host, path or field name is invented.
3. **WSO2 is the gateway and identity provider.** The Vati `/api/v1` service is published
   behind WSO2 API Manager. Staff and taxpayer sign-in uses WSO2 Identity Server through
   OIDC, mapped onto the existing Frappe users, roles and tenant membership (TAX-117).
   Frappe keeps permissions and approval authority, as before. Until TAX-117 records a
   working smoke test, local Frappe login (ADR-034) stays the pilot path.
4. **Component positioning.** The Frappe portal is the evidence-submission layer reached
   from e-Tax. Jube is the case and workflow engine; RSL's fraud score is an input feature
   and is shown as RSL's, never re-labelled as a Vati score. ADR-001 service authority is
   unchanged.
5. **Hybrid mode.** A Lekuka/IDMS match is evidence, not yet a hard rule. An unmatched line
   becomes a question for the taxpayer, never a denial (TAX-112).

Alternatives and tradeoff:
Keep Vati as the front door with its own login: this is simpler for the demo, but reads as
rebuilding RSL's platform and weakens pilot potential. Live integration with RSL systems is
impossible without access. Contract-first fixtures keep the demo honest and give RSL a
clear integration surface.

Official sources and version/date checked:
None yet. The RSL system descriptions come from the user-supplied framework (public
research, 2026-11-06) and are unverified. TAX-117 must pin a WSO2 Identity Server / API
Manager version from official WSO2 documentation and record it here.

Verification performed (or named implementation task):
TAX-111 implemented the contracts and fixture adapter (`integration/rsl_sources`, tests in
`tests/integration/test_rsl_sources.py`), and TAX-118 exercised them on a sealed synthetic
population. No live RSL system or WSO2 instance has been tested; TAX-117 (WSO2 smoke test)
is still open.

Consequences and revisit trigger:
Taxpayer and officer journeys start from RSL systems in the demo narrative. Revisit when
RSL supplies real specifications or access, or if TAX-117 shows WSO2 cannot run within the
demo resources (then a documented reduced demonstration applies).
