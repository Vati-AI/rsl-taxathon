# ADR-033: Governed extension contracts for additional tax-domain playbooks

Status: proposed (TAX-080, pending review)
Owner and task: Gupta (owner), TAX-080; implemented by gupta-claude-20261105-080. Review: Leseli (evaluation fixtures), Keletso (contract categories), an RSL legal reviewer (legal-review questions).
Date: 2026-11-05

Context and smallest prototype need: TAX-080 asks for governed extension contracts for PAYE, withholding, treaty/permanent-establishment and digital-services playbooks. Each proposed domain must name its required sources, legal-review gate and evaluation fixtures before implementation. The repository already has the pieces such a playbook would plug into:

- the TAX-061 approved playbook registry (ADR-017);
- the TAX-052 legal-route registry and policy-gap gate;
- the TAX-056 evaluation gate (ADR-029);
- the TAX-050 imported-services trace, which already covers withholding on non-resident payments;
- the TAX-079 payment channels (ADR-027), the TAX-074 ownership graph (ADR-026) and TAX-071 EOI drafts;
- the ADR-031 model roles.

Nothing yet stops a new domain from being coded before its law, data and fixtures exist.

Decision:

- **One design-stage catalog, no parallel registry.** `ai/workflows/playbook_extensions.synthetic.json` holds one contract per domain: `paye_reconciliation`, `withholding_review`, `treaty_pe_facts` and `digital_services_review`. A contract is data only. It loads no code and cannot run. Implementation still enters the TAX-061 registry as a `proposed` entry with a builder in `PLAYBOOK_BUILDERS`, and approval still works as ADR-017 describes.
- **Each contract names:**
  - scope;
  - the contract `Finding.category` to use now, plus any new category it needs;
  - the deterministic checks that produce every figure (Decimal, cited records);
  - the `get_model` roles that may interpret results (ADR-031);
  - allowed operations and provenance, drawn from subsets of the TAX-061 allowlists (`apply_approved_action` is excluded);
  - advisory finding codes;
  - at least two legitimate alternative explanations;
  - tool, model-call and token budgets (tool calls at most 8, the TAX-022 run limit);
  - **required sources**, each with a system of record and a v1 status (`available` or `not_in_v1`);
  - a **legal-review gate**: the `rsl_legal_reviewer` role, the open legal questions, and `reviewed_by`/`reviewed_on`;
  - **evaluation fixtures**, one of each kind: legitimate, missing evidence, contradictory evidence, hostile injection, no verified route and cross-tenant, each with an expected outcome.
- **The loader refuses:**
  - a catalog not marked synthetic;
  - a stage other than `design`;
  - a domain with no sources, no legal questions or a missing fixture kind;
  - an unknown system of record, model role, operation or provenance value;
  - provenance that is not cited, case-scoped, advisory and deterministic;
  - `interrupt_for_human` without `human_checkpoint`;
  - a category outside the contract enum;
  - a finding code on the TAX-056 forbidden-conclusion list;
  - fewer than two alternative explanations;
  - missing or excessive budgets;
  - a non-synthetic or self-approving legal reviewer;
  - duplicate fixture IDs.
- **Readiness is computed, never declared.** `readiness()` returns stable blocker codes:
  - `source_not_in_v1:<id>`;
  - `legal_review_pending`;
  - `finding_code_not_in_legal_registry:<code>` (checked against the TAX-052 registry);
  - `fixture_not_authored:<id>` (checked against `data/synthetic/tax080_domain_fixtures.json`, which Leseli authors independently);
  - `contract_category_pending:<category>`.
  `check_registry()` reports any TAX-061 playbook entry named after a domain that still has blockers. A test runs it against the shipped registry.
- **Legal routes stay unverified.** The gate records that an RSL legal reviewer has answered the scoping questions. It is not a citation. Findings still carry `legal_route.status: unverified` until a reviewer verifies an authority version in the TAX-052 registry, and the policy-gap gate still blocks enforcement conclusions. The catalog states no Lesotho rate, threshold, schedule or treaty term. PAYE checks compare declared figures only and never compute tax due from tables. Treaty/PE facts never determine residence, treaty eligibility or whether a permanent establishment exists. Digital services never decide whether VAT or registration applies.
- **Current state (all four blocked):**

  | Domain | Missing sources | New category |
  |---|---|---|
  | PAYE | PAYE figures DocType | `paye` |
  | Withholding | per-payee withholding lines | `withholding` |
  | Treaty/PE | reviewed treaty instruments; foreign facts via an authorised EOI channel | `cross_border` |
  | Digital services | supplier registration register | none (uses `imported_services`) |

  All four also need the legal review, legal-registry entries and fixtures.

Alternatives and tradeoff:

- Add proposed entries to the TAX-061 registry now: the loader requires a builder in the code, so this would mean writing playbooks before their gates. Rejected.
- A prose design document only: easier to write, but nothing would stop a later PR from registering a domain whose sources or fixtures do not exist. The catalog makes that a failing test.
- A new Finding category per domain immediately: this is a contract change owned by Keletso (TAX-096), so it is recorded as a blocker instead. Digital services fit the existing `imported_services` category.
- Encode legal tests, such as a day-count threshold for PE: this would state law without an authority. Rejected; the checks list cited facts and the reviewer decides relevance.

Official sources and version/date checked (2026-11-05): repository sources only.

- `integration/api/openapi.json`: `Finding` category and kind enums, `LegalRoute` and `ProposedAction`.
- `vati_tax` `Vati Submission` `submission_type` options: `paye_return` and `withholding_return` exist; there are no PAYE or withholding line DocTypes (ADR-010).
- ADR-017 registry allowlists; the TAX-052 legal-route registry (synthetic placeholders only); the TAX-056 forbidden codes; ADR-031 model roles; ADR-026/027 and TAX-071 for graph, payment and EOI sources.

No Lesotho statute, regulation or treaty was consulted or encoded. No new dependency.

Verification performed: `tests/ai/test_playbook_extensions.py` covers:

- the shipped catalog loads with all four domains, each with sources, legal questions and every fixture kind;
- every shipped domain is blocked, with source blockers matching v1 status;
- twenty load-time policy violations;
- duplicate fixture IDs across domains;
- readiness clears only when sources, reviewer, legal-registry codes and fixtures are all present;
- a pending contract category blocks readiness;
- `check_registry` flags a registry playbook for a blocked domain and ignores tools and other playbooks;
- the fixture file reader;
- the allowlists match the contract `Finding` enums and are subsets of the TAX-061 registry allowlists;
- the shipped TAX-061 registry has no entry for a blocked domain.

`python -m ai.workflows.playbook_extensions` prints the current blockers.

Consequences and revisit trigger: implementing any of these domains is new task work. That work starts only after the blockers for its domain are cleared, through reviewed PRs:

- the backend adds the source DocTypes;
- Keletso adds any contract category;
- the RSL legal reviewer answers the gate questions and the TAX-052 registry gains reviewed entries;
- Leseli authors the fixtures.

The implementation then follows ADR-017 registration and must pass the TAX-056 gate with the new fixtures. Revisit when real legal review becomes available, when another domain is proposed, or if TAX-046 `playbook.approve` records replace the synthetic registry approvals.
