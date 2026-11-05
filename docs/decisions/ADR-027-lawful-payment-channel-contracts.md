# ADR-027: Lawful payment-channel extension contracts

Status: proposed (TAX-079, pending human review)
Owner and task: Keletso (owner), TAX-079; implemented by claude-keletso-tax079
Date: 2026-11-05

Context and smallest prototype need:

- TAX-079 extends the evidence the platform can describe to bank statements, mobile money, foreign/cross-border payments and digital wallets. PROJECT_SCOPE.md says that "neither novel payment methods nor unusual relationships establish wrongdoing".
- Building blocks already exist:
  - TAX-032 (ADR-011) stores originals with a SHA-256.
  - TAX-029 (ADR-007) defines who may register and read evidence.
  - TAX-062 (ADR-016) controls export and retention.
  - TAX-036 (ADR-012) maps FtM `Payment`.
  - TAX-073 parses supplied exchange rates.
  - TAX-074 (ADR-026) refuses labelling vocabulary and keeps contradictions separate.
- Missing: a contract that says, for each payment-channel record, under what lawful basis it was obtained, where it came from and how it maps onto those pieces.
- Live connectors are out of scope. There is no verified legal route, no provider agreement and no real data.

Decision:

- **Contracts only, offline only.** `integration/payment_channels/channel_adapters.py` defines `PaymentChannelAdapter` and four adapters:
  - `bank_statement_csv.v1`;
  - `mobile_money_csv.v1`;
  - `foreign_payment_csv.v1`;
  - `digital_wallet_csv.v1`.

  Each parses the bytes of one supplied CSV file with an exact header. CSV is used because `text/csv` is already an ADR-011 evidence media type, and JSON is not. No module opens a socket, calls an HTTP client or provider SDK, or reads credentials. A test checks the imports. Live connectors need a confirmed legal authority, a real-data handling decision and a separate reviewed change.
- **Permission on every record.** A `payment_channel_submission.v1` envelope must carry a `permission` with these fields:
  - `basis`: `taxpayer_supplied`, `statutory_request` or `taxpayer_consent`;
  - the approval `reference`, `requested_by`, `approved_by` and `legal_authority` (`status` `unverified` until a qualified reviewer verifies it; nothing is cited for it);
  - `scope`: case, taxpayer, channels, account references and period;
  - `granted_at` and `expires_at`, at most 366 days apart.

  Each basis allows specific retrieval methods and roles, all in existing TAX-029 terms, and adds no new policy action:
  - **taxpayer upload:** made by the taxpayer for its own `taxpayer_id`;
  - **officer upload:** an officer records documents handed over;
  - **statutory response:** an officer request approved by a holder of `evidence_request.authorise`, which is human-only;
  - **consent file:** requested by a holder of `evidence_request.authorise`, with consent given by the described taxpayer.

  The ingesting actor must be a tenant member holding `evidence.register`. Reads need `evidence.read` in one (tenant, case). Export stays the TAX-062 request/approve manifest, and retention follows its evidence class. A test keeps the role sets equal to `policy.ACTION_ROLES`.

  A file is refused, and nothing is recorded, when:
  - its permission is missing, expired at `as_of`, or not yet valid when the file was received;
  - the permission was granted for another case or taxpayer;
  - the permission does not cover the channel, an account or a transaction date.
- **Provenance on every record.** Each record carries:
  - the channel;
  - the synthetic provider;
  - the evidence ID and SHA-256, which must equal the supplied bytes; the size must match as well;
  - an `EvidenceRef` locator `line:N`;
  - the retrieval method;
  - `received_at`, `recorded_at` and `recorded_by`;
  - the adapter ID, version and format. The store sets these from the adapter that parsed the file; the client does not supply them.

  Records are immutable. A correction is the next record-set version, naming the digest of the version it supersedes. Old versions stay readable. A replay is a no-op. Changed content under a recorded version or evidence ID is an `idempotency_conflict`, and a stale or skipped version is a `version_conflict`.
- **Money.** `Decimal` from strings via the TAX-048 `parse_money`, with ISO 4217 currencies. Exchange rates are used only when supplied (TAX-073 `parse_rate`/`convert`):
  - A missing rate is an `exchange_rate_not_supplied` gap.
  - A settled amount that disagrees with `instructed × rate` is an unresolved `booked_conversion_inconsistent` contradiction.
  - A virtual asset has an ISO-currency amount only when a fiat value and its source are supplied. Otherwise it keeps its quantity and gets a `fiat_value_not_supplied` gap.
- **FtM and graph.** Current records become a TAX-036 `tax_entity_bundle.v1`: one FtM `Payment` per record, validated against the locked subset, with each extension citing its line. `channel_links` carry each Payment's permission, provenance, gaps and contradiction IDs. Counterparties are one party per record, so nothing is joined on a name or account reference; identity matching remains yente review work. `scheme_graph_input` hands the bundle to TAX-074, which adds no edge from payments.
- **No judgement from payment method.** Records have no risk, score, signal or label field, and `interpretation` is always `null`. Cash deposits, agent cash-in/out, foreign transfers and virtual assets are recorded only as descriptive `details`. Contradictions and gaps come from disagreeing or missing supplied data, never from the method. Labelling vocabulary in a column name, text cell or envelope field is refused as `risk_label_not_allowed`, reusing the ADR-026 word list.

Alternatives and tradeoff:

- *Live provider connectors (bank APIs, mobile-money APIs, chain explorers).* They would be more complete, but they need legal authority, provider agreements and credentials that do not exist. They would also be the first outward data pull in the platform, so they are rejected for this task.
- *JSON channel files.* Easier to nest, but not an accepted evidence media type. Adding one would change the ADR-011 upload contract for no gain over CSV.
- *Joining counterparties on account references.* A smaller graph, but a reference can be reused or mistyped. Joining on references or names is the guilt-by-association path that ADR-026 rejects. Matching stays with reviewed yente candidates.
- *Keeping labelling words from provider narrations as quoted text.* This avoids refusing a lawful file, for example one with a counterparty genuinely named "Shell ...". However, the word would then reach the UI and model prompts. The refusal follows ADR-026, and the original remains in the evidence store for an officer. **Known false-refusal cost; revisit with real data.**
- *A new `payment_channel.ingest` policy action.* It would be more explicit, but the existing `evidence.register`/`evidence.read`/`evidence_request.authorise` already express who may supply, approve and read. A new action would widen the matrix without a new authority.

Official sources and version/date checked (2026-11-05):

- This repository: PROJECT_SCOPE.md, REQUIREMENTS.md, ADR-003, ADR-007, ADR-011, ADR-012, ADR-016, ADR-026, `identity/policy.py`, `integration/api/openapi.json` (`ErrorCode`, `EvidenceRef`).
- FtM `Payment` as recorded in `integration/followthemoney/ftm_schema_subset.v1.json`, generated from the locked commit `c8c457a` (4.11.1). No schema was added.
- Python `csv` module documentation (https://docs.python.org/3/library/csv.html), for strict parsing.
- OWASP CSV Injection (https://owasp.org/www-community/attacks/CSV_Injection), for the formula-prefix refusal.
- No RSL legal source, bank, mobile-money operator, payment network or wallet provider was consulted, and none is claimed. The bases and retrieval methods are prototype vocabulary that needs subject-matter review before real use.

Verification performed:

- `tests/integration/test_payment_channels.py`: 33 tests, all passing. They run in `scripts/dev.py check` through `tests/integration` discovery. They cover:
  - pinned fixture hashes and the four adapter contracts;
  - no network or process imports;
  - role sets equal to policy;
  - permission and provenance on every record;
  - every refusal reason above;
  - malformed files: floats, sub-cent, negative and exponent amounts, dates, headers, ragged rows, duplicate references, non-UTF-8, formula cells;
  - supplied-rate-only conversion and the virtual-asset gap;
  - labels refused on input and absent from every output, including the TAX-074 graph;
  - the same receipt recorded as transfer, cash deposit, cheque, card, mobile cash-in, p2p, merchant payment, wallet fiat payout, valued virtual asset and same-currency foreign transfer, all with no contradiction, gap or judgement field, and records that differ only in descriptive details;
  - replay, conflict and correction versioning;
  - order-independent output;
  - bounded paging;
  - tenant, case and role isolation;
  - FtM mapping with provenance and contradictions, and no joining on names.
- Mutations each made tests fail, and the code was restored afterwards. The mutations were: skipping the scope checks, skipping expiry, adding a method-derived `signal` field, skipping the read-role check, dropping the basis check, and skipping the SHA-256 check.
- Not performed: any live connector, any OpenAleph ingest of these Payments, or any Vati API route.

Consequences and revisit trigger:

- A Vati route (for example `POST /cases/{case_id}/payment-channel-files` and a list route) and persistence beside `Vati Evidence` are follow-up work. The tenant must come from the session; the route checks `evidence.register` / `evidence.read`.
- TAX-075 may read these Payments through the TAX-074 graph. Any pattern it surfaces goes to officer review.
- Revisit when any of these happens:
  - a qualified reviewer confirms a statutory route;
  - a real provider format or connector is proposed;
  - real data replaces synthetic fixtures;
  - the false-refusal cost of the labelling-word rule is observed;
  - FtM is bumped.
