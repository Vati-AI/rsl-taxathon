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
| Risk-based audit system (PSRM) | `referral` (audit case reference, selection reason, risk sets whose owner must be `RSL`; TAX-123) | `rsl.audit_system.referral.v0-assumed` |
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

## Consumers

- TAX-121 assurance checks (`backend/vati_tax/vati_tax/assurance`).
- TAX-123 audit referrals (`backend/vati_tax/vati_tax/rsl_referrals`, [ADR-047](../../docs/decisions/ADR-047-rsl-audit-referrals.md)): reads `audit_system.referral` per taxpayer, opens one Vati case per RSL audit case reference and keeps RSL's risk sets and scores as RSL-owned inputs. `fixtures/audit_referrals.json` is the synthetic referral fixture in the site fixture format.
- TAX-122 claim intake (`backend/vati_tax/vati_tax/rsl_intake`, ADR-043): reads `online_refunds.claim` per taxpayer and records each claim as a sent `Vati Submission` with source `rsl_online_refunds`, keeping references and hashes only.

Results go back to RSL through the outbound contracts in [`../rsl_sinks`](../rsl_sinks/README.md) (TAX-124).
