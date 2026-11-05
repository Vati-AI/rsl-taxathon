# payment_channels

Lawful payment-channel evidence contracts (TAX-079, [ADR-027](../../docs/decisions/ADR-027-lawful-payment-channel-contracts.md)). Owner: Keletso.

These are **contracts, not live integrations**. The adapters parse files that were already supplied: a taxpayer upload, an officer upload of documents handed over, a statutory response, or a provider file the taxpayer consented to. Nothing here opens a network connection, calls a bank, mobile-money, payment-network or wallet API, holds credentials or scrapes a site. A live connector needs a legal authority confirmed by a qualified reviewer, a data-handling decision for real data and a separate reviewed change. It must not be added to these modules.

Standard library only, plus the TAX-036/048/073/074 pure helpers. Callers put the repository root, `integration/followthemoney` and `integration/payment_channels` on `sys.path` (as the tests do).

```sh
python3 -m unittest tests/integration/test_payment_channels.py -v   # fixtures: data/synthetic/tax079_payment_channels*
```

## Adapters (`channel_adapters.py`)

One offline adapter per channel. Each implements `PaymentChannelAdapter` (`channel`, `adapter_id`, `adapter_version`, `document_format`, `media_type`, `parse(bytes)`). Every format is CSV (`text/csv`, an ADR-011 evidence media type) with an exact header.

| Channel | Format | Columns |
|---|---|---|
| `bank_statement` | `bank_statement_csv.v1` | `account_ref, transaction_ref, booking_date, value_date, transaction_type, direction (credit/debit), amount, currency, counterparty_name, counterparty_account_ref, narration` |
| `mobile_money` | `mobile_money_csv.v1` | `wallet_ref, transaction_ref, transaction_date, transaction_type, direction (received/sent), amount, fee, currency, counterparty_name, counterparty_wallet_ref, agent_ref, narration` |
| `foreign_payment` | `foreign_payment_csv.v1` | `account_ref, transfer_ref, instruction_date, settlement_date, direction (incoming/outgoing), instructed_amount, instructed_currency, settled_amount, settled_currency, exchange_rate, rate_source, counterparty_name, counterparty_account_ref, counterparty_country, intermediary_bank_ref, narration` |
| `digital_wallet` | `digital_wallet_csv.v1` | `wallet_ref, transaction_ref, transaction_date, direction (in/out), asset_kind (fiat_balance/virtual_asset), asset_code, quantity, fiat_value, fiat_currency, valuation_source, counterparty_name, counterparty_wallet_ref, narration` |

- Money is a `Decimal` from a string (TAX-048 `parse_money`): non-negative, at most two places, with an ISO 4217 currency. The direction column gives the sign. Nothing passes through `float`.
- Exchange rates come only from the file (TAX-073 `parse_rate`/`convert`, half-up to cents). A currency change without a supplied rate is an `exchange_rate_not_supplied` gap. A settled amount that does not equal `instructed × rate` is a `booked_conversion_inconsistent` contradiction. A rate without a currency change is refused.
- A virtual asset keeps its quantity and asset code. It has an ISO-currency amount only when the file supplies `fiat_value`, `fiat_currency` and `valuation_source` together. Otherwise there is a `fiat_value_not_supplied` gap and no price is looked up.
- One file covers one account. Transaction references are unique within a file. Cells that start with `=`, `+` or `@` are refused (CSV injection). Labelling vocabulary in a column name or text cell is refused as `risk_label_not_allowed` (the ADR-026 rule). The original file stays in the evidence store for an officer.

## Submission envelope (`channel_contracts.py`)

`payment_channel_submission.v1` describes one supplied file:

- `tenant_id`, `case_id`, `taxpayer_id`, `account_holder` (`record_id`, `name`, `kind`), `record_set_id`, `record_set_version`, plus `supersedes` and `correction_reason` for corrections.
- `permission`: `basis`, `reference` (approval reference), `requested_by`, `approved_by`, `legal_authority` (`reference`, `status` `unverified`/`verified`), `scope` (`case_id`, `taxpayer_id`, `channels`, `account_refs`, `period_start`, `period_end`), `granted_at`, `expires_at` (at most 366 days).
- `provenance`: `channel`, `document_format`, `provider` (`provider_id`, `name`, `synthetic: true`), `evidence` (TAX-032 `evidence_id`, `document_sha256`, `file_name`, `media_type`, `size`), `retrieval_method`, `received_at`.

| Basis | Retrieval method | Requested by | Approved by |
|---|---|---|---|
| `taxpayer_supplied` | `taxpayer_upload` | the taxpayer (its own `taxpayer_id`), who is also the uploading actor | none |
| `taxpayer_supplied` | `officer_upload` | an officer (auditor, specialist, supervisor) | none |
| `statutory_request` | `statutory_response` | an officer | a holder of `evidence_request.authorise` (auditor, supervisor; never a service). `legal_authority` required |
| `taxpayer_consent` | `consented_provider_file` | a holder of `evidence_request.authorise` | the described taxpayer |

`ChannelRecordStore.ingest(envelope, document_bytes, actor, as_of=...)` refuses the file, and records nothing, unless all of the following hold:

- The actor is a member of the envelope's tenant and holds `evidence.register`. A taxpayer actor registers only its own `taxpayer_upload`, and only an officer may make an `officer_upload`.
- The file's SHA-256 and size equal the evidence record.
- The permission names this case and taxpayer, covers the channel, every account and every transaction date, was valid when the file was received, and has not expired at `as_of`.

Each line becomes an immutable `payment_channel_record.v1` with:

- the transaction (reference, account, date, `inflow`/`outflow`, Money or `null`, counterparty, narration, channel `details`);
- the full permission;
- the provenance, including an `EvidenceRef` to `line:N` and the adapter ID and version;
- gaps;
- `interpretation: null`.

A correction is the next `record_set_version`, with `supersedes.record_set_sha256` equal to the current version's digest. Earlier versions stay readable (`include_superseded`, `history`). A replay returns `replayed`. Changed content under a recorded version or evidence ID is an `idempotency_conflict`. A version gap or stale digest is a `version_conflict`.

## Reads, mapping and graph

- **Reads.** `records`, `analysis`, `history` and `ftm_bundle` need `evidence.read` (auditor, specialist, supervisor, service) for one (tenant, case). Another tenant or case is `not_found`. Lists use the API standard's page shape (default 25, max 100, `next_cursor`).
- **Analysis.** `analysis` lists contradictions across the case's current records. Two files with the same provider transaction reference but different facts give `same_transaction_reference_differs`, and a line's conversion check can give `booked_conversion_inconsistent`. Each contradiction is `unresolved` and needs officer review. Identical copies of a transaction in two files are context. Payment method or channel never produces an entry.
- **FtM mapping.** `ftm_bundle` builds a TAX-036 `tax_entity_bundle.v1` and maps it with `tax_mapping.map_bundle`. Each record with an amount becomes an FtM `Payment` whose extension cites the line. Each counterparty is its own party, so parties are never joined on names. `channel_links` keeps each Payment's permission, provenance, gaps and contradiction IDs. Records without an ISO-currency value are listed in `unmapped`.
- **Graph.** `scheme_graph_input` wraps the bundle as a TAX-074 input. The graph adds no edge from payments.
- **Export and retention.** Channel records are not exported here. Export goes through the TAX-062 `export.request` / `export.approve` manifest, and the originals follow its retention metadata.

Error codes are ADR-003 `ErrorCode` values (`invalid_domain_input`, `forbidden`, `tenant_scope_denied`, `not_found`, `idempotency_conflict`, `version_conflict`). `ChannelContractError.error_response(correlation_id)` gives the contract `ErrorResponse`, and its `field_errors[0].code` is the reason.
