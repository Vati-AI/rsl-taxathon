# rsl_sources

Read contracts and a fixture adapter for RSL's existing systems (TAX-111,
[ADR-040](../../docs/decisions/ADR-040-rsl-existing-systems-integration.md)). Owner: Keletso.

These are **contracts, not live integrations**. Every shape is an assumption until RSL
supplies a specification: contract IDs end in `-assumed`, and no real host, path or field
name is claimed. Nothing here opens a network connection.

| System | Kinds | Contract IDs |
|---|---|---|
| e-Tax | `vat_return`, `wht_return`, `cit_return`, `paye_roster` | `rsl.etax.<kind>.v0-assumed` |
| Lekuka / IDMS | `invoice`, `supplier_integration` | `rsl.idms.<kind>.v0-assumed` |
| Online Refunds Application | `claim` (with the invoice schedule and uploaded copies) | `rsl.online_refunds.claim.v0-assumed` |
| RSL refund-fraud model | `score` (owner must be `RSL`; model ID and version kept) | `rsl.rsl_fraud_model.score.v0-assumed` |
| ASYCUDA World | `entry` | `rsl.asycuda.entry.v0-assumed` |
| Registry | `entity` | `rsl.registry.entity.v0-assumed` |
| Central Bank | `cross_border_payment` (CMA payments may be absent) | `rsl.central_bank.cross_border_payment.v0-assumed` |

Money is a decimal string with at most two places, and floats are refused. Dates use
`YYYY-MM-DD` and VAT periods use `YYYY-MM`.

`FixtureRslSources` serves synthetic records the way a WSO2-published read API would:

- every envelope carries `fixture: true` and the contract ID;
- reads are scoped to one tenant and one taxpayer;
- a source configured as unavailable returns `status: unavailable` with no records;
- Vati keeps references (system, record ID, contract, retrieval time and a record
  SHA-256), never a second store.

`lookup` serves reference data, such as registry TINs and Lekuka integration status.
`asycuda_entries_from_tax073` maps the TAX-073 customs fixture onto the ASYCUDA contract.

```sh
python3 -m unittest tests/integration/test_rsl_sources.py -v
```
