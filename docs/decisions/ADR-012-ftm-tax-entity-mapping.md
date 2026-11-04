# ADR-012: FtM tax entity mapping and OpenAleph entity ingest

Status: proposed (TAX-036, pending review by Keletso as integration area owner)
Owner and task: Leseli (owner), TAX-036; implemented by leseli-claude-20261104-036
Date: 2026-11-04

Context and smallest prototype need: ARCHITECTURE.md represents entities and relationships as FtM mappings "with documented tax extensions", and treats OpenAleph as a derived, rebuildable index. INTEGRATIONS.md asks for taxpayers mapped to Company/Person, ownership to Ownership and evidence to Document, with IDs and evidence links preserved and entities validated before indexing. TAX-041 (evidence and economic-relationship view), TAX-037 (yente), TAX-049, TAX-053 and TAX-074 build on one mapping. FtM has no invoice schema, and the Vati API has no server yet.

Decision:

- `integration/followthemoney/tax_mapping.py` maps a `tax_entity_bundle.v1` to FtM entity dicts using only schemata and properties of the locked FtM source. It runs on the standard library only, so it runs in `scripts/dev.py check` and needs no FtM install (PyICU does not build on every developer machine).
  - Company, Person or LegalEntity for parties.
  - Ownership (asset must be an FtM Asset, so a company).
  - Contract plus ContractAward for contracts.
  - UnknownLink for invoices (supplier → buyer, role `Purchase invoice` / `Sales invoice`).
  - Payment for payments, and Document for TAX-032 evidence.
- Allowed schemata, properties, value types and reference ranges come from `ftm_schema_subset.v1.json`. `scripts/ftm_schema_subset.py` generates it from the locked YAML schema files and records the commit and each file's SHA-256. `--check` detects drift after a lock bump.
- What FtM cannot hold goes in a `vati_tax_extension.v1` record per entity, never in a made-up schema or property: typed net/VAT/gross amounts, invoice direction, related-party flag, party role, the source record and the contract `EvidenceRef` citations.
- The evidence SHA-256 stays in the extension, not in `Document.contentHash`. That property is FtM's `checksum` type. OpenAleph strips it from non-admin bulk writes (`remove_checksums`), and Aleph uses it for its own archive hash.
- Every record must name its source (`frappe` or `synthetic_fixture`). Cited evidence must exist in the bundle, carry a page or record locator, and quote the stored original's SHA-256. Otherwise the bundle is rejected as `invalid_domain_input` with reasons such as `provenance_hash_mismatch`, `unknown_evidence`, `missing_locator` and `missing_provenance`.
- Conflicting ownership is flagged, never resolved. Both records stay mapped and both reference the conflict:
  - `disagreeing_ownership_records`: same owner, asset and type, overlapping periods, different percentages.
  - `ownership_exceeds_100_percent`: direct stakes in one asset add up to more than 100% on some date.
- Entity IDs are `vt-` plus 40 hex characters of SHA-256(tenant, kind, record ID). They are deterministic and tenant-scoped. OpenAleph additionally signs them per collection.
- `integration/openaleph/entity_ingest.py` writes with `POST /api/2/collections/{id}/_bulk` and keeps replays safe with a caller-held ledger of `entity_id -> digest`. Unchanged entities are skipped and only new ones are sent. A changed entity is refused as `idempotency_conflict` and nothing is sent: bulk writes go through FtM's fragment aggregator, so a rewrite is not relied on to replace old values.

Alternatives and tradeoff:

- Install `followthemoney` and build proxies with it: this gives exact cleaning and validation, but brings PyICU and SQLAlchemy into the CI and developer environments, and both fail on Windows here (ICU build, Smart App Control). The subset approach checks schema, property, type and range from the same source, but does not run FtM's value cleaning. OpenAleph still cleans on write (`clean=True` for non-admin keys).
- Model an invoice as a Payment or a Contract: it would read as a settled payment or a legal agreement, which it is not. UnknownLink with a role is honest. The cost is that amounts are not FtM properties, so OpenAleph search sees them only in the description text.
- Put tax fields in FtM `notes`/`description` only: they would be searchable but untyped. The typed extension is the authoritative copy, and the description is a readable summary.
- Overwrite changed entities in OpenAleph: simpler, but it could leave stale values merged into an entity that an auditor reads as evidence.

Official sources and version/date checked (2026-11-04):
- FtM at commit `c8c457a5c80fcf3ebe134f49b8dc60b417f0b860` (integration/upstreams.lock.json, version 4.11.1). Its `followthemoney/schema` and `followthemoney/types` are identical to tag `v4.11.2` (git diff), which is the version in OpenAleph's locked `requirements.txt` and image.
- `followthemoney/schema.py`: `required` is read per schema, not inherited.
- `followthemoney/helpers.py`: `remove_checksums`.
- `followthemoney/namespace.py`: `Namespace.apply` signs IDs.
- OpenAleph at commit `f89ff18d83fcfebdadc6b2db83fb9e2fbb5397c2`: `aleph/views/collections_api.py` (`bulk`: WRITE, `can_bulk_import`, `safe`/`clean` forced for non-admins, 204) and `aleph/logic/processing.py` (`bulk_write`, `collection.ns.apply`, aggregator `put`).

Verification performed:
- `scripts/ftm_schema_subset.py --check` against the locked source.
- 25 tests in `tests/integration/test_ftm_tax_mapping.py`, all passing, covering:
  - schema conformance;
  - relationships;
  - provenance rejection;
  - conflicting ownership (over 100%, disagreeing records, successive and beneficial stakes not flagged);
  - repeated ingest against a fake of the locked `_bulk` route (second run sends nothing, new records alone, changed entity refused, failed batch retryable, no WRITE access forbidden);
  - all five TAX-024 scenarios mapping and validating.
- Not performed:
  - Validation with the real FtM library. It does not import on this Windows machine: PyICU does not build, and Smart App Control blocks SQLAlchemy's compiled extension even with `DISABLE_SQLALCHEMY_CEXT_RUNTIME=1`.
  - A live OpenAleph run (Docker was not running).

Consequences and revisit trigger:
- TAX-041 reads entities, extensions and conflicts.
- A future Vati API persists the extension records and ingest ledger (TAX-030 service links carry OpenAleph entity IDs).
- Revisit when:
  - FtM is bumped (rerun `--check`);
  - OpenAleph gets a supported entity update/delete path for changed records;
  - invoice amounts must be searchable as numbers;
  - real taxpayer data replaces synthetic fixtures.
