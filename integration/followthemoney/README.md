# followthemoney

Entity schema and mapping library. Owner: Keletso (integration area); TAX-036 mapping owned by Leseli. LangGraph implementation is owned by Gupta.

- Upstream: https://github.com/opensanctions/followthemoney
- Official documentation: https://followthemoney.tech/
- Configuration: `installed in the owning Python service`
- Status: tax entity mapping implemented (TAX-036, [ADR-012](../../docs/decisions/ADR-012-ftm-tax-entity-mapping.md)). It is checked against the locked schema source; it has not been run against a live OpenAleph yet.

Keep upstream external. Use its supported deployment with its own dependencies and volumes; do not copy source here. Pin a reviewed release/commit and record the deployment version in the integration runbook. See `docs/INTEGRATIONS.md` for contract and acceptance requirements.

## Tax entity mapping (TAX-036)

`tax_mapping.py` turns a `tax_entity_bundle.v1` into FtM entities, a `vati_tax_extension.v1` record per entity and a list of ownership conflicts. It uses only the standard library. Entities are validated against `ftm_schema_subset.v1.json`, which `scripts/ftm_schema_subset.py` generates from the locked source.

```sh
python3 scripts/upstreams.py fetch followthemoney   # locked source into ignored external/
python3 scripts/ftm_schema_subset.py --check        # needs PyYAML; fails if the subset drifted
python3 -m unittest tests/integration/test_ftm_tax_mapping.py -v
```

The locked commit `c8c457a` is version 4.11.1. Its `followthemoney/schema` and `followthemoney/types` directories are identical to tag `v4.11.2`, the version inside the locked OpenAleph image.

| Vati record | FtM schema | Main properties | In the extension |
|---|---|---|---|
| Party: company / person / unknown kind | `Company` / `Person` / `LegalEntity` | `name`, `taxNumber`, `registrationNumber`, `idNumber`, `country` | role (taxpayer, counterparty, owner) |
| Ownership | `Ownership` (owner → asset; asset must be a `Company`) | `percentage`, `ownershipType`, `startDate`, `endDate`, `recordId`, `proof` | percentage, conflict IDs |
| Contract | `Contract` (`authority` = buyer) and `ContractAward` (`supplier`) | `title`, `contractDate`, `recordId`, `proof` | related-party flag |
| Invoice | `UnknownLink` (subject = supplier, object = buyer, role `Purchase invoice` / `Sales invoice`) | `date`, `recordId`, `proof`, `description` | direction, currency, net/VAT/gross amounts, contract |
| Payment | `Payment` (payer → beneficiary) | `amount`, `currency`, `date`, `contract`, `recordId`, `proof` | invoice record and entity |
| Evidence (TAX-032 `Vati Evidence`) | `Document` | `fileName`, `title`, `mimeType`, `fileSize`, `date` | document SHA-256 |

Every record needs a `source` (`frappe` or `synthetic_fixture`, record type, record ID). Records may cite evidence with the contract `EvidenceRef` (`evidence_id`, `document_sha256`, `locator` with `page` and/or `record`). A cited hash must equal the stored original's hash, or the bundle is rejected as `provenance_hash_mismatch`. Rejections use the ADR-003 `invalid_domain_input` code with a `field` and a `reason`.

Ownership conflicts are flagged, never resolved. Both records stay mapped and both list the conflict ID:

- `disagreeing_ownership_records`: same owner, asset and type, overlapping periods, different percentages.
- `ownership_exceeds_100_percent`: direct (or untyped) stakes in one asset add up to more than 100% on some date.

`bundle_from_tax024` builds a bundle from a synthetic TAX-024 scenario record. It refuses records that are not marked synthetic.

Writing to OpenAleph is in `integration/openaleph/entity_ingest.py`.

## Scheme, ownership and intermediary graph (TAX-074)

`scheme_graph.py` builds one deterministic graph per (tenant, case) from FtM entities ([ADR-026](../../docs/decisions/ADR-026-scheme-ownership-intermediary-graph.md)). Its input, `scheme_graph_input.v1`, is a TAX-036 bundle plus `directorships`, `representations` (advisers and intermediaries), `schemes`, `scheme_participants`, evidence-backed `case_scheme_links` and the `extraction_revisions` the citations come from. These map to FtM `Directorship`, `Representation`, `Project` and `ProjectParticipant`.

- Nodes are `case`, `party` and `scheme`. The edges are `legal_ownership` (direct), `beneficial_ownership` (beneficial or ultimate), `indirect_ownership`, `ownership_type_unstated`, `directorship`, `adviser_to`, `intermediary_for`, `scheme_participation`, `case_subject` and `case_scheme_link`.
- One source record makes one edge. Each edge keeps the source record, the FtM entity digest and each citation's evidence ID, SHA-256, locator and extraction revision. Records that disagree stay separate edges and are listed as `unresolved` contradictions.
- No risk or fraud labels exist, and the input cannot add them (`risk_label_not_allowed`). `connections` returns described paths with `interpretation: null`. Assessment goes to officer review (`finding.review`).
- `SchemeGraphStore.neighbourhood` / `connections` read one case's graph for a `case.read` role, with depth, node and path limits and `meta.truncated` flags. A replay is a no-op, and a changed record is an `idempotency_conflict`.

```sh
python3 -m unittest tests/integration/test_scheme_graph.py -v   # fixtures: data/synthetic/tax074_scheme_graph.json
```
