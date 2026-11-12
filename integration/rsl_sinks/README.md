# rsl_sinks

Outbound contracts from Vati to RSL systems and a fixture sink (TAX-124,
[ADR-048](../../docs/decisions/ADR-048-outbound-results-to-rsl.md)). Owner: Gupta.

These are **contracts, not live integrations**. Every shape is an assumption until RSL
supplies a specification: contract IDs end in `-assumed`, and no real host, path or field
name is claimed. The stated route is RSL's WSO2 API Manager. Nothing here opens a network
connection.

| Kind | Systems | Contract IDs | Authority |
|---|---|---|---|
| `findings_pack` | `audit_system`, `online_refunds` | `rsl.<system>.findings_pack.v0-assumed` | `findings_for_rsl_review` |
| `lane_recommendation` | `online_refunds` | `rsl.online_refunds.lane_recommendation.v0-assumed` | `recommendation_only` |
| `evidence_requested` | `audit_system`, `online_refunds` | `rsl.<system>.evidence_requested.v0-assumed` | `notice_only` |

Every message names its RSL record (`rsl_reference`), the TIN and the Vati case. No
contract has a field that sets an RSL status, pays, offsets or assesses; `validate_message`
refuses those names and any field outside the contract. Money is a decimal string with at
most two places; floats are refused.

`FixtureRslSink(directory, tenant_id, unavailable=[...])` receives messages the way a
WSO2-published endpoint would:

- every receipt carries `fixture: true` and a `FIXTURE-` receipt ID;
- the same idempotency key with the same message returns the stored receipt; with a
  different message it is refused (`idempotency_key_reused`);
- a system configured as unavailable raises `SinkUnavailable` (retryable) and records nothing;
- `lookup` finds an earlier delivery by key and `received` lists accepted messages.

The site binding is `vati_tax.integrations.config.rsl_sinks` (site key `vati_rsl_sinks`:
`directory`, `vati_tenant_id`, `unavailable`). The sender is `vati_tax.rsl_outbound`.

```sh
python3 -m unittest tests/integration/test_rsl_sinks.py -v
```
