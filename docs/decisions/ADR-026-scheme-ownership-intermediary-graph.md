# ADR-026: Scheme, beneficial ownership and intermediary graph

Status: proposed (TAX-074, pending human review)
Owner and task: Keletso (owner), TAX-074; implemented by claude-keletso-tax074
Date: 2026-11-05

Context and smallest prototype need: TAX-074 links taxpayer cases to schemes and models legal and beneficial ownership, directorships, advisers and intermediaries, and conflicting sources. TAX-036 (ADR-012) already maps parties, Ownership, contracts, invoices, payments and evidence to FtM. It flags ownership conflicts and never resolves them. It has no directorship, adviser or scheme records. TAX-075 (network pattern review) and TAX-041 (relationship view) will read the graph. The graph must not become a guilt-by-association engine. It must not leak across tenants or cases, and it must stay deterministic for replay. There is no Vati API server or graph database yet. (ADR-024 and ADR-025 are taken by open PRs #174 and #176, so this ADR takes 026.)

Decision:

- `integration/followthemoney/scheme_graph.py` (standard library only) takes a `scheme_graph_input.v1`. The input holds one tenant and case ID, the case's taxpayer party, a TAX-036 `tax_entity_bundle.v1`, and the records the bundle lacks: `directorships`, `representations` (advisers and intermediaries), `schemes`, `scheme_participants`, evidence-backed `case_scheme_links`, and the `extraction_revisions` that the citations were read from.
- Everything goes through FtM first. The bundle uses `tax_mapping.map_bundle`. The new records become FtM `Directorship`, `Representation`, `Project` (a scheme) and `ProjectParticipant`, with `vati_tax_extension.v1` records. They are checked against the same locked schema subset; `scripts/ftm_schema_subset.py` now includes these four schemata. The graph is built from the resulting entities and extensions.
- Node types: `case`, `party` (Company / Person / LegalEntity) and `scheme`. Evidence documents are provenance on edges, not nodes, so sharing a document never connects two parties.
- Edge types keep the roles apart:
  - Ownership is split by `ownershipType`. `direct` becomes `legal_ownership`. `beneficial` and `ultimate` become `beneficial_ownership`. `indirect` becomes `indirect_ownership`. A record with no type becomes `ownership_type_unstated`; the type is never guessed.
  - The other edge types are `directorship`, `adviser_to` (tax adviser, accountant, legal adviser, statutory auditor), `intermediary_for` (agent, nominee shareholder or director, corporate service provider, trustee), `scheme_participation`, `case_subject` and `case_scheme_link`.
  - Scheme, participation and case-to-scheme links must cite evidence.
- Each edge comes from exactly one source record. It carries that record, the FtM entity ID and digest, and each citation's evidence ID, SHA-256, locator and extraction revision (`null` when none was recorded). Records that disagree stay separate edges. `contradictions` reuses TAX-036's checks: disagreeing records, and direct stakes over 100%. It adds beneficial stakes over 100%, counting each owner once at its lowest recorded percentage. Each contradiction is `unresolved`. A query result lists every contradiction that involves a returned edge, and names the other edges even when a limit left them out.
- No guilt by association:
  - No node or edge has a risk, fraud or abuse field.
  - Unknown input fields are refused. Field names or free text with labelling words (fraud, risk, suspicious, sham, shell, flagged and similar) are refused as `risk_label_not_allowed`.
  - Fields the TAX-036 mapper does not know, such as a `risk_flag` on a party, are dropped. The graph stays byte-identical, so a label cannot spread to neighbours.
  - `connections` returns paths with per-step descriptions ("X is recorded as tax adviser to Y (source representation REP-1)") and evidence IDs. Each path has `interpretation: null`. The result's `meta.review_route` sends any assessment to an officer through the existing `finding.review` action.
- Scope:
  - `SchemeGraphStore` keeps one graph per (tenant, case). A query reads only that graph, so data that only another case or tenant recorded is never reachable.
  - Node IDs are tenant-scoped (TAX-036 `entity_id`). The same party or scheme in two cases of one tenant has one ID, but each case keeps its own attributes and edges.
  - Reads need a `case.read` role (auditor, specialist, supervisor, service; a test keeps this equal to policy.py). A tenant, case or node outside the scope is `not_found`.
  - `scheme_cases` lists only case IDs the caller says it may read, within the caller's tenant.
- Bounds: `neighbourhood` has depth ≤ 4 (default 2) and nodes ≤ 200 (default 50). `connections` has depth ≤ 4, paths ≤ 25 and a 5,000-step search budget. Both are breadth-first in edge-ID order. `meta.truncated` names each limit that was hit.
- Replay safety: IDs are hashes, and output is sorted. The same input again is `replayed` and changes nothing. New records are added; nothing is removed. A changed record under an existing ID is refused as `idempotency_conflict` and nothing is applied, as in the ADR-012 ingest ledger. Only inputs marked `synthetic: true` are accepted.

Alternatives and tradeoff:

- Merge identical relationships from different sources into one edge with several sources. The graph would be smaller, but a disagreement could be averaged away or overwritten, which the task forbids.
- Model schemes as a Vati-only object. FtM `Project` / `ProjectParticipant` ("an activity carried out by a group of participants") fits, keeps schemes indexable in OpenAleph, and uses the same validation. The case link stays Vati-only, because cases are Jube/Vati objects, not FtM entities.
- One tenant-wide graph that filters by case at query time. This allows cross-case analytics, but one missed filter would leak across cases. Per-case graphs make the isolation structural. Cross-case work (TAX-075) must name the cases it may read.
- Allow free text with a warning. It is simpler, but the label would reach the UI and model prompts. Refusing it pushes assessment to officer review.
- Add a graph database (Neo4j and similar). Not needed for synthetic fixtures; it would be a new shared dependency and a second store.

Official sources and version/date checked (2026-11-05):

- FtM at the locked commit `c8c457a5c80fcf3ebe134f49b8dc60b417f0b860` (version 4.11.1): `followthemoney/schema/Directorship.yaml`, `Representation.yaml` ("a mediatory, intermediary, middleman, or broker acting on behalf of a legal entity"; agent → client), `Project.yaml`, `ProjectParticipant.yaml`, `Interest.yaml` (`role`) and `Interval.yaml` (`startDate`, `endDate`, `recordId`, `proof`).
- The contract `Entity.ftm_schema` enum in integration/api/openapi.json already lists `Directorship`.
- `scripts/ftm_schema_subset.py --check` passes against that source. The generator now hashes YAML with LF line endings, so a Windows (CRLF) checkout and a macOS/Linux checkout record the same digests. The previous file had CRLF digests.

Verification performed:

- `tests/integration/test_scheme_graph.py` has 26 tests, all passing. They run in `scripts/dev.py check` and CI through `tests/integration` discovery. They cover:
  - the FtM schemata of the new records;
  - separate legal, beneficial, director, adviser and intermediary roles, and an unstated type that is never guessed;
  - schemes as evidence-backed grouping objects linked to the case;
  - conflicting sources kept as separate edges, each with its source record, evidence SHA-256 and extraction revision;
  - contradiction listing, including beneficial stakes over 100% and truncation that does not hide the other side;
  - a changed record refused, and a new source added alongside;
  - labelling words and fields absent from every neighbourhood and connection output, refused on input, and not spread from a flagged party;
  - case, tenant and role isolation, `scheme_cases` limited to readable cases;
  - depth, node and path truncation flags and limit validation;
  - determinism independent of input order, and replay as a no-op;
  - synthetic-only and provenance refusals.
- Changing the code to add a proximity field, or to skip the scope and role check, made 2 and 8 tests fail. The code was then restored.
- The TAX-036 suite (25 tests) still passes. Its schema-count expectation now includes the four new schemata at 0.
- Not performed: a live OpenAleph read-back, or ingest of the new FtM schemata. Graph inputs are synthetic fixtures (`data/synthetic/tax074_scheme_graph.json`). No Vati API route exposes the graph yet.

Consequences and revisit trigger:

- TAX-075 builds network review on `neighbourhood` / `connections`. Any pattern it surfaces goes to the analyst review queue, never into a graph label.
- TAX-041 can show the per-case graph.
- A Vati API route (`GET /cases/{case_id}/graph`) and persistence (the store's records plus the TAX-030 service links) are follow-up work. That route must take the tenant from the session and check `case.read` on the case.
- Revisit when:
  - real data replaces synthetic fixtures;
  - OpenAleph indexes the new schemata (live smoke needed);
  - cross-case analytics is approved;
  - FtM is bumped (rerun `--check`).
